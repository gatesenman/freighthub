"""开放平台 API 模式测试（独立数据目录，不碰真实账号）。
- 签名算法单元测试（燕文官方文档示例逐字节对照；云途/顺丰按文档/SDK 算法交叉计算）
- 各平台适配器用 mock 的 HTTP 层验证请求构造与响应解析
- 对公开接口用假凭据做在线探测，确认端点可达且错误被正确映射为“鉴权失败”
- 后端接口/授权模式切换/密钥不回显
输出 PASS/FAIL 行，并写入 tests/api_mode_result.json。"""
import base64
import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tmp = tempfile.mkdtemp(prefix="fh_api_")
os.environ["FREIGHT_HUB_DATA"] = tmp
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app import main as m  # noqa: E402
from app.adapters import openapi as oa  # noqa: E402
from app.adapters.base import AdapterError, Query  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append({"name": name, "pass": bool(cond), "detail": detail})
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


class Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status
        self.text = json.dumps(data)

    def json(self):
        return self._d


class FakeNet:
    """记录请求并按 URL 返回预置响应。"""
    def __init__(self):
        self.calls, self.routes = [], []

    def route(self, frag, data, status=200):
        self.routes.append((frag, data, status))

    def __call__(self, method, url, **kw):
        self.calls.append({"method": method, "url": url, **kw})
        for rt in self.routes:
            frag, data, status = (rt + (200,))[:3] if len(rt) == 2 else rt
            if frag in url:
                return Resp(data() if callable(data) else data, status)
        return Resp({"error": "no route"}, 404)


def with_net(net):
    oa.requests.request = net


REAL_REQUEST = oa.requests.request
c = TestClient(m.app)


def put(pid, values=None, mode=None, enabled=None):
    body = {"values": values or {}}
    if mode:
        body["auth_mode"] = mode
    if enabled is not None:
        body["enabled"] = enabled
    return c.put(f"/api/platforms/{pid}/account", json=body)


def platform(pid):
    return next(p for p in c.get("/api/platforms").json() if p["id"] == pid)


def run():
    # ============ 1. 签名算法 ============
    tok, uid = "D6140AA383FD8515B09028C586493DDB", "100000"
    check("燕文 MD5 签名 = 官方文档示例 bc5f1792…", oa.yw_sign(tok, uid, "{}", "common.country.getlist", "1655798809414") == "bc5f1792e30936c72fa2a35efead8d73")
    exp = base64.b64encode(hmac.new(b"sec", b"body={\"a\":1}&date=1700000000000&method=POST&uri=/v1/x", hashlib.sha256).digest()).decode()
    check("云途 HMAC-SHA256（含 body）与 SDK 规则一致", oa.yt_sign("sec", "1700000000000", "POST", "/v1/x", '{"a":1}') == exp)
    exp = base64.b64encode(hmac.new(b"sec", b"date=1700000000000&method=GET&uri=/v1/price-trial/get", hashlib.sha256).digest()).decode()
    check("云途 HMAC-SHA256（GET 无 body）", oa.yt_sign("sec", "1700000000000", "GET", "/v1/price-trial/get") == exp)
    check("顺丰 URLEncode 与 Java URLEncoder 一致（空格→+，~→%7E，* 保留，中文 UTF-8）",
          oa._java_urlencode("a b*~中-_.+&") == "a+b*%7E%E4%B8%AD-_.%2B%26")
    md, ts, cw = '{"orderId":"A B+1"}', "1708235379205", "CHECKWORD"
    raw = "%7B%22orderId%22%3A%22A+B%2B1%22%7D1708235379205CHECKWORD"
    check("顺丰 msgDigest = Base64(MD5(URLEncode(msgData+timestamp+checkWord)))",
          oa.sf_digest(md, ts, cw) == base64.b64encode(hashlib.md5(raw.encode()).digest()).decode(), oa.sf_digest(md, ts, cw))

    # ============ 2. 后端：授权模式 ============
    pls = c.get("/api/platforms").json()
    check("7 个平台都有授权模式定义（欣航为单一公开模式）", len(pls) == 7 and all({"login", "api"} <= set(p["modes"]) for p in pls))
    xh = [p for p in pls if p["id"] == "xinhang"][0]
    check("欣航：单一模式「公开网站，无需授权」，无账号/API 字段", xh["fixed_mode"] and xh["no_auth"] and xh["auth_mode_label"] == "公开网站，无需授权"
          and not xh["modes"]["login"]["fields"] and not xh["modes"]["api"]["fields"], xh["auth_mode_label"])
    check("默认授权模式为登录账号授权", all(p["auth_mode"] == "login" for p in pls))
    check("欣航：未配置也可用（不是待配置账号）", xh["state"] != "no_account" and xh["has_account"] is True, xh["state"])
    check("欣航：尝试切到 API 模式被忽略", put("xinhang", mode="api").json()["auth_mode"] == "login")
    docs = {p["id"]: p["modes"]["api"]["doc_status"] for p in pls}
    check("有公开文档的平台标注 public，其余标注需申请",
          docs == {"yuntu": "public", "yanwen": "public", "shunfeng": "public", "xinhang": "apply",
                   "zhonghang": "public", "huanyang": "public", "yutong": "public"}, str(docs))
    check("每个平台 API 模式都有字段/文档说明", all(p["modes"]["api"]["fields"] and p["modes"]["api"]["doc_note"] for p in pls if not p["fixed_mode"]))
    r = put("yuntu", {"app_id": "APPID1", "app_secret": "SECRET1", "env": "sandbox", "username": "u@x.com", "password": "pw"}, mode="api")
    yt = r.json()
    check("切换到 API 模式并保存字段", yt["auth_mode"] == "api" and yt["has_account"] is True)
    check("API 模式下 secret 字段不回显明文", all(not f["value"] for f in yt["modes"]["api"]["fields"] if f["secret"]) and "SECRET1" not in json.dumps(yt))
    check("API 模式字段值（非密钥）回显", {f["key"]: f["value"] for f in yt["modes"]["api"]["fields"]}["app_id"] == "APPID1")
    yt2 = put("yuntu", mode="login").json()
    check("切回登录模式后登录字段仍在（两套凭据互不覆盖）",
          yt2["auth_mode"] == "login" and {f["key"]: f["value"] for f in yt2["modes"]["login"]["fields"]}["username"] == "u@x.com"
          and {f["key"]: f["saved"] for f in yt2["modes"]["api"]["fields"]}["app_secret"])
    check("非法授权模式被拒绝(400)", put("yuntu", mode="oauth").status_code == 400)
    check("未知字段被忽略", "evil" not in json.dumps(put("yuntu", {"evil": "x"}).json()))
    check("API 模式未配置时状态为待配置账号", platform("yanwen")["state"] in ("pending", "no_account"))
    put("yanwen", mode="api")
    check("燕文 API 模式未填凭据 → 待配置账号", platform("yanwen")["state"] == "no_account")
    r = c.post("/api/platforms/yanwen/test").json()
    check("API 测试连接：未填凭据返回 no_account", r["ok"] is False and r["kind"] == "no_account", r["message"])

    # ============ 3. 适配器：mock HTTP ============
    net = FakeNet()
    with_net(net)
    # --- 云途
    put("yuntu", {"app_id": "APPID1", "app_secret": "SECRET1", "env": "prod"}, mode="api")
    net.route("/openapi/oauth2/token", {"accessToken": "TKN-123", "expiresIn": 7200})
    net.route("/v1/price-trial/get", {"success": True, "result": [
        {"product_code": "BKZXR", "product_name": "云途全球专线挂号（标快）", "fee_name": "E1", "calculate_amount": 8,
         "currency": "RMB", "interval_day": "3-8", "price_name": "A价", "convert_currency": "RMB", "convert_amount": 8.5},
        {"product_code": "X2", "product_name": "USD渠道", "calculate_amount": 3, "currency": "USD", "convert_currency": "USD",
         "convert_amount": 3, "interval_day": "7-12"}]})
    net.route("/v1/basic-data/countries/getlist", {"success": True, "result": [{"code": "US"}, {"code": "GB"}]})
    a = m.registry["yuntu"]
    rows = a.quote(Query(country="US", weight=1.5, postcode="10001", length=10, width=10, height=10))
    tokc = [x for x in net.calls if "oauth2/token" in x["url"]][0]
    check("云途 API：先 OAuth2 client_credentials 换 token", tokc["json"]["grantType"] == "client_credentials" and tokc["json"]["appId"] == "APPID1" and tokc["json"]["appSecret"] == "SECRET1")
    qc = [x for x in net.calls if "price-trial" in x["url"]][0]
    h = qc["headers"]
    check("云途 API：请求头 token/date/sign 且签名可复算", h["token"] == "TKN-123" and h["sign"] == oa.yt_sign("SECRET1", h["date"], "GET", "/v1/price-trial/get"), h["sign"][:16])
    check("云途 API：查询参数含国家/重量/邮编/尺寸", all(k in qc["url"] for k in ("country_code=US", "weight=1.5", "postal_code=10001", "length=10")))
    check("云途 API：运价解析（RMB 折算价、时效、渠道名）", len(rows) == 2 and rows[0]["total"] == 8.5 and rows[0]["days"] == "3-8" and "标快" in rows[0]["channel"], f"{rows[0]['total']}")
    n_tok = sum(1 for x in net.calls if "oauth2/token" in x["url"])
    a.quote(Query(country="GB", weight=1))
    check("云途 API：token 缓存复用（不重复换取）", sum(1 for x in net.calls if "oauth2/token" in x["url"]) == n_tok)
    check("云途 API：测试连接", "成功" in a.test_login())
    net.routes = [("/openapi/oauth2/token", {"message": "wrong"}, 412)]
    a.store.clear_session("yuntu_api")
    try:
        a.api_test()
        check("云途 API：appId/secret 错误 → 鉴权失败", False)
    except AdapterError as e:
        check("云途 API：appId/secret 错误 → 鉴权失败", e.kind == "login_failed", str(e)[:60])
    net.routes = [("/openapi/oauth2/token", {"accessToken": "T2", "expiresIn": 7200}), ("/v1/price-trial/get", {"msg": "bad"}, 401)]
    a.store.clear_session("yuntu_api")
    try:
        a.quote(Query(country="US", weight=1))
        check("云途 API：401 重试后报鉴权失败", False)
    except AdapterError as e:
        check("云途 API：401 重试后报鉴权失败", e.kind == "login_failed")

    # --- 燕文
    net.calls.clear()
    put("yanwen", {"user_id": "100000", "api_token": tok, "env": "sandbox"}, mode="api")
    net.routes = [("calc.list", {"success": True, "code": "200", "data": [
        {"productName": "燕文专线追踪小包", "rmbTotalMoney": 55.5, "basicFreight": 40, "fuelCost": 5, "referAging": "7-15"}]}),
        ("common.country.getlist", {"success": True, "data": [{"id": "US"}]})]
    y = m.registry["yanwen"]
    rows = y.quote(Query(country="US", weight=0.5))
    u = net.calls[0]["url"]
    check("燕文 API：沙箱域名/公共参数/签名", u.startswith(oa.YW_SANDBOX) and "user_id=100000" in u and "method=calc.list" in u and "sign=" in u, u[:70])
    check("燕文 API：运价解析", rows[0]["total"] == 55.5 and rows[0]["days"] == "7-15" and any(f["label"] == "燃油费" for f in rows[0]["fees"]))
    check("燕文 API：测试连接", "沙箱" in y.test_login())
    net.routes = [("calc.list", {"success": True, "code": "0", "message": "操作成功", "data": [{"productName": "P0", "rmbTotalMoney": 9}]})]
    check("燕文 API：文档成功码 code=0 也被接受", y.quote(Query(country="US", weight=1))[0]["total"] == 9)
    net.routes = [("calc.list", {"success": False, "code": "500", "message": "英国邮编不能为空", "data": None})]
    try:
        y.quote(Query(country="GB", weight=1))
        check("燕文 API：业务失败信息透传（如 GB 邮编必填）", False)
    except AdapterError as e:
        check("燕文 API：业务失败信息透传（如 GB 邮编必填）", "邮编" in str(e))

    # --- 顺丰
    net.calls.clear()
    put("shunfeng", {"partner_id": "PID", "check_word": "CW", "customer_code": "CC", "products": "INT0007, INT0008", "env": "sandbox", "auth": "oauth2", "api_kind": "fengqiao"}, mode="api")
    net.route("/oauth2/accessToken", {"apiResultCode": "A1000", "apiResultData": json.dumps({"accessToken": "SFTOK", "expiresIn": 7200})})
    net.route("/std/service", lambda: {"apiResultCode": "A1000", "apiResultData": json.dumps(
        {"success": True, "errorCode": "0", "errorMsg": None, "msgData": {"totalFreight": "125", "weight": "12.5", "currency": "CNY"}})})
    s = m.registry["shunfeng"]
    rows = s.quote(Query(country="SG", weight=12.01, postcode="499938"))
    svc = [x for x in net.calls if "/std/service" in x["url"]]
    check("顺丰 API：沙箱域名 + OAuth2 accessToken + 表单字段", len(svc) == 2 and svc[0]["url"].startswith(oa.SF_HOSTS["sandbox"]) and svc[0]["data"]["accessToken"] == "SFTOK"
          and svc[0]["data"]["serviceCode"] == "COM_RECE_IUOP_QUERY_FREIGHT" and svc[0]["data"]["partnerID"] == "PID")
    md = json.loads(svc[0]["data"]["msgData"])
    check("顺丰 API：msgData 含 customerCode/产品码/收寄国家/邮编/重量", md["customerCode"] == "CC" and md["interProductCode"] == "INT0007" and md["receiverCountry"] == "SG"
          and md["receiverPostCode"] == "499938" and md["weight"] == "12.01" and md["senderCountry"] == "CN")
    check("顺丰 API：逐个产品试算并解析", len(rows) == 2 and rows[0]["total"] == 125 and rows[0]["currency"] == "CNY", rows[0]["channel"])
    put("shunfeng", {"auth": "digest"})
    net.calls.clear()
    s.quote(Query(country="SG", weight=1))
    d = [x for x in net.calls if "/std/service" in x["url"]][0]["data"]
    check("顺丰 API：数字签名模式带 msgDigest 且不带 accessToken",
          "accessToken" not in d and d["msgDigest"] == oa.sf_digest(d["msgData"], d["timestamp"], "CW"))
    net.routes = [("/std/service", {"apiResultCode": "A1006", "apiErrorMsg": "数字签名无效"})]
    try:
        s.quote(Query(country="SG", weight=1))
        check("顺丰 API：A1006 映射为鉴权失败", False)
    except AdapterError as e:
        check("顺丰 API：A1006 映射为鉴权失败", e.kind == "login_failed", str(e))
    check("顺丰 登录模式提示暂未实现", platform("shunfeng")["auth_mode"] == "api" and put("shunfeng", mode="login").json()["state"] == "pending")

    # --- 欣航：只有公开网站
    net.calls.clear()
    x = m.registry["xinhang"]
    check("欣航：无 API 实现、无 K5 字段", not x.api_impl and not x.api_fields and not x.login_fields and not x.needs_account)
    check("欣航：status 单一模式 + 文案", x.status()["auth_mode_label"] == "公开网站，无需授权" and x.status()["fixed_mode"])

    # --- sf.global（顺丰国际开放平台）：AES + 签名
    key = "nodMcfCf7OwV876KdoTIhDN9fHvGi2uvRh9C+QWi4vg"      # 随机示例值，非真实密钥
    ak = "AK-UNIT-TEST"
    enc = oa.sfg_encrypt(key, ak, '{"a":"中文","b":1}', rand="ABCDEFGHIJKLMNOP")
    check("sf.global AES-256-CBC：加解密往返（含中文）", oa.sfg_decrypt(key, ak, enc) == '{"a":"中文","b":1}')
    check("sf.global AES：随机串不同 → 密文不同但都可解", oa.sfg_encrypt(key, ak, "x") != oa.sfg_encrypt(key, ak, "x") and oa.sfg_decrypt(key, ak, oa.sfg_encrypt(key, ak, "x")) == "x")
    try:
        oa.sfg_decrypt(key, "OTHER", enc)
        check("sf.global AES：appKey 不符 → 解密校验失败", False)
    except AdapterError as e:
        check("sf.global AES：appKey 不符 → 解密校验失败", e.kind == "login_failed")
    check("sf.global 纯 Python AES = FIPS-197 向量", __import__("app.adapters.aes", fromlist=["x"]).cbc_encrypt(
        bytes.fromhex("603deb1015ca71be2b73aef0857d77811f352c073b6108d72d9810a30914dff4"), bytes.fromhex("000102030405060708090a0b0c0d0e0f"),
        bytes.fromhex("6bc1bee22e409f96e93d7e117393172a")).hex() == "f58c4c04d6e5f1ba779eabfb5f7bfbd6")
    check("sf.global 签名 = SHA256(字典序拼接 token/timestamp/nonce/encrypt)",
          oa.sfg_signature("T", "2", "N", "E") == hashlib.sha256(b"2ENT".replace(b"2ENT", "".join(sorted(["T", "2", "N", "E"])).encode())).hexdigest())

    def sfg_net(method, url, **kw):
        net.calls.append({"method": method, "url": url, **kw})
        if "/openapi/api/token" in url:
            return Resp({"apiResultCode": "0", "apiResultData": {"accessToken": "SFGTOK", "expireIn": 7200}})
        h = kw["headers"]
        req = json.loads(oa.sfg_decrypt(key, ak, kw["data"].decode()))
        if h["msgType"] == "IPE_QUERY_PRODUCT_LIST":
            body = {"success": True, "data": [{"interProductCode": "INT0007", "displayProductName": "国际标快"}, {"interProductCode": "INT0008", "displayProductName": "国际特惠"}]}
        else:
            body = {"success": True, "data": {"discountedRate": 100 + len(req["interProductCode"]), "currency": "CNY", "weight": req["weight"]}}
        return Resp({"apiResultCode": "0", "apiResultData": oa.sfg_encrypt(key, ak, json.dumps(body))})
    oa.requests.request = sfg_net
    net.calls.clear()
    put("shunfeng", {"app_key": ak, "app_secret": "SEC", "aes_key": key, "customer_code": "CC1", "env": "prod", "api_kind": "sfglobal"}, mode="api")
    s = m.registry["shunfeng"]
    check("顺丰：API 模式文档指向 sf.global 开放平台", "sf.global" in s.status()["modes"]["api"]["doc_url"], s.status()["modes"]["api"]["doc_url"])
    msg = s.test_login()
    check("顺丰 sf.global：测试连接（token + AES 产品查询）", "授权成功" in msg and "2 个产品" in msg, msg)
    tk = [c_ for c_ in net.calls if "/api/token" in c_["url"]][0]
    check("顺丰 sf.global：token 为 GET + appKey/appSecret 参数", tk["method"] == "GET" and tk["params"]["appKey"] == ak and tk["url"].startswith(oa.SFG_HOSTS["prod"]))
    rows = s.quote(Query(country="US", weight=1.5, postcode="90001"))
    dp = [c_ for c_ in net.calls if c_.get("headers", {}).get("msgType") == "IUOP_QUERY_FREIGHT"][0]
    dm = json.loads(oa.sfg_decrypt(key, ak, dp["data"].decode()))
    h = dp["headers"]
    check("顺丰 sf.global：dispatch 头（appKey/token/msgType/signature）", h["appKey"] == ak and h["token"] == "SFGTOK" and h["msgType"] == "IUOP_QUERY_FREIGHT"
          and h["signature"] == oa.sfg_signature("SFGTOK", h["timestamp"], h["nonce"], dp["data"].decode()))
    check("顺丰 sf.global：报文加密（请求体非明文）且含收寄国家/邮编/重量/客户编码",
          b"90001" not in dp["data"] and dm["receiverCountry"] == "US" and dm["receiverPostCode"] == "90001" and dm["weight"] == "1.5" and dm["customerCode"] == "CC1")
    check("顺丰 sf.global：逐产品试算解析", len(rows) == 2 and all(r["currency"] == "CNY" and r["total"] > 100 for r in rows) and "国际标快" in rows[0]["channel"], str(rows[0]["channel"]))
    put("shunfeng", {"customer_code": ""})
    try:
        s.quote(Query(country="US", weight=1))
        check("顺丰 sf.global：缺 customerCode 提示补填", False)
    except AdapterError as e:
        check("顺丰 sf.global：缺 customerCode 提示补填", e.kind == "no_account" and "customerCode" in str(e))
    net.calls.clear()

    def sfg_noperm(method, url, **kw):
        if "/openapi/api/token" in url:
            return Resp({"apiResultCode": "0", "apiResultData": {"accessToken": "SFGTOK", "expireIn": 7200}})
        return Resp({"apiResultCode": "1004", "apiErrorMsg": "没权限"})
    oa.requests.request = sfg_noperm
    try:
        s.test_login()
        check("顺丰 sf.global：1004 没权限 → 给出关联 API 的指引（manual）", False)
    except AdapterError as e:
        check("顺丰 sf.global：1004 没权限 → 给出关联 API 的指引（manual）", e.kind == "manual" and "关联 API" in str(e), str(e)[:40])
    oa.requests.request = net
    net.routes = []
    put("shunfeng", {"api_kind": "fengqiao"})
    check("顺丰：丰桥备选仍可选", s._kind() == "fengqiao")
    put("shunfeng", {"api_kind": "sfglobal"})

    # --- OIS：中航/环洋/雨桐（Intelink OIS，同一套接口）
    net.calls.clear()
    QUOTE = {"result_code": 0, "message": "ok", "body": [
        {"code": "USZ-H01", "name": "USZ-H01美国包中包6000", "standardFeeSum": "143.0", "standardCharge": "123.0", "fuelCharge": "10", "otherStandardFeeSum": "10",
         "otherStandardFeeList": [{"ardedName": "偏远费", "chargestandard": "10"}], "standardCur": "CNY", "aging": "5-8", "divsize": "6000", "zoneName": "美国", "formulaView": "143"},
        {"code": "BAD", "name": "无报价占位", "standardFeeSum": "1000000.0", "standardCur": "CNY"},
        {"code": "HKZ-01", "name": "HKZ-01中港", "standardFeeSum": "1.5", "standardCur": "CNY"},
        {"code": "USD1", "name": "美元渠道", "standardFeeSum": "20", "standardCur": "USD"}]}
    net.route("/ois/order/getAuth", {"body": {"ack": "true", "clno": "C001", "coid": "1", "hash": "h", "token": "OISTOK"}})
    net.route("/ois/order/getProductList", {"result_code": 0, "body": [{"code": "USZ-H01", "name": "x", "hubType": "CNUPS"}, {"code": "HKZ-01"}]})
    net.route("/tms/expose/queryQuoteList", QUOTE)
    for pid in ("zhonghang", "huanyang", "yutong"):
        put(pid, {"app_key": "AK", "app_secret": "AS"}, mode="api")
        ad = m.registry[pid]
        msg = ad.test_login()
        check(f"OIS API：{ad.name} getAuth + 渠道列表", "授权成功" in msg and "C001" in msg and "2 个产品" in msg, msg)
        check(f"OIS API：{ad.name} 状态=运价试算可用", ad.implemented and ad.status()["modes"]["api"]["quote_ok"] and ad.status()["modes"]["api"]["doc_status"] == "public")
    hy_calls = [x for x in net.calls if "getAuth" in x["url"]]
    check("OIS API：环洋 默认对接环境为易快通 ekttech.net/pos-web", any(x["url"].startswith("https://ekttech.net/pos-web/ois/order/getAuth") for x in hy_calls),
          ",".join(sorted({x["url"].split("/ois")[0] for x in hy_calls})))
    gc = [x for x in net.calls if "getAuth" in x["url"]][0]
    check("OIS API：getAuth 表单提交 appKey/appSecret，默认域名 openapi.intelink.net.cn",
          gc["data"] == {"appKey": "AK", "appSecret": "AS"} and gc["url"] == "https://openapi.intelink.net.cn/ois/order/getAuth" and oa.OIS_DEFAULT == "https://openapi.intelink.net.cn")
    yt_ = m.registry["yutong"]
    rows = yt_.quote(Query(country="US", weight=1, postcode="90001", length=30, width=20, height=10))
    qc = [x for x in net.calls if "queryQuoteList" in x["url"]][-1]
    body1 = json.loads(qc["data"]["body1"])
    check("OIS 运价试算：body1 含 weig/destNo/packing/reZip/pieces", body1["weig"] == 1 and body1["destNo"] == "US" and body1["packing"] == "WPX"
          and body1["reZip"] == "90001" and body1["pieces"][0]["length"] == 30, json.dumps(body1))
    hd = qc["headers"]
    pm = {"nonce": "slnkda", "timestamp": hd["token"] and json.loads(base64.b64decode(hd["token"].replace("-", "a").replace("#", "c").replace("^", "x").replace("$", "M")))["timestamp"], "token": "OISTOK", "version": "1.0", "body1": qc["data"]["body1"]}
    check("OIS 签名：MD5(Base64(排序参数)+appSecret) 大写，头 token 伪装可还原", hd["sign"] == oa.ois_sign("AS", {k: str(v) for k, v in pm.items()}) and hd["version"] == "1.0")
    check("OIS 解析：占位价 1000000 被过滤，其余 3 条", [r["channel"] for r in rows] == ["USZ-H01美国包中包6000", "HKZ-01中港", "美元渠道"], str([r["channel"] for r in rows]))
    r0 = rows[0]
    check("OIS 解析：总价/币种/时效/分组/费用明细", r0["total"] == 143 and r0["currency"] == "CNY" and r0["days"] == "5-8" and r0["group"] == "CNUPS"
          and {f["label"] for f in r0["fees"]} >= {"运费", "燃油", "偏远费"}, json.dumps(r0["fees"], ensure_ascii=False)[:80])
    check("OIS 解析：极低价（中港段）打上疑似境内标记", any("疑似" in f["value"] for f in rows[1]["fees"]))
    check("OIS 汇总查询接口可用（API 模式 → 查价而不是待申请）", c.post("/api/quote", json={"country": "US", "weight": 1, "platforms": ["yutong"]}).json()["platforms"][0]["state"] == "ok")
    # token 过期 → 自动重新授权并重试一次
    calls = {"n": 0}

    def expiring(self_=None):
        calls["n"] += 1
        return QUOTE if calls["n"] > 1 else {"result_code": 1005, "message": "token expired"}
    net.routes = [r_ for r_ in net.routes if "queryQuoteList" not in r_[0]]
    net.route("/tms/expose/queryQuoteList", expiring)
    n_auth = len([x for x in net.calls if "getAuth" in x["url"]])
    check("OIS：token 过期(1005) → 自动重新授权重试", len(yt_.quote(Query(country="US", weight=1))) == 3 and len([x for x in net.calls if "getAuth" in x["url"]]) == n_auth + 1)
    net.routes = [("/ois/order/getAuth", {"body": {"ack": "false"}, "message": "账号或秘钥错误"})]
    try:
        m.registry["huanyang"].api_test()
        check("OIS API：错误密钥 → 鉴权失败", False)
    except AdapterError as e:
        check("OIS API：错误密钥 → 鉴权失败", e.kind == "login_failed")
    net.routes = []
    for pid in ("zhonghang", "huanyang", "yutong"):
        put(pid, mode="login")
    for pid in ("yuntu", "yanwen", "shunfeng", "xinhang"):
        put(pid, mode="login")
    check("模式切换后登录模式的原有实现保留", platform("zhonghang")["implemented"] and platform("yuntu")["implemented"] and platform("xinhang")["implemented"])

    # ============ 4. 在线探测（公开接口 + 假凭据，不涉及任何真实账号）============
    oa.requests.request = REAL_REQUEST
    put("yanwen", {"user_id": uid, "api_token": tok, "env": "sandbox"}, mode="api")
    try:
        t0 = time.time()
        msg = m.registry["yanwen"].test_login()
        check("【在线】燕文官方沙箱：鉴权成功(user_id=100000)", "成功" in msg, f"{msg[:40]} {time.time()-t0:.1f}s")
        try:
            rws = m.registry["yanwen"].quote(Query(country="US", weight=0.5))
            check("【在线】燕文官方沙箱：calc.list 运价试算可调用", True, f"{len(rws)} 条")
        except AdapterError as e:
            check("【在线】燕文官方沙箱：calc.list 运价试算可调用（接口应答并被解析）", "签名" not in str(e) and "sign" not in str(e).lower(), str(e)[:60])
    except Exception as e:  # noqa
        check("【在线】燕文官方沙箱：鉴权成功(user_id=100000)", False, str(e)[:80])
    probes = [
        ("云途 prod 假凭据 → 授权失败", lambda: oa.yt_token(oa.YT_PROD, "fake", "fake")),
        ("顺丰沙箱 假凭据 → 授权失败", lambda: oa.sf_token("sandbox", "fake", "fake")),
        ("顺丰国际 sf.global 假凭据 → 授权失败", lambda: oa.sfg_token("prod", "fake", "fake")),
        ("OIS 假 appKey → 鉴权失败", lambda: oa.ois_auth(oa.OIS_DEFAULT, "fake", "fake")),
    ]
    # sf.global 官方文档公开的共享测试环境凭据（文档示例，非用户密钥）：token + AES 报文 + 国际产品查询 全链路
    SFG_PUB = ("cdd169d87a4698d314754a79f180308a", "69fb3e868a45d0bdd5b0de0ea1db6ece", "oI1YdU1cb1YC70HVjZRa3wXBLUsrIUYr5lDh0gFrMbe")
    try:
        tkn, _ttl = oa.sfg_token("sandbox", SFG_PUB[0], SFG_PUB[1])
        res = oa.sfg_call("sandbox", SFG_PUB[0], SFG_PUB[2], tkn, "IPE_QUERY_PRODUCT_LIST", {})
        check("【在线】顺丰 sf.global 沙箱（文档公开凭据）：token + AES 加解密 + 国际产品查询", bool(res.get("success")) and len(res.get("data") or []) > 0, f"{len(res.get('data') or [])} 个产品")
    except Exception as e:  # noqa
        check("【在线】顺丰 sf.global 沙箱（文档公开凭据）：token + AES 加解密 + 国际产品查询", False, f"{type(e).__name__}: {e}"[:80])
    put("xinhang", {})
    try:
        rws = m.registry["xinhang"].quote(Query(country="US", weight=1))
        check("【在线】欣航公开价页面：美国 1kg 有报价", len(rws) > 0 and all(r["total"] > 0 for r in rws), f"{len(rws)} 条，最低 {min(r['total'] for r in rws):g}")
    except Exception as e:  # noqa
        check("【在线】欣航公开价页面：美国 1kg 有报价", False, f"{type(e).__name__}: {e}"[:80])
    # 雨桐 OIS 真实只读联调：仅当开发机 data/config.json 里已有 appKey（不随安装包发布）时才跑
    dev = {}
    try:
        dev = json.loads((ROOT / "data" / "config.json").read_text(encoding="utf-8"))["accounts"]["yutong"]
    except Exception:
        pass
    if dev.get("app_key") and dev.get("app_secret"):
        put("yutong", {"app_key": dev["app_key"], "app_secret": dev["app_secret"]}, mode="api")
        yad = m.registry["yutong"]
        try:
            msg = yad.test_login()
            check("【在线·只读】雨桐 OIS：getAuth + getProductList", "授权成功" in msg and "个产品" in msg, msg[:60])
            rws = yad.quote(Query(country="US", weight=1))
            good = sorted(r["total"] for r in rws if r["total"] >= 10)
            check("【在线·只读】雨桐 OIS：美国 1kg 运价试算返回多个渠道", len(rws) >= 10 and good, f"{len(rws)} 条，最低（≥10）{good[0] if good else '-'}")
        except Exception as e:  # noqa
            check("【在线·只读】雨桐 OIS：美国 1kg 运价试算", False, f"{type(e).__name__}: {e}"[:80])
    # 环洋 OIS（易快通 pos-web）真实只读联调：同样只在开发机 config 有 appKey 时跑
    hy = {}
    try:
        hy = json.loads((ROOT / "data" / "config.json").read_text(encoding="utf-8"))["accounts"]["huanyang"]
    except Exception:
        pass
    if hy.get("app_key") and hy.get("app_secret"):
        put("huanyang", {"app_key": hy["app_key"], "app_secret": hy["app_secret"], "api_base": hy.get("api_base", "")}, mode="api")
        had = m.registry["huanyang"]
        try:
            msg = had.test_login()
            check("【在线·只读】环洋 OIS：getAuth + getProductList", "授权成功" in msg and "个产品" in msg, msg[:60])
            for cc, ww in (("US", 1), ("GB", 0.5)):
                rws = had.quote(Query(country=cc, weight=ww))
                good = sorted(r["total"] for r in rws if r["total"] >= 10)
                check(f"【在线·只读】环洋 OIS：{cc} {ww:g}kg 运价试算返回多个渠道", len(rws) >= 10 and good, f"{len(rws)} 条，最低 {good[0] if good else '-'}")
        except Exception as e:  # noqa
            check("【在线·只读】环洋 OIS 运价试算", False, f"{type(e).__name__}: {e}"[:80])
    for name, fn in probes:
        try:
            fn()
            check("【在线】" + name, False, "意外成功")
        except AdapterError as e:
            check("【在线】" + name, e.kind == "login_failed", str(e)[:70])
        except Exception as e:  # noqa
            check("【在线】" + name, False, f"{type(e).__name__}: {e}"[:80])

    ok = sum(1 for r in results if r["pass"])
    print(f"\nAPI 模式测试：{ok}/{len(results)} 通过")
    (ROOT / "tests" / "api_mode_result.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return ok == len(results)


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
