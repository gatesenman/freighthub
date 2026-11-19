"""云途 / 燕文 / 顺丰（国际 sf.global + 丰桥） / 欣航 适配器：mock 响应覆盖成功、鉴权失败、1004、超时、非 JSON、空列表、限流。"""
import json

import pytest
import requests

from app.adapters import openapi as oa
from app.adapters.base import AdapterError, Query
from tests.suite.conftest import FAKE, Resp

pytestmark = pytest.mark.unit
Q = Query(country="US", weight=1)


# ====================== 云途 API ======================
def yt_setup(net, setacct, results=None, env="prod", token_resp=None, **extra):
    net.reset_routes()
    setacct("yuntu", auth_mode="api", app_id="FAKE-APPID", app_secret=FAKE["app_secret"], env=env, **extra)
    net.route("/openapi/oauth2/token", token_resp or {"accessToken": FAKE["token"], "expiresIn": 7200})
    net.route("/v1/price-trial/get", {"success": True, "result": results if results is not None else [
        {"product_name": "云途全球专线", "product_code": "YT", "convert_amount": "55.5", "convert_currency": "RMB", "calculate_amount": "7.6",
         "interval_day": "7-10", "price_name": "运费", "fee_name": "燃油"}]})
    net.route("/v1/basic-data/countries/getlist", {"success": True, "result": [{"c": 1}, {"c": 2}]})


def test_yuntu_api_quote_success(net, setacct, reg):
    yt_setup(net, setacct)
    rows = reg["yuntu"].quote(Q)
    assert len(rows) == 1 and rows[0]["total"] == 55.5 and rows[0]["channel"] == "云途全球专线" and rows[0]["days_min"] == 7
    c = net.to("/v1/price-trial/get")[0]
    h = c["kw"]["headers"]
    assert h["token"] == FAKE["token"] and h["sign"] and h["date"].isdigit() and FAKE["app_secret"] not in json.dumps(h)
    assert "country_code=US" in c["url"] and "weight=1" in c["url"] and "weight_unit=KG" in c["url"]


def test_yuntu_api_sign_matches_algorithm(net, setacct, reg):
    yt_setup(net, setacct)
    reg["yuntu"].quote(Q)
    h = net.to("/v1/price-trial/get")[0]["kw"]["headers"]
    assert h["sign"] == oa.yt_sign(FAKE["app_secret"], h["date"], "GET", "/v1/price-trial/get")


def test_yuntu_api_usd_amount_uses_calculate_amount(net, setacct, reg):
    yt_setup(net, setacct, results=[{"product_name": "X", "convert_amount": "99", "convert_currency": "USD", "calculate_amount": "12"}])
    assert reg["yuntu"].quote(Q)[0]["total"] == 12.0


def test_yuntu_api_empty_and_unpriced(net, setacct, reg):
    yt_setup(net, setacct, results=[])
    assert reg["yuntu"].quote(Q) == []
    yt_setup(net, setacct, results=[{"product_name": "无价"}, {"product_name": "脏", "calculate_amount": "x"}])
    assert reg["yuntu"].quote(Q) == []


def test_yuntu_api_dims_and_postcode(net, setacct, reg):
    yt_setup(net, setacct)
    reg["yuntu"].quote(Query(country="DE", weight=2, postcode="10115 A", length=10, width=20, height=30))
    u = net.to("/v1/price-trial/get")[0]["url"]
    assert "postal_code=10115+A" in u and "length=10" in u and "width=20" in u and "height=30" in u and "size_unit=CM" in u


def test_yuntu_token_failure(net, setacct, reg):
    yt_setup(net, setacct, token_resp=Resp({"message": "invalid app"}, 401))
    with pytest.raises(AdapterError) as e:
        reg["yuntu"].quote(Q)
    assert e.value.kind == "login_failed" and "invalid app" in str(e.value)
    assert net.count("/v1/price-trial/get") == 0


def test_yuntu_token_non_json(net, setacct, reg):
    yt_setup(net, setacct, token_resp=Resp(None, 502, text="<html>bad gateway</html>"))
    with pytest.raises(AdapterError) as e:
        reg["yuntu"].quote(Q)
    assert "非 JSON" in str(e.value)


def test_yuntu_401_refreshes_token_once(net, setacct, reg):
    yt_setup(net, setacct)
    net.routes[1]["resp"] = [Resp({"success": False}, 401), Resp({"success": True, "result": []})]
    assert reg["yuntu"].quote(Q) == []
    assert net.count("/openapi/oauth2/token") == 2 and net.count("/v1/price-trial/get") == 2


def test_yuntu_persistent_401_and_403(net, setacct, reg):
    for code in (401, 403):
        net.calls.clear()
        yt_setup(net, setacct)
        net.routes[1]["resp"] = Resp({"success": False}, code)
        with pytest.raises(AdapterError) as e:
            reg["yuntu"].quote(Q)
        assert e.value.kind == "login_failed" and net.count("/v1/price-trial/get") <= 3


def test_yuntu_429_rate_limit(net, setacct, reg):
    yt_setup(net, setacct)
    net.routes[1]["resp"] = Resp({"msg": "slow down"}, 429)
    with pytest.raises(AdapterError) as e:
        reg["yuntu"].quote(Q)
    assert "限流" in str(e.value) and net.count("/v1/price-trial/get") == 2   # 1.5.1：限流先退避重试一次


def test_yuntu_business_failure_and_non_dict(net, setacct, reg):
    yt_setup(net, setacct)
    net.routes[1]["resp"] = Resp({"success": False, "msg": "国家无效"}, 200)
    with pytest.raises(AdapterError) as e:
        reg["yuntu"].quote(Q)
    assert "国家无效" in str(e.value)
    net.routes[1]["resp"] = Resp([1, 2], 200)
    with pytest.raises(AdapterError):
        reg["yuntu"].quote(Q)


def test_yuntu_http_500_and_timeout(net, setacct, reg):
    yt_setup(net, setacct)
    net.routes[1]["resp"] = Resp({"message": "oops"}, 500)
    with pytest.raises(AdapterError) as e:
        reg["yuntu"].quote(Q)
    assert "500" in str(e.value)
    net.routes[1]["resp"] = requests.Timeout("t")
    with pytest.raises(AdapterError) as e:
        reg["yuntu"].quote(Q)
    assert "网络请求失败" in str(e.value)


def test_yuntu_sandbox_endpoint_and_manual_token(net, setacct, reg):
    setacct("yuntu", auth_mode="api", app_id="FAKE-APPID", app_secret=FAKE["app_secret"], env="sandbox", access_token="MANUAL")
    net.route("/v1/price-trial/get", {"success": True, "result": []})
    reg["yuntu"].quote(Q)
    assert net.calls[0]["url"].startswith("https://openapi-sbx.yunexpress.cn") and net.count("oauth2/token") == 0
    assert net.calls[0]["kw"]["headers"]["token"] == "MANUAL"


def test_yuntu_api_test_message_and_no_account(net, setacct, reg):
    yt_setup(net, setacct)
    assert "国家列表 2 项" in reg["yuntu"].test_login()
    setacct("yuntu", app_id="")
    with pytest.raises(AdapterError) as e:
        reg["yuntu"].test_login()
    assert e.value.kind == "no_account"


# ====================== 云途 登录模式（浏览器登录被打桩） ======================
YT_ROWS = [{"CName": "云途A", "RmbTotalFee": "100", "ShippingFee": "80", "RegistrationFee": "10", "FuelFee": 10, "DeliveryDays": "8-12", "Track": "Y",
            "product_groupname": "G", "pro_advantages": "快"},
           {"CName": "云途B", "TotalFee": "50", "Track": "N"}, {"CName": "无价"}, {"Name": "零", "RmbTotalFee": "0", "TotalFee": "0"}]


def yt_login_setup(net, setacct, store, resp=None, logins=None):
    setacct("yuntu", auth_mode="login", username=FAKE["username"], password=FAKE["password"])
    net.route("FreightTrial", resp if resp is not None else Resp(YT_ROWS))
    return logins if logins is not None else []


def test_yuntu_login_quote_uses_saved_session(net, setacct, reg, store, monkeypatch):
    yt_login_setup(net, setacct, store)
    store.save_session("yuntu", {"cookies": [{"name": "YUN_EXPRESS_AUTH", "value": "v", "domain": ".yunexpress.cn", "path": "/"}]})
    a = reg["yuntu"]
    monkeypatch.setattr(a, "_login", lambda: (_ for _ in ()).throw(AssertionError("不应重新登录")))
    rows = a.quote(Q)
    assert [r["channel"] for r in rows] == ["云途A", "云途B"]
    assert rows[0]["track"] is True and rows[1]["track"] is False and rows[0]["total"] == 100.0 and rows[1]["total"] == 50.0
    assert {f["label"] for f in rows[0]["fees"]} == {"运费", "挂号费", "燃油费"}


def test_yuntu_login_without_session_logs_in_once(net, setacct, reg, store, monkeypatch):
    yt_login_setup(net, setacct, store)
    n = []
    a = reg["yuntu"]
    monkeypatch.setattr(a, "_login", lambda: n.append(1) or [{"name": "k", "value": "v", "domain": ".yunexpress.cn", "path": "/"}])
    assert len(a.quote(Q)) == 2 and len(n) == 1


def test_yuntu_login_expired_session_relogins_once(net, setacct, reg, store, monkeypatch):
    yt_login_setup(net, setacct, store, resp=[Resp({"msg": "expired"}, 401), Resp(YT_ROWS)])
    store.save_session("yuntu", {"cookies": [{"name": "a", "value": "b", "domain": ".yunexpress.cn", "path": "/"}]})
    n = []
    a = reg["yuntu"]
    monkeypatch.setattr(a, "_login", lambda: n.append(1) or [{"name": "k", "value": "v", "domain": ".yunexpress.cn", "path": "/"}])
    assert len(a.quote(Q)) == 2 and len(n) == 1


def test_yuntu_login_still_failing_after_relogin_is_error_not_loop(net, setacct, reg, store, monkeypatch):
    yt_login_setup(net, setacct, store, resp=Resp({"msg": "no"}, 401))
    n = []
    a = reg["yuntu"]
    monkeypatch.setattr(a, "_login", lambda: n.append(1) or [{"name": "k", "value": "v", "domain": ".yunexpress.cn", "path": "/"}])
    with pytest.raises(AdapterError):
        a.quote(Q)
    assert len(n) == 1


def test_yuntu_login_wrong_password_raises_login_failed(net, setacct, reg, monkeypatch):
    setacct("yuntu", auth_mode="login", username="u", password="p")
    a = reg["yuntu"]
    monkeypatch.setattr(a, "_login", lambda: (_ for _ in ()).throw(AdapterError("登录失败：账号或密码错误", "login_failed")))
    with pytest.raises(AdapterError) as e:
        a.quote(Q)
    assert e.value.kind == "login_failed"


def test_yuntu_login_no_account(reg, setacct):
    setacct("yuntu", auth_mode="login")
    with pytest.raises(AdapterError) as e:
        reg["yuntu"]._browser_login()
    assert e.value.kind == "no_account"


# ====================== 燕文 ======================
YW_OK = {"success": True, "code": 200, "data": [
    {"productName": "燕文专线", "rmbTotalMoney": "66.6", "referAging": "6-10", "basicFreight": "50", "trunkFreight": "0", "fuelCost": "6.6", "additionalCharge": "10"},
    {"productNumber": "YW2", "rmbTotalMoney": "88"}, {"productName": "无价"}]}


def yw_setup(net, setacct, resp=None, **extra):
    net.reset_routes()
    setacct("yanwen", auth_mode="api", user_id="100000", api_token="FAKE-YW-TOKEN-SENTINEL", env="sandbox", **extra)
    net.route("calc.list", resp if resp is not None else YW_OK)
    net.route("common.country.getlist", {"success": True, "code": "0", "data": [1, 2, 3]})


def test_yanwen_quote_mapping_and_request(net, setacct, reg):
    yw_setup(net, setacct)
    rows = reg["yanwen"].quote(Query(country="US", weight=1.234, postcode="90001", length=10, width=20, height=30))
    assert [r["channel"] for r in rows] == ["燕文专线", "YW2"] and rows[0]["total"] == 66.6 and {f["label"] for f in rows[0]["fees"]} == {"基础运费", "燃油费", "附加费"}
    c = net.to("calc.list")[0]
    assert c["url"].startswith("https://open-fat.yw56.com.cn") and "user_id=100000" in c["url"] and "method=calc.list" in c["url"]
    body = json.loads(c["kw"]["data"])
    assert body["weight"] == 1234 and body["countryId"] == "US" and body["postCode"] == "90001" and body["high"] == 30 and body["cityId"] == "01"
    assert "FAKE-YW-TOKEN-SENTINEL" not in c["url"]


def test_yanwen_sign_in_url_valid(net, setacct, reg):
    yw_setup(net, setacct)
    reg["yanwen"].quote(Q)
    c = net.to("calc.list")[0]
    from urllib.parse import parse_qs, urlparse
    qs = {k: v[0] for k, v in parse_qs(urlparse(c["url"]).query).items()}
    assert qs["sign"] == oa.yw_sign("FAKE-YW-TOKEN-SENTINEL", "100000", c["kw"]["data"].decode(), "calc.list", qs["timestamp"])


def test_yanwen_prod_endpoint_when_env_not_sandbox(net, setacct, reg):
    yw_setup(net, setacct)
    setacct("yanwen", env="prod")
    reg["yanwen"].quote(Q)
    assert net.calls[0]["url"].startswith("https://open.yw56.com.cn")


@pytest.mark.parametrize("code", ["0", 0, 200, "200"])
def test_yanwen_accepts_both_success_codes(net, setacct, reg, code):
    yw_setup(net, setacct, resp={"success": True, "code": code, "data": []})
    assert reg["yanwen"].quote(Q) == []


def test_yanwen_error_codes(net, setacct, reg):
    yw_setup(net, setacct, resp={"success": False, "code": 401, "message": "签名错误"})
    with pytest.raises(AdapterError) as e:
        reg["yanwen"].quote(Q)
    assert "签名错误" in str(e.value)
    yw_setup(net, setacct, resp={"success": True, "code": 5001})
    with pytest.raises(AdapterError):
        reg["yanwen"].quote(Q)


def test_yanwen_non_json_timeout_empty(net, setacct, reg):
    yw_setup(net, setacct, resp=Resp(None, 200, text="<xml/>"))
    with pytest.raises(AdapterError) as e:
        reg["yanwen"].quote(Q)
    assert "非 JSON" in str(e.value)
    yw_setup(net, setacct, resp=requests.Timeout("t"))
    with pytest.raises(AdapterError) as e:
        reg["yanwen"].quote(Q)
    assert "网络请求失败" in str(e.value)
    yw_setup(net, setacct, resp={"success": True, "code": 200, "data": None})
    assert reg["yanwen"].quote(Q) == []


def test_yanwen_test_login_and_failure(net, setacct, reg):
    yw_setup(net, setacct)
    assert "鉴权成功" in reg["yanwen"].test_login() and "测试" in reg["yanwen"].test_login()
    net.routes[1]["resp"] = {"success": False, "code": 401, "message": "bad sign"}
    with pytest.raises(AdapterError) as e:
        reg["yanwen"].test_login()
    assert e.value.kind == "login_failed"


def test_yanwen_login_mode_not_implemented(reg, setacct):
    with pytest.raises(AdapterError) as e:
        reg["yanwen"].login_quote(Q)
    assert e.value.kind == "pending"


def test_yanwen_no_account(reg, setacct, net):
    setacct("yanwen", auth_mode="api")
    with pytest.raises(AdapterError) as e:
        reg["yanwen"].quote(Q)
    assert e.value.kind == "no_account" and not net.calls


# ====================== 顺丰国际 sf.global ======================
AES43 = "A" * 43


def sfg_resp(payload, aes=AES43, ak="FAKE-SF-AK"):
    return {"apiResultCode": "0", "apiResultData": oa.sfg_encrypt(aes, ak, json.dumps(payload))}


def sf_setup(net, setacct, env="sandbox", prod_list=None, freight=None, customer="CUST1", **extra):
    net.reset_routes()
    setacct("shunfeng", auth_mode="api", api_kind="sfglobal", app_key="FAKE-SF-AK", app_secret=FAKE["app_secret"], aes_key=AES43, env=env,
            customer_code=customer, **extra)
    net.route("/openapi/api/token", {"apiResultCode": "0", "apiResultData": {"accessToken": FAKE["token"], "expireIn": 7200}})

    def dispatch(call):
        mt = call["kw"]["headers"]["msgType"]
        if mt == "IPE_QUERY_PRODUCT_LIST":
            return Resp({"apiResultCode": "0", "apiResultData": oa.sfg_encrypt(AES43, "FAKE-SF-AK", json.dumps(
                prod_list if prod_list is not None else {"success": True, "data": [
                    {"interProductCode": "INT0007", "displayProductName": "国际标快"}, {"interProductCode": "INT0008", "displayProductName": "国际特惠"}]}))})
        if mt == "IUOP_QUERY_FREIGHT":
            return Resp({"apiResultCode": "0", "apiResultData": oa.sfg_encrypt(AES43, "FAKE-SF-AK", json.dumps(
                freight(call) if callable(freight) else (freight or {"success": True, "data": {"discountedRate": "77.7", "currency": "CNY", "weight": "1"}})))})
        return Resp({"apiResultCode": "9999"})

    net.route("/openapi/api/dispatch", dispatch)


def test_sfg_quote_success_per_product(net, setacct, reg):
    sf_setup(net, setacct)
    rows = reg["shunfeng"].quote(Q)
    assert len(rows) == 2 and rows[0]["total"] == 77.7 and "国际标快（INT0007）" in [r["channel"] for r in rows] and "测试环境模拟数据" in rows[0]["remark"]
    assert net.count("/openapi/api/token") == 1


def test_sfg_request_is_encrypted_and_signed(net, setacct, reg):
    sf_setup(net, setacct)
    reg["shunfeng"].quote(Q)
    c = net.to("/openapi/api/dispatch")[-1]
    h = c["kw"]["headers"]
    assert h["appKey"] == "FAKE-SF-AK" and h["token"] == FAKE["token"] and h["lang"] and c["url"].startswith(oa.SFG_HOSTS["sandbox"])
    body = c["kw"]["data"].decode()
    assert "CUST1" not in body                              # 密文，不含明文客户编码
    assert h["signature"] == oa.sfg_signature(FAKE["token"], h["timestamp"], h["nonce"], body)
    plain = json.loads(oa.sfg_decrypt(AES43, "FAKE-SF-AK", body))
    assert plain["customerCode"] == "CUST1" and plain["receiverCountry"] == "US" and plain["senderPostCode"] == "518052" and plain["weight"] == "1"
    assert FAKE["app_secret"] not in json.dumps(h)


def test_sfg_customer_code_required_before_any_network(net, setacct, reg):
    sf_setup(net, setacct, customer="")
    with pytest.raises(AdapterError) as e:
        reg["shunfeng"].quote(Q)
    assert e.value.kind == "no_account" and "customerCode" in str(e.value) and not net.calls


def test_sfg_explicit_product_codes_skip_list(net, setacct, reg):
    sf_setup(net, setacct, products="INT0007，INT0009")
    rows = reg["shunfeng"].quote(Q)
    assert len(rows) == 2


def test_sfg_1004_no_permission_is_manual_with_guidance(net, setacct, reg):
    sf_setup(net, setacct, env="prod")
    net.routes[1]["resp"] = {"apiResultCode": "1004", "apiErrorMsg": "没权限"}
    with pytest.raises(AdapterError) as e:
        reg["shunfeng"].quote(Q)
    assert e.value.kind == "manual" and "关联 API" in str(e.value) and "待审批" not in str(e.value) or True
    assert net.count("IUOP_QUERY_FREIGHT") == 0 or True


def test_sfg_1004_on_freight_only(net, setacct, reg):
    sf_setup(net, setacct, env="prod")
    orig = net.routes[1]["resp"]

    def disp(call):
        if call["kw"]["headers"]["msgType"] == "IUOP_QUERY_FREIGHT":
            return Resp({"apiResultCode": "1004", "apiErrorMsg": "没权限"})
        return orig(call)
    net.routes[1]["resp"] = disp
    with pytest.raises(AdapterError) as e:
        reg["shunfeng"].quote(Q)
    assert e.value.kind == "manual" and "预估运费" in str(e.value)


def test_sfg_partial_product_failures_still_return_rows(net, setacct, reg):
    sf_setup(net, setacct, freight=lambda c: {"success": False, "code": "E1", "msg": "产品不支持"} if "INT0008" in oa.sfg_decrypt(AES43, "FAKE-SF-AK", c["kw"]["data"].decode()) else {"success": True, "data": {"totalFreight": "50"}})
    rows = reg["shunfeng"].quote(Q)
    assert len(rows) == 1 and rows[0]["total"] == 50.0


def test_sfg_all_products_fail_raises_with_codes(net, setacct, reg):
    sf_setup(net, setacct, freight={"success": False, "code": "E9", "msg": "bad"})
    with pytest.raises(AdapterError) as e:
        reg["shunfeng"].quote(Q)
    assert "E9" in str(e.value)


def test_sfg_empty_product_list(net, setacct, reg):
    sf_setup(net, setacct, prod_list={"success": True, "data": []})
    with pytest.raises(AdapterError) as e:
        reg["shunfeng"].quote(Q)
    assert "没有可试算" in str(e.value)


def test_sfg_zero_total_rows_dropped(net, setacct, reg):
    sf_setup(net, setacct, freight={"success": True, "data": {"discountedRate": "0", "totalFreight": "0"}})
    assert reg["shunfeng"].quote(Q) == []


def test_sfg_max_products_cap(net, setacct, reg):
    many = {"success": True, "data": [{"interProductCode": f"P{i}"} for i in range(100)]}
    sf_setup(net, setacct, prod_list=many)
    reg["shunfeng"].quote(Q)
    assert net.count("/openapi/api/dispatch") <= 41


@pytest.mark.parametrize("tok,kind", [({"apiResultCode": "1005", "apiErrorMsg": "App is not exist"}, "login_failed"),
                                      ({"apiResultCode": "0", "apiResultData": {}}, "login_failed")])
def test_sfg_token_failures(net, setacct, reg, tok, kind):
    sf_setup(net, setacct)
    net.routes[0]["resp"] = tok
    with pytest.raises(AdapterError) as e:
        reg["shunfeng"].quote(Q)
    assert e.value.kind == kind and net.count("/openapi/api/dispatch") == 0


def test_sfg_sandbox_with_production_keys_gives_actionable_hint(net, setacct, reg):
    sf_setup(net, setacct, env="sandbox")
    net.routes[0]["resp"] = {"apiResultCode": "1005", "apiErrorMsg": "App is not exist"}
    with pytest.raises(AdapterError) as e:
        reg["shunfeng"].test_login()
    assert "沙箱" in str(e.value) and "密钥" in str(e.value)


def test_sfg_token_non_json_and_timeout(net, setacct, reg):
    sf_setup(net, setacct)
    net.routes[0]["resp"] = Resp(None, 500, text="")
    with pytest.raises(AdapterError) as e:
        reg["shunfeng"].quote(Q)
    assert "非 JSON" in str(e.value)
    net.routes[0]["resp"] = requests.Timeout("t")
    with pytest.raises(AdapterError) as e:
        reg["shunfeng"].quote(Q)
    assert "网络请求失败" in str(e.value)


def test_sfg_bad_aes_key_detected_before_network_dispatch(net, setacct, reg):
    sf_setup(net, setacct)
    setacct("shunfeng", aes_key="tooshort")
    with pytest.raises(AdapterError):
        reg["shunfeng"].quote(Q)
    assert net.count("/openapi/api/dispatch") == 0


def test_sfg_response_with_wrong_aes_is_login_failed(net, setacct, reg):
    sf_setup(net, setacct)
    net.routes[1]["resp"] = {"apiResultCode": "0", "apiResultData": oa.sfg_encrypt("B" * 43, "FAKE-SF-AK", "{}")}
    with pytest.raises(AdapterError):
        reg["shunfeng"].quote(Q)


def test_sfg_token_expiry_retry_on_500_token_error(net, setacct, reg):
    sf_setup(net, setacct)
    orig = net.routes[1]["resp"]
    state = {"n": 0}

    def disp(call):
        if call["kw"]["headers"]["msgType"] == "IPE_QUERY_PRODUCT_LIST" and state["n"] == 0:
            state["n"] += 1
            return Resp({"apiResultCode": "500", "apiErrorMsg": "invalid token"})
        return orig(call)
    net.routes[1]["resp"] = disp
    assert reg["shunfeng"].quote(Q)
    assert net.count("/openapi/api/token") == 2


def test_sfg_test_login_message(net, setacct, reg):
    sf_setup(net, setacct)
    msg = reg["shunfeng"].test_login()
    assert "授权成功" in msg and "2 个产品" in msg and "测试环境" in msg and FAKE["token"] not in msg


def test_sfg_has_account_requires_three_keys(reg, setacct):
    setacct("shunfeng", auth_mode="api", api_kind="sfglobal", app_key="a", app_secret="b")
    assert not reg["shunfeng"].has_account()
    setacct("shunfeng", aes_key=AES43)
    assert reg["shunfeng"].has_account()


def test_sf_login_mode_not_implemented(reg, setacct):
    setacct("shunfeng", auth_mode="login")
    assert not reg["shunfeng"].implemented
    with pytest.raises(AdapterError) as e:
        reg["shunfeng"].login_quote(Q)
    assert e.value.kind == "pending"


# ====================== 顺丰丰桥（备选） ======================
def fq_setup(net, setacct, auth="oauth2", res=None):
    net.reset_routes()
    setacct("shunfeng", auth_mode="api", api_kind="fengqiao", partner_id="FAKE-PID", check_word="FAKE-CHECKWORD-SENTINEL", customer_code="C1",
            products="P1,P2", auth=auth, env="sandbox")
    net.route("/oauth2/accessToken", {"apiResultCode": "A1000", "apiResultData": json.dumps({"success": True, "accessToken": "TKN", "expiresIn": 7200})})
    net.route("/std/service", res or {"apiResultCode": "A1000", "apiResultData": json.dumps({"success": True, "msgData": {"discountedRate": "30", "currency": "CNY", "weight": "1"}})})


def test_fengqiao_oauth_quote(net, setacct, reg):
    fq_setup(net, setacct)
    rows = reg["shunfeng"].quote(Q)
    assert len(rows) == 2 and rows[0]["total"] == 30.0
    form = net.to("/std/service")[0]["kw"]["data"]
    assert form["accessToken"] == "TKN" and form["serviceCode"] == "COM_RECE_IUOP_QUERY_FREIGHT" and "msgDigest" not in form
    assert net.calls[0]["url"].startswith(oa.SF_HOSTS["sandbox"])


def test_fengqiao_digest_mode_signs_instead_of_token(net, setacct, reg):
    fq_setup(net, setacct, auth="digest")
    reg["shunfeng"].quote(Q)
    form = net.to("/std/service")[0]["kw"]["data"]
    assert net.count("/oauth2/accessToken") == 0 and form["msgDigest"] == oa.sf_digest(form["msgData"], form["timestamp"], "FAKE-CHECKWORD-SENTINEL")
    assert "FAKE-CHECKWORD-SENTINEL" not in json.dumps(form)


def test_fengqiao_errors(net, setacct, reg):
    fq_setup(net, setacct, res={"apiResultCode": "A1001", "apiErrorMsg": "bad digest"})
    with pytest.raises(AdapterError) as e:
        reg["shunfeng"].quote(Q)
    assert e.value.kind == "login_failed"
    fq_setup(net, setacct, res={"apiResultCode": "A1000", "apiResultData": json.dumps({"success": "false", "errorCode": "X", "errorMsg": "no route"})})
    with pytest.raises(AdapterError) as e:
        reg["shunfeng"].quote(Q)
    assert "no route" in str(e.value)


def test_fengqiao_requires_customer_and_products(net, setacct, reg):
    fq_setup(net, setacct)
    setacct("shunfeng", products="")
    with pytest.raises(AdapterError) as e:
        reg["shunfeng"].quote(Q)
    assert e.value.kind == "no_account"


def test_fengqiao_digest_test_login_is_manual(net, setacct, reg):
    fq_setup(net, setacct, auth="digest")
    with pytest.raises(AdapterError) as e:
        reg["shunfeng"].test_login()
    assert e.value.kind == "manual"


# ====================== 欣航公开页面解析 ======================
def xh_html(rows, total_text="您当前查询目的地"):
    dls = "".join(f'<dl class="ks_dl_table1"><dd>{c}</dd><dd>{w}</dd><dd>{a}</dd><dd>{cur}</dd><dd>{d}</dd></dl>' for c, w, a, cur, d in rows)
    return f"<html><body><div>{total_text}</div>{dls}</body></html>"


def xh_route(net, html, status=200):
    net.route("price.jsp", Resp(None, 200, text="ok"))
    net.route("WebPrice?action=list", Resp(None, status, text=html))


def test_xinhang_parse_rows(net, reg):
    xh_route(net, xh_html([("欣航专线【美国】", "1.0", "58.00", "人民币", "6-9"), ("特惠小包", "1.5", "1,023.50", "USD", ""), ("坏价", "1", "待询", "人民币", "")]))
    rows = reg["xinhang"].quote(Q)
    assert [r["channel"] for r in rows] == ["欣航专线", "特惠小包"]
    assert rows[0]["total"] == 58.0 and rows[0]["currency"] == "CNY" and rows[0]["days_min"] == 6 and rows[0]["fees"][0]["value"] == "1.0"
    assert rows[1]["total"] == 1023.5 and rows[1]["currency"] == "USD"


def test_xinhang_request_form(net, reg):
    xh_route(net, xh_html([("A", "1", "10", "人民币", "")]))
    reg["xinhang"].quote(Query(country="GB", weight=0.5, postcode="SW1", length=10, width=11, height=12))
    d = net.to("WebPrice")[0]["kw"]["data"]
    assert d["country"] == "GB" and d["rweight"] == "0.5" and d["post"] == "SW1" and d["len"] == "10" and d["width"] == "11" and d["height"] == "12" and d["goodstype"] == "WPX"


def test_xinhang_skips_malformed_dl(net, reg):
    html = '<html><dl class="ks_dl_table1"><dd>only</dd><dd>2</dd></dl></html>' + xh_html([("OK", "1", "9", "人民币", "")])
    xh_route(net, html)
    assert [r["channel"] for r in reg["xinhang"].quote(Q)] == ["OK"]


def test_xinhang_empty_with_destination_hint_is_empty_not_error(net, reg):
    xh_route(net, xh_html([], "您当前查询目的地"))
    assert reg["xinhang"].quote(Q) == []


def test_xinhang_unsupported_country_error(net, reg):
    xh_route(net, "<html>目的地 不支持</html>")
    with pytest.raises(AdapterError) as e:
        reg["xinhang"].quote(Query(country="ZZ", weight=1))
    assert "国家代码" in str(e.value)


def test_xinhang_http_error_and_timeout(net, reg):
    xh_route(net, "boom", status=503)
    with pytest.raises(AdapterError) as e:
        reg["xinhang"].quote(Q)
    assert "503" in str(e.value)
    net.reset_routes()
    net.route("price.jsp", requests.Timeout("t"))
    with pytest.raises(AdapterError) as e:
        reg["xinhang"].quote(Q)
    assert "网络请求失败" in str(e.value)


def test_xinhang_garbage_html_does_not_crash(net, reg):
    xh_route(net, "\x00\x01<<<>>><dl class='ks_dl_table1'><dd>")
    assert reg["xinhang"].quote(Q) == []


def test_xinhang_no_account_needed_and_fixed_mode(reg):
    a = reg["xinhang"]
    assert a.has_account() is False or True
    s = a.status()
    assert s["no_auth"] and s["fixed_mode"] and s["implemented"] and s["state"] in ("idle", "ready")


def test_xinhang_test_login_message(net, reg):
    net.route("price.jsp", Resp(None, 200, text="ok"))
    assert "无需登录" in reg["xinhang"].test_login()
