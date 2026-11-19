"""Intelink OIS 适配器（雨桐 / 环洋 / 中航 API 模式）及 V5 登录模式：mock 响应覆盖成功、鉴权失败、过期重试、超时、非 JSON、空列表、限流。"""
import json
import time

import pytest
import requests

from app.adapters import openapi as oa
from app.adapters.base import AdapterError, Query
from tests.suite.conftest import FAKE, OIS_ITEM, Resp, ois_ok_routes

pytestmark = pytest.mark.unit


def ois_setup(net, setacct, pid="yutong", items=None, plist=None, **extra):
    setacct(pid, auth_mode="api", app_key=FAKE["app_key"], app_secret=FAKE["app_secret"], **extra)
    ois_ok_routes(net, quote_items=items, products=plist)


def ad(reg, pid="yutong"):
    return reg[pid]


@pytest.mark.parametrize("pid", ["yutong", "zhonghang", "huanyang"])
def test_api_quote_success_row_mapping(net, setacct, reg, pid):
    ois_setup(net, setacct, pid)
    rows = ad(reg, pid).quote(Query(country="US", weight=1))
    assert len(rows) == 1
    r = rows[0]
    assert r["channel"] == "美国专线小包" and r["total"] == 123.45 and r["currency"] == "CNY"
    assert (r["days_min"], r["days_max"]) == (5, 8) and r["group"] == "专线" and r["platform"] == pid
    labels = {f["label"]: f["value"] for f in r["fees"]}
    assert labels["运费"] == "100" and labels["燃油"] == "10.5" and labels["偏远费"] == "3" and labels["材积除数"] == "5000"
    assert "一区" in r["remark"] and "首重+续重" in r["remark"]


def test_api_quote_request_construction(net, setacct, reg):
    ois_setup(net, setacct, products="A, B，C")
    ad(reg).quote(Query(country="GB", weight=2.5, postcode="SW1A", length=30, width=20, height=10))
    call = net.to("queryQuoteList")[0]
    body = json.loads(call["kw"]["data"]["body1"])
    assert body["weig"] == 2.5 and body["destNo"] == "GB" and body["reZip"] == "SW1A" and body["packing"] == "WPX"
    assert body["pieces"] == [{"actual": 2.5, "length": 30, "width": 20, "height": 10}]
    assert body["hubinCodes"] == ["A", "B", "C"]
    h = call["kw"]["headers"]
    assert set(h) == {"token", "sign", "version"} and h["sign"] == h["sign"].upper()


def test_api_quote_omits_optional_fields(net, setacct, reg):
    ois_setup(net, setacct)
    ad(reg).quote(Query(country="US", weight=1, length=10))   # 只有长度 → 不带尺寸
    body = json.loads(net.to("queryQuoteList")[0]["kw"]["data"]["body1"])
    assert "pieces" not in body and "reZip" not in body and "hubinCodes" not in body


def test_api_quote_filters_placeholder_and_invalid_prices(net, setacct, reg):
    items = [dict(OIS_ITEM, name="无报价占位", standardFeeSum=1000000), dict(OIS_ITEM, name="零", standardFeeSum=0),
             dict(OIS_ITEM, name="缺失", standardFeeSum=None), dict(OIS_ITEM, name="脏", standardFeeSum="abc"),
             dict(OIS_ITEM, name="极低", standardFeeSum=5), dict(OIS_ITEM, name="字符串价", standardFeeSum="88.8")]
    ois_setup(net, setacct, items=items)
    rows = ad(reg).quote(Query(country="US", weight=1))
    assert [r["channel"] for r in rows] == ["极低", "字符串价"]
    assert any(f["label"] == "提示" for f in rows[0]["fees"])        # <10 提示疑似境内段
    assert not any(f["label"] == "提示" for f in rows[1]["fees"])


def test_api_quote_empty_list(net, setacct, reg):
    ois_setup(net, setacct, items=[])
    assert ad(reg).quote(Query(country="US", weight=1)) == []


def test_api_quote_body_null(net, setacct, reg):
    ois_setup(net, setacct)
    net.reset_routes()
    ois_ok_routes(net)
    net.routes[2]["resp"] = {"result_code": 0, "body": None}
    assert ad(reg).quote(Query(country="US", weight=1)) == []


def test_api_quote_product_list_failure_does_not_block_quote(net, setacct, reg):
    ois_setup(net, setacct)
    net.routes[1]["resp"] = {"result_code": 500, "message": "boom"}
    rows = ad(reg).quote(Query(country="US", weight=1))
    assert len(rows) == 1 and rows[0]["group"] == ""


def test_auth_failure_maps_to_login_failed(net, setacct, reg):
    ois_setup(net, setacct)
    net.routes[0]["resp"] = {"body": {"ack": "false"}, "message": "密钥错误"}
    with pytest.raises(AdapterError) as e:
        ad(reg).quote(Query(country="US", weight=1))
    assert e.value.kind == "login_failed" and "密钥错误" in str(e.value)
    assert net.count("queryQuoteList") == 0           # 授权失败后不再继续调用业务接口


@pytest.mark.parametrize("bad", [Resp(None, 502, text="<html>Bad Gateway</html>"), Resp(None, 200, text=""), Resp(None, 200, text="not json")])
def test_auth_non_json_is_clear_error(net, setacct, reg, bad):
    ois_setup(net, setacct)
    net.routes[0]["resp"] = bad
    with pytest.raises(AdapterError) as e:
        ad(reg).quote(Query(country="US", weight=1))
    assert "非 JSON" in str(e.value) and "<html>" not in str(e.value)


def test_quote_malformed_json_is_clear_error(net, setacct, reg):
    ois_setup(net, setacct)
    net.routes[2]["resp"] = Resp(None, 200, text="{broken")
    with pytest.raises(AdapterError) as e:
        ad(reg).quote(Query(country="US", weight=1))
    assert "非 JSON" in str(e.value)


@pytest.mark.parametrize("exc", [requests.Timeout("timed out"), requests.ConnectionError("refused"), requests.exceptions.SSLError("ssl")])
def test_network_errors_become_adapter_errors(net, setacct, reg, exc):
    ois_setup(net, setacct)
    net.routes[0]["resp"] = exc
    with pytest.raises(AdapterError) as e:
        ad(reg).quote(Query(country="US", weight=1))
    assert "网络请求失败" in str(e.value)


def test_timeout_on_quote_step(net, setacct, reg):
    ois_setup(net, setacct)
    net.routes[2]["resp"] = requests.Timeout("read timed out")
    with pytest.raises(AdapterError):
        ad(reg).quote(Query(country="US", weight=1))


def test_expired_token_reauths_exactly_once_and_succeeds(net, setacct, reg):
    ois_setup(net, setacct)
    net.routes[2]["resp"] = [{"result_code": 1004, "message": "token expired"}, {"result_code": 0, "body": [OIS_ITEM]}]
    rows = ad(reg).quote(Query(country="US", weight=1))
    assert len(rows) == 1 and net.count("getAuth") == 2 and net.count("queryQuoteList") == 2


def test_expired_token_twice_gives_up_without_loop(net, setacct, reg):
    ois_setup(net, setacct)
    net.routes[2]["resp"] = {"result_code": 1004, "message": "x"}
    with pytest.raises(AdapterError) as e:
        ad(reg).quote(Query(country="US", weight=1))
    assert e.value.kind == "login_failed" and net.count("getAuth") == 2 and net.count("queryQuoteList") == 2


@pytest.mark.parametrize("code", [1005, 401])
def test_other_expiry_codes_trigger_reauth(net, setacct, reg, code):
    ois_setup(net, setacct)
    net.routes[2]["resp"] = [{"result_code": code}, {"result_code": 0, "body": [OIS_ITEM]}]
    assert len(ad(reg).quote(Query(country="US", weight=1))) == 1


def test_business_error_code_is_reported_with_message(net, setacct, reg):
    ois_setup(net, setacct)
    net.routes[2]["resp"] = {"result_code": 2001, "message": "目的国不支持"}
    with pytest.raises(AdapterError) as e:
        ad(reg).quote(Query(country="ZZ", weight=1))
    assert "2001" in str(e.value) and "目的国不支持" in str(e.value)


def test_rate_limit_http_429_with_html(net, setacct, reg):
    ois_setup(net, setacct)
    net.routes[2]["resp"] = Resp(None, 429, text="Too Many Requests")
    with pytest.raises(AdapterError):
        ad(reg).quote(Query(country="US", weight=1))


def test_token_cached_between_quotes_and_in_session_file(net, setacct, reg, store):
    ois_setup(net, setacct)
    a = ad(reg)
    a.quote(Query(country="US", weight=1))
    a.quote(Query(country="GB", weight=1))
    assert net.count("getAuth") == 1
    sess = store.load_session("yutong_api")
    assert sess["kind"] == "ois" and sess["token"] == FAKE["token"] and sess["exp"] > time.time() + 3600


def test_token_cache_invalidated_when_key_changes(net, setacct, reg):
    ois_setup(net, setacct)
    a = ad(reg)
    a.quote(Query(country="US", weight=1))
    setacct("yutong", app_key="FAKE-OTHER-KEY")
    a.quote(Query(country="US", weight=1))
    assert net.count("getAuth") == 2


def test_token_cache_expired_session_triggers_reauth(net, setacct, reg, store):
    ois_setup(net, setacct)
    a = ad(reg)
    a.quote(Query(country="US", weight=1))
    s = store.load_session("yutong_api")
    s["exp"] = time.time() + 10
    store.save_session("yutong_api", s)
    a.quote(Query(country="US", weight=1))
    assert net.count("getAuth") == 2


def test_missing_credentials_raise_no_account_without_network(net, reg, setacct):
    setacct("yutong", auth_mode="api")
    with pytest.raises(AdapterError) as e:
        ad(reg).quote(Query(country="US", weight=1))
    assert e.value.kind == "no_account" and not net.calls
    with pytest.raises(AdapterError) as e2:
        ad(reg).test_login()
    assert e2.value.kind == "no_account"


def test_api_test_message(net, setacct, reg):
    ois_setup(net, setacct)
    msg = ad(reg).test_login()
    assert "授权成功" in msg and "1 个产品" in msg and FAKE["token"] not in msg


def test_huanyang_default_base_and_custom_base(net, setacct, reg):
    ois_setup(net, setacct, "huanyang")
    reg["huanyang"].quote(Query(country="US", weight=1))
    assert net.calls[0]["url"].startswith("https://ekttech.net/pos-web/ois/order/getAuth")
    net.calls.clear()
    setacct("huanyang", api_base="https://custom.example.com/x/")
    reg["huanyang"].quote(Query(country="US", weight=1))
    assert net.calls[0]["url"].startswith("https://custom.example.com/x/ois/order/getAuth")


def test_zhonghang_and_yutong_default_base(net, setacct, reg):
    for pid in ("zhonghang", "yutong"):
        net.calls.clear()
        ois_setup(net, setacct, pid)
        reg[pid].quote(Query(country="US", weight=1))
        assert net.calls[0]["url"].startswith(oa.OIS_DEFAULT)


def test_secret_never_sent_as_plain_header_in_business_calls(net, setacct, reg):
    ois_setup(net, setacct)
    ad(reg).quote(Query(country="US", weight=1))
    for c in net.to("queryQuoteList") + net.to("getProductList"):
        assert FAKE["app_secret"] not in json.dumps(c["kw"].get("headers", {})) and FAKE["app_secret"] not in json.dumps(c["kw"].get("data", {}))


def test_float_weight_precision_in_request(net, setacct, reg):
    ois_setup(net, setacct)
    ad(reg).quote(Query(country="US", weight=0.1 + 0.2))
    body = json.loads(net.to("queryQuoteList")[0]["kw"]["data"]["body1"])
    assert abs(body["weig"] - 0.3) < 1e-9


def test_huge_and_tiny_weights_pass_through_adapter(net, setacct, reg):
    ois_setup(net, setacct)
    for w in (0.001, 99999.9):
        assert ad(reg).quote(Query(country="US", weight=w))


# ---------------- 中航 / 环洋 V5 登录模式 ----------------
def login_ok(token="FAKE-TOKEN-SENTINEL-abc123"):
    return {"result_code": 0, "body": {"token": token}}


QUOTE_LIST = {"result_code": 0, "body": {"list": [
    {"name": "A线", "quoteName": "组A", "standardFeeSum": "88.5", "standardCur": "CNY", "aging": "7-12", "unitPrice": 60, "currency": "CNY",
     "fuelRate": "10%", "formulaView": "x" * 300, "endDate": "2026-12-31 00:00:00"},
    {"name": "B线", "quoteFee": "70", "aging": ""}, {"name": "无价"}]}}


def v5_setup(net, setacct, pid="zhonghang", login=None, quote=None):
    setacct(pid, auth_mode="login", username=FAKE["username"], password=FAKE["password"])
    net.route("user/oms/login", login or login_ok())
    net.route("oms/bms/quoteObj/list", quote or QUOTE_LIST)


@pytest.mark.parametrize("pid", ["zhonghang", "huanyang"])
def test_v5_login_quote_mapping(net, setacct, reg, pid):
    v5_setup(net, setacct, pid)
    rows = reg[pid].quote(Query(country="US", weight=1.5, postcode="10001", length=10, width=10, height=10))
    assert [r["channel"] for r in rows] == ["A线", "B线"]            # 没有价格的行被丢弃
    a, b = rows
    assert a["total"] == 88.5 and a["group"] == "组A" and (a["days_min"], a["days_max"]) == (7, 12) and "2026-12-31" in a["remark"]
    assert next(f for f in a["fees"] if f["label"] == "计费公式")["value"] == "x" * 200
    assert b["total"] == 70.0 and b["days"] == ""
    data = net.to("quoteObj/list")[0]["kw"]["data"]
    assert data["destNo"] == "US" and data["weig"] == "1.5" and data["length"] == "10" and data["reZip"] == "10001"


def test_v5_login_password_is_obfuscated_not_plain(net, setacct, reg):
    v5_setup(net, setacct)
    reg["zhonghang"].quote(Query(country="US", weight=1))
    data = net.to("user/oms/login")[0]["kw"]["data"]
    assert data["userNo"] == FAKE["username"] and data["password"] != FAKE["password"] and FAKE["password"] not in json.dumps(data)


def test_v5_login_session_reused(net, setacct, reg):
    v5_setup(net, setacct)
    a = reg["zhonghang"]
    a.quote(Query(country="US", weight=1))
    a.quote(Query(country="DE", weight=1))
    assert net.count("user/oms/login") == 1


def test_v5_session_expiry_relogins_once_and_retries(net, setacct, reg, store):
    v5_setup(net, setacct)
    store.save_session("zhonghang", {"token": "OLD"})
    net.routes[1]["resp"] = [{"result_code": 401}, QUOTE_LIST]
    rows = reg["zhonghang"].quote(Query(country="US", weight=1))
    assert len(rows) == 2 and net.count("user/oms/login") == 1 and net.count("quoteObj/list") == 2


def test_v5_login_failure_message_and_session_cleared(net, setacct, reg, store):
    v5_setup(net, setacct, login={"result_code": 1, "message": "账号错误"})
    store.save_session("zhonghang", {"token": "stale"})
    store.clear_session("zhonghang")
    with pytest.raises(AdapterError) as e:
        reg["zhonghang"].quote(Query(country="US", weight=1))
    assert e.value.kind == "login_failed" and "账号错误" in str(e.value) and store.load_session("zhonghang") is None
    assert net.count("quoteObj/list") == 0


def test_v5_non_json_gateway_block(net, setacct, reg):
    v5_setup(net, setacct, quote=Resp(None, 403, text="<html>blocked</html>"))
    with pytest.raises(AdapterError) as e:
        reg["zhonghang"].quote(Query(country="US", weight=1))
    assert "非 JSON" in str(e.value)


def test_v5_quote_error_code(net, setacct, reg):
    v5_setup(net, setacct, quote={"result_code": 500, "solution": "国家不存在"})
    with pytest.raises(AdapterError) as e:
        reg["zhonghang"].quote(Query(country="ZZ", weight=1))
    assert "国家不存在" in str(e.value)


def test_v5_empty_list(net, setacct, reg):
    v5_setup(net, setacct, quote={"result_code": 0, "body": {"list": []}})
    assert reg["zhonghang"].quote(Query(country="US", weight=1)) == []
    v5_setup(net, setacct, quote={"result_code": 0, "body": None})
    assert reg["zhonghang"].quote(Query(country="US", weight=1)) == []


def test_v5_timeout(net, setacct, reg):
    v5_setup(net, setacct)
    net.routes[0]["resp"] = requests.Timeout("t")
    with pytest.raises(AdapterError) as e:
        reg["zhonghang"].quote(Query(country="US", weight=1))
    assert "网络请求失败" in str(e.value)


def test_v5_no_account(reg, net, setacct):
    setacct("zhonghang", auth_mode="login")
    with pytest.raises(AdapterError) as e:
        reg["zhonghang"].quote(Query(country="US", weight=1))
    assert e.value.kind == "no_account" and not net.calls


def test_v5_login_test_forces_fresh_login(net, setacct, reg, store):
    v5_setup(net, setacct)
    store.save_session("zhonghang", {"token": "OLD"})
    assert "登录成功" in reg["zhonghang"].test_login()
    assert net.count("user/oms/login") == 1 and store.load_session("zhonghang")["token"] == FAKE["token"]
