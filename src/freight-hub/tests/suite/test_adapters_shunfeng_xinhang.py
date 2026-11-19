"""顺丰国际（sf.global AES+SHA256 / 丰桥）与欣航（公开 HTML 解析）适配器。"""
import json

import pytest
import requests

from app.adapters import openapi as oa
from app.adapters.base import AdapterError, Query
from tests.suite.conftest import FAKE, Resp

pytestmark = pytest.mark.unit
Q = Query(country="US", weight=2)
AK, AES = "FAKE-SFG-APPKEY", "A" * 43


def sfg_setup(setacct, **kw):
    d = dict(auth_mode="api", api_kind="sfglobal", app_key=AK, app_secret=FAKE["app_secret"], aes_key=AES, customer_code="CUST1")
    d.update(kw)
    setacct("shunfeng", **d)


def enc(payload):
    return oa.sfg_encrypt(AES, AK, json.dumps(payload, ensure_ascii=False))


def sfg_routes(net, plist=None, freight=None, token_resp=None, dispatch=None):
    net.route("/openapi/api/token", token_resp or {"apiResultCode": "0", "apiResultData": {"accessToken": "FAKE-TOKEN-SENTINEL-abc123", "expireIn": 7200}})

    def disp(call):
        mt = call["kw"]["headers"]["msgType"]
        if dispatch:
            r = dispatch(mt, call)
            if r is not None:
                return r
        if mt == "IPE_QUERY_PRODUCT_LIST":
            return Resp({"apiResultCode": "0", "apiResultData": enc({"success": True, "data": plist if plist is not None else
                                                                      [{"interProductCode": "INT1", "displayProductName": "标快"}, {"interProductCode": "INT2", "displayProductName": "特惠"}]})})
        if mt == "IUOP_QUERY_FREIGHT":
            code = json.loads(oa.sfg_decrypt(AES, AK, call["kw"]["data"].decode()))["interProductCode"]
            d = (freight or {}).get(code, {"discountedRate": "100.5", "totalFreight": "120", "currency": "CNY", "weight": "2"})
            return Resp({"apiResultCode": "0", "apiResultData": enc({"success": True, "data": d})})
        return Resp({"apiResultCode": "9999", "apiErrorMsg": "unknown msgType"})
    net.route("/openapi/api/dispatch", disp)
    return net


# ---------------------------------------------------------------- 加解密 / 签名
def test_sfg_encrypt_decrypt_roundtrip_and_layout():
    for plain in ["", "x", "{\"a\":1}", "中文报文" * 50, "a" * 31, "a" * 32, "a" * 33]:
        c = oa.sfg_encrypt(AES, AK, plain, rand="R" * 16)
        assert oa.sfg_decrypt(AES, AK, c) == plain


def test_sfg_encrypt_deterministic_with_fixed_rand_and_random_otherwise():
    assert oa.sfg_encrypt(AES, AK, "m", rand="R" * 16) == oa.sfg_encrypt(AES, AK, "m", rand="R" * 16)
    assert oa.sfg_encrypt(AES, AK, "m") != oa.sfg_encrypt(AES, AK, "m")


def test_sfg_decrypt_wrong_appkey_is_rejected():
    c = oa.sfg_encrypt(AES, AK, "hello")
    with pytest.raises(AdapterError) as e:
        oa.sfg_decrypt(AES, "FAKE-OTHER-KEY", c)
    assert e.value.kind == "login_failed"


@pytest.mark.parametrize("bad", ["", "short", "A" * 42, "A" * 44, "!!!!"])
def test_sfg_bad_aes_key_rejected(bad):
    with pytest.raises(AdapterError) as e:
        oa.sfg_key(bad)
    assert e.value.kind == "no_account"


def test_sfg_signature_sorted_concat_sha256():
    import hashlib
    assert oa.sfg_signature("t", "1", "n", "e") == hashlib.sha256("".join(sorted(["t", "1", "n", "e"])).encode()).hexdigest()
    assert oa.sfg_signature("b", "a", "d", "c") == oa.sfg_signature("a", "b", "c", "d")


def test_sf_digest_java_urlencode_vector():
    import base64
    import hashlib
    assert oa._java_urlencode("a b*c-d_e.f~g中") == "a+b*c-d_e.f%7Eg%E4%B8%AD"
    assert oa.sf_digest("{}", "1", "ck") == base64.b64encode(hashlib.md5(b"%7B%7D1ck").digest()).decode()


# ---------------------------------------------------------------- sf.global API
def test_sfg_quote_success_all_products(net, setacct, reg):
    sfg_setup(setacct)
    sfg_routes(net, freight={"INT1": {"discountedRate": "80", "currency": "CNY", "weight": "2"}, "INT2": {"totalFreight": "90"}})
    rows = reg["shunfeng"].quote(Q)
    assert sorted((r["channel"], r["total"]) for r in rows) == [("标快（INT1）", 80.0), ("特惠（INT2）", 90.0)]
    assert rows[0]["currency"] == "CNY"


def test_sfg_request_headers_signature_and_encrypted_body(net, setacct, reg):
    sfg_setup(setacct, sender_post="100000", pay_month_card="MC")
    sfg_routes(net)
    reg["shunfeng"].quote(Query(country="DE", weight=1.25, postcode="10115"))
    call = [c for c in net.to("dispatch") if c["kw"]["headers"]["msgType"] == "IUOP_QUERY_FREIGHT"][0]
    h = call["kw"]["headers"]
    body = call["kw"]["data"].decode()
    assert h["appKey"] == AK and h["signature"] == oa.sfg_signature(h["token"], h["timestamp"], h["nonce"], body)
    assert "FAKE" not in body and "customerCode" not in body, "请求体必须是密文"
    msg = json.loads(oa.sfg_decrypt(AES, AK, body))
    assert msg["receiverCountry"] == "DE" and msg["weight"] == "1.25" and msg["receiverPostCode"] == "10115"
    assert msg["senderPostCode"] == "100000" and msg["payMonthCard"] == "MC" and msg["customerCode"] == "CUST1"
    assert FAKE["app_secret"] not in json.dumps([c["kw"].get("headers") for c in net.to("dispatch")])


def test_sfg_token_request_uses_get_params_and_is_cached(net, setacct, reg):
    sfg_setup(setacct)
    sfg_routes(net)
    reg["shunfeng"].quote(Q)
    reg["shunfeng"].quote(Q)
    assert net.count("/openapi/api/token") == 1 and net.to("/token")[0]["method"] == "GET"


def test_sfg_sandbox_host(net, setacct, reg):
    sfg_setup(setacct, env="sandbox")
    sfg_routes(net)
    reg["shunfeng"].quote(Q)
    assert all("api-ifsp-sit.sf.global" in u for u in net.urls())


def test_sfg_missing_customer_code(net, setacct, reg):
    sfg_setup(setacct, customer_code="")
    sfg_routes(net)
    with pytest.raises(AdapterError) as e:
        reg["shunfeng"].quote(Q)
    assert e.value.kind == "no_account" and "customerCode" in str(e.value)


def test_sfg_missing_credentials_no_network(net, setacct, reg):
    setacct("shunfeng", auth_mode="api", api_kind="sfglobal", app_key=AK)
    with pytest.raises(AdapterError) as e:
        reg["shunfeng"].quote(Q)
    assert e.value.kind == "no_account" and not net.calls


def test_sfg_token_auth_failure_locks_and_hides_secret(net, setacct, reg, store):
    sfg_setup(setacct)
    net.route("/openapi/api/token", {"apiResultCode": "1001", "apiErrorMsg": "appKey 或 appSecret 错误"})
    with pytest.raises(AdapterError) as e:
        reg["shunfeng"].quote(Q)
    assert e.value.kind == "login_failed" and FAKE["app_secret"] not in str(e.value)
    assert store.login_locked("shunfeng")
    n = len(net.calls)
    with pytest.raises(AdapterError):          # 登录一次原则：第二次查询不再访问网络
        reg["shunfeng"].quote(Q)
    assert len(net.calls) == n


def test_sfg_1004_no_permission_is_manual_with_guidance_and_no_lock(net, setacct, reg, store):
    sfg_setup(setacct)
    sfg_routes(net, dispatch=lambda mt, c: Resp({"apiResultCode": "1004", "apiErrorMsg": "没权限"}))
    with pytest.raises(AdapterError) as e:
        reg["shunfeng"].quote(Q)
    assert e.value.kind == "manual" and "关联 API" in str(e.value) and "1004" in str(e.value)
    assert not store.login_locked("shunfeng"), "1004 是权限问题而不是密码错误，不应锁定"


def test_sfg_1004_test_connection_message(net, setacct, reg):
    sfg_setup(setacct)
    sfg_routes(net, dispatch=lambda mt, c: Resp({"apiResultCode": "1004", "apiErrorMsg": "没权限"}))
    with pytest.raises(AdapterError) as e:
        reg["shunfeng"].test_login()
    assert e.value.kind == "manual"


def test_sfg_test_login_success_counts_products(net, setacct, reg):
    sfg_setup(setacct)
    sfg_routes(net)
    msg = reg["shunfeng"].test_login()
    assert "2 个产品" in msg and "正式" in msg


def test_sfg_partial_product_failures_still_return_rows(net, setacct, reg):
    sfg_setup(setacct)

    def disp(mt, call):
        if mt == "IUOP_QUERY_FREIGHT" and json.loads(oa.sfg_decrypt(AES, AK, call["kw"]["data"].decode()))["interProductCode"] == "INT2":
            return Resp({"apiResultCode": "0", "apiResultData": enc({"success": False, "code": "E1", "msg": "不支持该国家"})})
    sfg_routes(net, dispatch=disp)
    rows = reg["shunfeng"].quote(Q)
    assert [r["channel"] for r in rows] == ["标快（INT1）"]


def test_sfg_all_products_fail_raises_summary(net, setacct, reg):
    sfg_setup(setacct)
    sfg_routes(net, dispatch=lambda mt, c: Resp({"apiResultCode": "500", "apiErrorMsg": "系统繁忙"}) if mt == "IUOP_QUERY_FREIGHT" else None)
    with pytest.raises(AdapterError) as e:
        reg["shunfeng"].quote(Q)
    assert "INT1" in str(e.value)


def test_sfg_zero_price_rows_dropped_and_empty_product_list(net, setacct, reg):
    sfg_setup(setacct)
    sfg_routes(net, freight={"INT1": {"discountedRate": "0", "totalFreight": "0"}, "INT2": {}})
    assert reg["shunfeng"].quote(Q) == []
    net.reset_routes()
    sfg_routes(net, plist=[])
    with pytest.raises(AdapterError) as e:
        reg["shunfeng"].quote(Q)
    assert "没有可试算" in str(e.value)


def test_sfg_explicit_products_skip_product_list_and_cap(net, setacct, reg):
    sfg_setup(setacct, products="INT9，INT8, INT7")
    sfg_routes(net, plist=[])
    rows = reg["shunfeng"].quote(Q)
    assert sorted(r["channel"] for r in rows) == ["INT7（INT7）", "INT8（INT8）", "INT9（INT9）"]


def test_sfg_product_list_capped_at_40(net, setacct, reg):
    sfg_setup(setacct)
    sfg_routes(net, plist=[{"interProductCode": f"P{i}", "displayProductName": f"产品{i}"} for i in range(55)])
    reg["shunfeng"].quote(Q)
    assert len([c for c in net.to("dispatch") if c["kw"]["headers"]["msgType"] == "IUOP_QUERY_FREIGHT"]) == 40


def test_sfg_non_json_and_timeout(net, setacct, reg):
    sfg_setup(setacct)
    net.route("/openapi/api/token", Resp(None, 502, text="<html>502</html>"))
    with pytest.raises(AdapterError) as e:
        reg["shunfeng"].quote(Q)
    assert "非 JSON" in str(e.value)
    net.reset_routes()
    net.route("/openapi/api/token", requests.ReadTimeout("timed out"))
    with pytest.raises(AdapterError) as e:
        reg["shunfeng"].quote(Q)
    assert "网络请求失败" in str(e.value)


def test_sfg_network_error_does_not_leak_app_secret_from_query_string(net, setacct, reg, store):
    """回归：requests 的异常文本会带上 URL（含 ?appKey=…&appSecret=…），必须脱敏。"""
    sfg_setup(setacct)
    leak = f"HTTPSConnectionPool(host='api-ifsp.sf.global'): Max retries exceeded with url: /openapi/api/token?appKey={AK}&appSecret={FAKE['app_secret']}"
    net.route("/openapi/api/token", requests.ConnectionError(leak))
    with pytest.raises(AdapterError) as e:
        reg["shunfeng"].quote(Q)
    assert FAKE["app_secret"] not in str(e.value) and "appSecret=***" in str(e.value)


def test_sfg_garbled_encrypted_response(net, setacct, reg):
    sfg_setup(setacct)
    sfg_routes(net, dispatch=lambda mt, c: Resp({"apiResultCode": "0", "apiResultData": "bm90LWVuY3J5cHRlZA=="}))
    with pytest.raises(AdapterError):
        reg["shunfeng"].test_login()


def test_sfg_login_mode_pending(setacct, reg):
    setacct("shunfeng", auth_mode="login", username="u", password="p")
    with pytest.raises(AdapterError) as e:
        reg["shunfeng"].quote(Q)
    assert e.value.kind == "pending"


# ---------------------------------------------------------------- 丰桥
def fq_setup(setacct, **kw):
    d = dict(auth_mode="api", api_kind="fengqiao", partner_id="FAKE-PARTNER", check_word="FAKE-CHECKWORD-SENTINEL", customer_code="C1", products="INT1,INT2")
    d.update(kw)
    setacct("shunfeng", **d)


def fq_routes(net, ok=True):
    net.route("/oauth2/accessToken", {"apiResultCode": "A1000", "apiResultData": json.dumps({"success": True, "accessToken": "FAKE-TOKEN-SENTINEL-abc123", "expiresIn": 7200})})
    net.route("/std/service", lambda c: {"apiResultCode": "A1000", "apiResultData": json.dumps(
        {"success": True, "msgData": {"discountedRate": "55.5", "currency": "CNY", "weight": "2"}})})


def test_fq_oauth_quote_success(net, setacct, reg):
    fq_setup(setacct)
    fq_routes(net)
    rows = reg["shunfeng"].quote(Q)
    assert [r["total"] for r in rows] == [55.5, 55.5] and net.count("accessToken") == 1
    form = net.to("/std/service")[0]["kw"]["data"]
    assert form["serviceCode"] == "COM_RECE_IUOP_QUERY_FREIGHT" and "accessToken" in form and "msgDigest" not in form


def test_fq_digest_mode_sends_msgdigest_not_token(net, setacct, reg):
    fq_setup(setacct, auth="digest")
    fq_routes(net)
    reg["shunfeng"].quote(Q)
    form = net.to("/std/service")[0]["kw"]["data"]
    assert form["msgDigest"] == oa.sf_digest(form["msgData"], form["timestamp"], "FAKE-CHECKWORD-SENTINEL") and "accessToken" not in form
    assert not net.to("accessToken")


def test_fq_digest_test_connection_is_manual(setacct, reg):
    fq_setup(setacct, auth="digest")
    with pytest.raises(AdapterError) as e:
        reg["shunfeng"].test_login()
    assert e.value.kind == "manual"


def test_fq_token_failure_login_failed(net, setacct, reg):
    fq_setup(setacct)
    net.route("/oauth2/accessToken", {"apiResultCode": "A1004", "apiErrorMsg": "校验码错误"})
    with pytest.raises(AdapterError) as e:
        reg["shunfeng"].quote(Q)
    assert e.value.kind == "login_failed" and "FAKE-CHECKWORD-SENTINEL" not in str(e.value)


def test_fq_requires_customer_code_and_products(setacct, reg):
    fq_setup(setacct, products="")
    with pytest.raises(AdapterError) as e:
        reg["shunfeng"].quote(Q)
    assert e.value.kind == "no_account"


def test_fq_partial_failure_and_total_failure(net, setacct, reg):
    fq_setup(setacct)
    net.route("/oauth2/accessToken", {"apiResultCode": "A1000", "apiResultData": json.dumps({"accessToken": "T", "expiresIn": 7200})})
    net.route("/std/service", [{"apiResultCode": "A1000", "apiResultData": json.dumps({"success": False, "errorCode": "X", "errorMsg": "不支持"})},
                               {"apiResultCode": "A1000", "apiResultData": json.dumps({"success": True, "msgData": {"totalFreight": "12"}})}])
    assert [r["total"] for r in reg["shunfeng"].quote(Q)] == [12.0]
    net.reset_routes()
    net.route("/oauth2/accessToken", {"apiResultCode": "A1000", "apiResultData": json.dumps({"accessToken": "T", "expiresIn": 7200})})
    net.route("/std/service", {"apiResultCode": "A1000", "apiResultData": json.dumps({"success": False, "errorCode": "X", "errorMsg": "不支持"})})
    with pytest.raises(AdapterError):
        reg["shunfeng"].quote(Q)


def test_fq_unparseable_result_data(net, setacct, reg):
    fq_setup(setacct)
    net.route("/oauth2/accessToken", {"apiResultCode": "A1000", "apiResultData": "{not json"})
    with pytest.raises(AdapterError):
        reg["shunfeng"].test_login()


# ---------------------------------------------------------------- 欣航（HTML）
def kt_html(rows):
    body = "".join(f'<dl class="ks_dl_table1">{"".join(f"<dd>{c}</dd>" for c in r)}</dl>' for r in rows)
    return f"<html><body><div>目的地：美国</div>{body}</body></html>"


def kt_route(net, html, status=200):
    net.route("price.jsp", Resp(None, 200, text="<html></html>"))
    net.route("WebPrice?action=list", Resp(None, status, text=html))


def test_xh_parse_rows(net, reg):
    kt_route(net, kt_html([["美国专线【推荐】", "1.5", "88.50", "人民币", "7-10天"], ["美国经济", "2", " 1,234.5 ", "人民币", ""]]))
    rows = reg["xinhang"].quote(Q)
    assert [(r["channel"], r["total"], r["currency"]) for r in rows] == [("美国专线", 88.5, "CNY"), ("美国经济", 1234.5, "CNY")]
    assert (rows[0]["days_min"], rows[0]["days_max"]) == (7, 10) and rows[0]["fees"][0]["value"] == "1.5"


def test_xh_request_form(net, reg):
    kt_route(net, kt_html([["A", "1", "10", "人民币", "5"]]))
    reg["xinhang"].quote(Query(country="DE", weight=0.5, postcode="10115", length=1, width=2, height=3))
    f = net.to("WebPrice")[0]["kw"]["data"]
    assert f["country"] == "DE" and f["rweight"] == "0.5" and f["post"] == "10115" and (f["len"], f["width"], f["height"]) == ("1", "2", "3")
    reg["xinhang"].quote(Q)
    f2 = net.to("WebPrice")[1]["kw"]["data"]
    assert f2["len"] == "" and f2["post"] == ""


def test_xh_no_account_needed_and_status(reg):
    s = reg["xinhang"].status()
    assert s["no_auth"] is True and s["mode_short"] == "公开网页" and s["fixed_mode"]


@pytest.mark.parametrize("cells", [["仅一列"], ["A", "1", "x", "人民币", "5"], ["A", "1", "", "人民币"], []])
def test_xh_malformed_rows_skipped(net, reg, cells):
    kt_route(net, kt_html([cells, ["OK", "1", "9.9", "人民币", "3"]]))
    assert [r["channel"] for r in reg["xinhang"].quote(Q)] == ["OK"]


def test_xh_non_cny_currency_kept(net, reg):
    kt_route(net, kt_html([["A", "1", "10", "USD", "5"]]))
    assert reg["xinhang"].quote(Q)[0]["currency"] == "USD"


def test_xh_empty_result_page_unsupported_country(net, reg):
    kt_route(net, "<html><body>目的地 不支持</body></html>")
    with pytest.raises(AdapterError) as e:
        reg["xinhang"].quote(Q)
    assert "没有返回报价" in str(e.value)


def test_xh_empty_page_without_hint_returns_empty(net, reg):
    kt_route(net, "<html><body>nothing</body></html>")
    assert reg["xinhang"].quote(Q) == []


@pytest.mark.parametrize("status", [403, 500, 502])
def test_xh_http_error(net, reg, status):
    kt_route(net, "x", status=status)
    with pytest.raises(AdapterError) as e:
        reg["xinhang"].quote(Q)
    assert str(status) in str(e.value)


def test_xh_timeout_and_garbage_html(net, reg):
    net.route("price.jsp", requests.ConnectTimeout("t"))
    with pytest.raises(AdapterError) as e:
        reg["xinhang"].quote(Q)
    assert "网络请求失败" in str(e.value)
    net.reset_routes()
    kt_route(net, "<<<>>>\x00<dl class='ks_dl_table1'><dd>")
    assert reg["xinhang"].quote(Q) == []


def test_xh_html_injection_text_is_inert(net, reg):
    kt_route(net, kt_html([["<script>alert(1)</script>A", "1", "10", "人民币", "5"]]))
    r = reg["xinhang"].quote(Q)
    assert r and "<" not in r[0]["channel"]   # 解析为纯文本（前端另有 esc 转义）


def test_xh_test_connection(net, reg):
    net.route("price.jsp", Resp(None, 200, text="ok"))
    assert "无需登录" in reg["xinhang"].test_login()
