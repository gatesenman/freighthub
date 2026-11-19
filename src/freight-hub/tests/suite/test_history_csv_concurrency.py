"""历史记录 / CSV 导出、多平台并发与部分失败、只读安全（不下单/不重置密钥/不改密码）。"""
import csv
import io
import json
import re
import threading
import time
from pathlib import Path

import pytest
import requests

from app.adapters.base import AdapterError
from tests.suite.conftest import FAKE, OIS_ITEM, Resp, ois_ok_routes
from tests.suite.test_adapters_ois import ois_setup
from tests.suite.test_adapters_shunfeng_xinhang import AES, AK, kt_html, kt_route, sfg_routes, sfg_setup
from tests.suite.test_adapters_yuntu_yanwen import YW_OK, yt_api, yt_ok, yw_setup

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def parse_csv(resp):
    return list(csv.reader(io.StringIO(resp.content.decode("utf-8-sig"))))


# ---------------- CSV ----------------
def test_history_csv_structure_bom_filename(client, net, setacct):
    ois_setup(net, setacct)
    hid = client.post("/api/quote", json={"country": "US", "weight": 1.5, "platforms": ["yutong"]}).json()["id"]
    r = client.get(f"/api/history/{hid}/csv")
    assert r.content.startswith(b"\xef\xbb\xbf") and f"quote_US_1.5kg_{hid}.csv" in r.headers["content-disposition"]
    rows = parse_csv(r)
    assert rows[0] == ["平台", "渠道", "分组", "总价", "币种", "时效(天)", "轨迹", "备注"]
    assert rows[1][1] == "美国专线小包" and rows[1][3] == "123.45" and rows[1][4] == "CNY" and rows[1][5] == "5-8"


def test_history_csv_quotes_commas_newlines_and_formula_injection(client, net, setacct):
    ois_setup(net, setacct, items=[dict(OIS_ITEM, name='=HYPERLINK("http://evil","x")', remark='a,b\n"c"'),
                                   dict(OIS_ITEM, name="+SUM(A1)", code="P2"), dict(OIS_ITEM, name="@cmd", code="P3"),
                                   dict(OIS_ITEM, name="-2+3", code="P4"), dict(OIS_ITEM, name="正常，渠道", code="P5")])
    hid = client.post("/api/quote", json={"country": "US", "weight": 1, "platforms": ["yutong"]}).json()["id"]
    rows = parse_csv(client.get(f"/api/history/{hid}/csv"))
    chans = [r[1] for r in rows[1:]]
    assert all(not c.startswith(("=", "+", "-", "@")) for c in chans), chans
    assert "正常，渠道" in chans and len(rows) == 6
    assert all(len(r) == 8 for r in rows)


def test_history_csv_empty_result(client, net, setacct):
    ois_setup(net, setacct, items=[])
    hid = client.post("/api/quote", json={"country": "US", "weight": 1, "platforms": ["yutong"]}).json()["id"]
    assert len(parse_csv(client.get(f"/api/history/{hid}/csv"))) == 1


def test_history_limit_and_order(store):
    for i in range(210):
        store.add_history({"country": "US", "weight": i}, [], [{"total": i + 1}])
    lst = store.list_history()
    assert len(lst) == 200 and lst[0]["query"]["weight"] == 209


def test_history_min_total_none_when_no_rows(store):
    store.add_history({"country": "US", "weight": 1}, [], [])
    assert store.list_history()[0]["min_total"] is None


def test_history_unicode_roundtrip(store):
    hid = store.add_history({"country": "日本", "weight": 1}, [{"name": "云途"}], [{"total": 1, "channel": "🚀渠道"}])
    assert store.get_history(hid)["rows"][0]["channel"] == "🚀渠道"


def test_history_concurrent_writes(store):
    ids = []
    ts = [threading.Thread(target=lambda: ids.append(store.add_history({"c": 1}, [], []))) for _ in range(30)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(set(ids)) == 30 and len(store.list_history()) == 30


# ---------------- 并发 / 部分失败 ----------------
def setup_all(net, setacct):
    ois_setup(net, setacct, "yutong")
    setacct("zhonghang", auth_mode="api", app_key="k", app_secret=FAKE["app_secret"])
    setacct("huanyang", auth_mode="api", app_key="k", app_secret=FAKE["app_secret"])
    yw_setup(setacct)
    net.route("open.yw56.com.cn", YW_OK)
    yt_api(setacct)
    yt_ok(net)
    sfg_setup(setacct)
    sfg_routes(net)
    kt_route(net, kt_html([["欣航渠道", "1", "77", "人民币", "6"]]))


def test_all_platforms_success_merged_and_sorted(client, net, setacct):
    setup_all(net, setacct)
    j = client.post("/api/quote", json={"country": "US", "weight": 2}).json()
    st = {p["id"]: p for p in j["platforms"]}
    assert all(st[i]["state"] == "ok" for i in ("yutong", "zhonghang", "huanyang", "yanwen", "yuntu", "shunfeng", "xinhang")), {k: v["state"] for k, v in st.items()}
    totals = [r["total"] for r in j["rows"]]
    assert totals == sorted(totals) and {r["platform"] for r in j["rows"]} == set(st)
    assert sum(p["count"] for p in j["platforms"]) == len(j["rows"])


def test_partial_failures_do_not_poison_other_platforms(client, net, setacct):
    setup_all(net, setacct)
    net.routes = [r for r in net.routes if "open.yw56" not in r["frag"] and "WebPrice" not in r["frag"] and "/oauth2/token" not in r["frag"]]
    net.route("open.yw56.com.cn", requests.ReadTimeout("t"))
    net.route("WebPrice", Resp(None, 500, text="err"))
    net.route("/oauth2/token", Resp({"message": "bad"}, 401))
    j = client.post("/api/quote", json={"country": "US", "weight": 2}).json()
    st = {p["id"]: p["state"] for p in j["platforms"]}
    assert st["yanwen"] == "error" and st["xinhang"] == "error" and st["yuntu"] == "login_failed"
    assert st["yutong"] == "ok" and st["shunfeng"] == "ok" and j["rows"], "成功的平台结果必须保留"
    assert len(client.get("/api/history").json()) == 1


def test_platform_exception_isolated(client, net, setacct, monkeypatch, reg):
    setup_all(net, setacct)
    monkeypatch.setattr(reg["zhonghang"].__class__, "api_quote", lambda self, q: (_ for _ in ()).throw(KeyError("boom")))
    j = client.post("/api/quote", json={"country": "US", "weight": 2}).json()
    assert {p["id"]: p["state"] for p in j["platforms"]}["zhonghang"] == "error" and any(r["platform"] == "yutong" for r in j["rows"])


def test_platforms_run_in_parallel_not_serially(client, net, setacct):
    ois_setup(net, setacct, "yutong")
    setacct("zhonghang", auth_mode="api", app_key="k", app_secret="s")
    setacct("huanyang", auth_mode="api", app_key="k", app_secret="s")
    delay = 0.6

    def slow(call):
        time.sleep(delay)
        return Resp({"body": {"ack": "true", "token": "T"}})
    net.reset_routes()
    net.route("getAuth", slow)
    net.route("getProductList", {"result_code": 0, "body": []})
    net.route("queryQuoteList", {"result_code": 0, "body": [OIS_ITEM]})
    t0 = time.time()
    client.post("/api/quote", json={"country": "US", "weight": 1, "platforms": ["yutong", "zhonghang", "huanyang"]})
    assert time.time() - t0 < delay * 2.2, "三个平台应并发查询，而不是串行"


def test_platform_timeout_reported_as_error(client, net, setacct, monkeypatch):
    from app import main as m
    ois_setup(net, setacct)
    monkeypatch.setattr(m, "PLATFORM_TIMEOUT", 0.2)
    net.reset_routes()
    net.route("getAuth", lambda c: (time.sleep(1.0), Resp({"body": {"ack": "true", "token": "T"}}))[1])
    j = client.post("/api/quote", json={"country": "US", "weight": 1, "platforms": ["yutong"]}).json()
    assert j["platforms"][0]["state"] == "error" and "超时" in j["platforms"][0]["message"]


def test_concurrent_requests_share_nothing_bad(client, net, setacct):
    ois_setup(net, setacct)
    res = []
    ts = [threading.Thread(target=lambda: res.append(client.post("/api/quote", json={"country": "US", "weight": 1, "platforms": ["yutong"]}).status_code)) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert res == [200] * 8


def test_no_account_pending_and_stopped_states(client, setacct):
    setacct("xinhang", enabled=True)
    j = client.post("/api/quote", json={"country": "US", "weight": 1, "platforms": ["yuntu", "yanwen", "shunfeng", "yutong"]}).json()
    assert {p["id"]: p["state"] for p in j["platforms"]}["yuntu"] == "no_account"


# ---------------- 只读安全 ----------------
# 允许访问的接口路径（正则）。凡是不在表里的路径一律视为危险：下单、重置/重新生成密钥、改密码、删除/取消……都不在其中。
ALLOWED = [
    r"^(/pos-web)?/ois/order/getAuth$", r"^(/pos-web)?/ois/order/getProductList$", r"^(/pos-web)?/tms/expose/queryQuoteList$",           # Intelink OIS：鉴权 / 产品 / 运价试算
    r"^/openapi/oauth2/token$", r"^/v1/basic-data/countries/getlist$", r"^/v1/price-trial/get$",          # 云途
    r"^/api/order$",                                                                                        # 燕文（method=calc.list / common.country.getlist 在 query 里）
    r"^/openapi/api/token$", r"^/openapi/api/dispatch$", r"^/oauth2/accessToken$", r"^/std/service$",   # 顺丰
    r"^/api/Home/FreightTrial$", r"^/price\.jsp$", r"^/WebPrice$",                                         # 云途登录态询价 / 欣航公开页
]
ALLOWED_YW_METHODS = {"calc.list", "common.country.getlist"}
ALLOWED_SF_MSGTYPES = {"IPE_QUERY_PRODUCT_LIST", "IUOP_QUERY_FREIGHT"}
ALLOWED_SF_SERVICES = {"COM_RECE_IUOP_QUERY_FREIGHT"}
FORBIDDEN_WORDS = re.compile(r"(?i)(create|submit|cancel|delete|remove|reset|regenerate|refresh[_-]?key|password|passwd|pwd/update|changepwd|modify|update|pay(ment)?|recharge|withdraw|place[_-]?order|insert|save[_-]?order)")


def test_readonly_guard_all_adapters_only_hit_allowlisted_readonly_endpoints(client, net, setacct):
    from urllib.parse import urlparse, parse_qs
    setup_all(net, setacct)
    setacct("zhonghang", auth_mode="login", username="U", password="P")   # V5 登录模式也过一遍
    net.route("tms-saas", Resp({"x": 1}))
    for pid in ("yutong", "zhonghang", "huanyang", "yanwen", "yuntu", "shunfeng", "xinhang"):
        client.post(f"/api/platforms/{pid}/test")
    client.post("/api/quote", json={"country": "US", "weight": 2, "postcode": "90001", "length": 1, "width": 2, "height": 3})
    # 丰桥备选路径
    setacct("shunfeng", api_kind="fengqiao", partner_id="P", check_word="C", customer_code="C1", products="INT1")
    net.route("/oauth2/accessToken", {"apiResultCode": "A1000", "apiResultData": json.dumps({"accessToken": "T", "expiresIn": 7200})})
    net.route("/std/service", {"apiResultCode": "A1000", "apiResultData": json.dumps({"success": True, "msgData": {"totalFreight": "5"}})})
    client.post("/api/platforms/shunfeng/test")
    client.post("/api/quote", json={"country": "US", "weight": 2, "platforms": ["shunfeng"]})
    assert net.calls
    bad = []
    for c in net.calls:
        u = urlparse(c["url"])
        if c["method"] not in ("GET", "POST"):
            bad.append((c["method"], u.path))
            continue
        if u.netloc.startswith("tms-saas") or "tms-saas" in u.netloc or u.path.startswith("/api/v5") or "login" in u.path.lower():
            continue   # V5 登录模式（中航 / 环洋）：登录 + 运价试算，下面单独断言
        if not any(re.match(p, u.path) for p in ALLOWED):
            bad.append((c["method"], u.path))
        if u.path == "/api/order":
            assert parse_qs(u.query)["method"][0] in ALLOWED_YW_METHODS
        if u.path == "/openapi/api/dispatch":
            assert c["kw"]["headers"]["msgType"] in ALLOWED_SF_MSGTYPES
        if u.path == "/std/service":
            assert c["kw"]["data"]["serviceCode"] in ALLOWED_SF_SERVICES
    assert not bad, f"出现了白名单之外的请求：{bad}"


def test_readonly_guard_v5_login_mode_endpoints_whitelisted(net, setacct, reg):
    # V5：只允许 登录 / 询价 相关路径
    src = (ROOT / "app/adapters/v5.py").read_text(encoding="utf-8")
    paths = set(re.findall(r'["\'](/[A-Za-z0-9_/\-\.]+)["\']', src))
    for p in paths:
        assert not FORBIDDEN_WORDS.search(p), f"V5 适配器出现疑似写操作路径：{p}"


def test_static_scan_no_write_endpoints_in_adapter_sources():
    """静态扫描：适配器代码里出现的所有接口路径 / msgType / serviceCode 都不能是写操作。"""
    hits = []
    for f in (ROOT / "app" / "adapters").glob("*.py"):
        src = f.read_text(encoding="utf-8")
        for p in re.findall(r'["\'](/[A-Za-z0-9_/\-\.]{3,})["\']', src):
            if p.startswith(("/static", "/home")):
                continue
            if FORBIDDEN_WORDS.search(p):
                hits.append((f.name, p))
        for m in re.findall(r'["\']((?:IUOP|IPE|GTS|COM_RECE)_[A-Z_]+)["\']', src):
            if not re.search(r"QUERY|TRACK", m):
                hits.append((f.name, m))
    assert not hits, hits


def test_frontend_only_calls_known_local_api_routes():
    js = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    from app import main as m
    routes = {r.path for r in m.app.routes}
    routes |= set(re.findall(r'@app\.\w+\("(/[^"]*)"', (ROOT / "app/server.py").read_text(encoding="utf-8")))   # 服务器模式才注册的路由
    calls = set(re.findall(r'api\(\s*[`"\'](/api/[A-Za-z0-9_/{}$.\-]*)', js)) | set(re.findall(r'fetch\(\s*[`"\'](/api/[A-Za-z0-9_/{}$.\-]*)', js))
    assert calls, "未解析到前端接口调用"
    norm = lambda p: re.sub(r"\$\{[^}]*\}", "{x}", p).rstrip("/")
    route_norm = {re.sub(r"\{[^}]*\}", "{x}", r).rstrip("/") for r in routes}
    for c in calls:
        c = norm(c)
        assert any(c == r or r.startswith(c) for r in route_norm), f"前端调用了不存在的路由 {c}"
    assert not re.search(r"https?://", js.replace("http://www.w3.org", "")), "前端不应直连任何外部地址"


def test_app_exposes_no_order_or_key_management_routes():
    from app import main as m
    for r in m.app.routes:
        p = getattr(r, "path", "")
        if p.startswith("/api"):
            assert not re.search(r"(?i)order|reset|regenerate|password|pwd|ship|pay", p.replace("/api/platforms", "")), p
            methods = getattr(r, "methods", set())
            if "PUT" in methods or "DELETE" in methods:
                assert p.startswith(("/api/platforms", "/api/history", "/api/shell/settings")), f"意外的写路由 {p}"


def test_no_credentials_in_source_tree():
    """代码 / 文档 / 安装脚本里不能出现真实凭据：用 config.json 里存在的密钥值反查（只比较，不打印）。"""
    cfgp = ROOT / "data" / "config.json"
    secrets = []
    if cfgp.exists():
        cfg = json.loads(cfgp.read_text(encoding="utf-8"))
        for acc in cfg.get("accounts", {}).values():
            for k in ("password", "app_secret", "api_token", "aes_key", "check_word", "access_token"):
                v = acc.get(k)
                if isinstance(v, str) and len(v) >= 6:
                    secrets.append(v)
    scan = [p for p in ROOT.rglob("*") if p.is_file() and p.suffix in (".py", ".js", ".html", ".css", ".md", ".nsi", ".bat", ".sh", ".txt", ".json", ".vbs", ".ini")
            and "data" not in p.relative_to(ROOT).parts[:1] and "__pycache__" not in p.parts and "screenshots" not in p.parts[-3:-1]]
    leaks = [p.name for p in scan for s in secrets if s in p.read_text(encoding="utf-8", errors="ignore")]
    assert not leaks, f"疑似凭据出现在文件里：{sorted(set(leaks))}"
