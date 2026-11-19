"""FastAPI 全部路由：正常路径 + 非法输入。"""
import base64
import json

import pytest

from tests.suite.conftest import FAKE, SENTINELS
from tests.suite.test_adapters_ois import ois_setup

pytestmark = pytest.mark.unit
ALL = ["yuntu", "zhonghang", "xinhang", "huanyang", "yanwen", "shunfeng", "yutong"]


def test_ping_counts(client):
    from app import main as m
    n = m.ping_state["count"]
    assert client.get("/api/ping").json() == {"ok": True}
    assert m.ping_state["count"] == n + 1


def test_meta(client):
    from app import __version__
    j = client.get("/api/meta").json()
    assert j["version"] == __version__ and len(j["countries"]) >= 50
    assert {"code": "US", "name": "美国"} in j["countries"] or any(c["code"] == "US" for c in j["countries"])
    assert all(len(c["code"]) == 2 for c in j["countries"])


def test_platforms_listing_contract(client):
    ps = client.get("/api/platforms").json()
    assert [p["id"] for p in ps] == ALL
    for p in ps:
        assert {"id", "name", "mode", "mode_short", "state", "label", "enabled", "implemented"} <= set(p)
        assert 0 < len(p["mode_short"]) <= 14, p["id"]


def test_mode_short_labels_exact_per_platform(client, setacct):
    exp_api = {"yutong": "OIS 开放API", "huanyang": "OIS 开放API", "zhonghang": "OIS 开放API", "shunfeng": "sf.global API", "yanwen": "燕文开放API"}
    for pid, lab in exp_api.items():
        setacct(pid, auth_mode="api")
    got = {p["id"]: p["mode_short"] for p in client.get("/api/platforms").json()}
    assert {k: got[k] for k in exp_api} == exp_api
    assert got["xinhang"] == "公开网页"
    setacct("zhonghang", auth_mode="login")
    setacct("yuntu", auth_mode="login")
    got = {p["id"]: p["mode_short"] for p in client.get("/api/platforms").json()}
    assert got["zhonghang"] == "tms-saas 登录" and got["yuntu"] == "OMS2 登录"


def test_secrets_never_echoed_in_platforms(client, setacct):
    setacct("yutong", auth_mode="api", app_key="FAKE-APPKEY-0001", app_secret=FAKE["app_secret"])
    body = client.get("/api/platforms").text
    assert FAKE["app_secret"] not in body and "FAKE-APPKEY-0001" in body
    y = next(p for p in json.loads(body) if p["id"] == "yutong")
    f = next(f for f in y["api_fields"] if f["key"] == "app_secret") if "api_fields" in y else None
    if f:
        assert f["value"] == "" and f["saved"] is True


# ---- 保存账号 ----
def test_save_account_unknown_platform_404(client):
    assert client.put("/api/platforms/nope/account", json={"values": {}}).status_code == 404


def test_save_account_flow_and_trim_and_ignore_unknown_keys(client, store):
    r = client.put("/api/platforms/yutong/account", json={"values": {"app_key": "  K1  ", "app_secret": " S 1 ", "evil": "x", "__proto__": "y"}, "auth_mode": "api"})
    assert r.status_code == 200
    a = store.get_account("yutong")
    assert a["app_key"] == "K1" and a["app_secret"] == " S 1 " and "evil" not in a and a.get("auth_mode", "api") == "api"


def test_blank_secret_keeps_old_secret_but_blank_plain_field_clears(client, store):
    client.put("/api/platforms/yutong/account", json={"values": {"app_key": "K1", "app_secret": "SECRET-1"}, "auth_mode": "api"})
    client.put("/api/platforms/yutong/account", json={"values": {"app_key": "K2", "app_secret": ""}})
    a = store.get_account("yutong")
    assert a["app_secret"] == "SECRET-1" and a["app_key"] == "K2"
    client.put("/api/platforms/yutong/account", json={"values": {"app_key": ""}})
    assert store.get_account("yutong")["app_key"] == ""


@pytest.mark.parametrize("mode", ["", "oauth", "API", None.__class__.__name__, "login; drop"])
def test_invalid_auth_mode_400(client, mode):
    assert client.put("/api/platforms/yutong/account", json={"values": {}, "auth_mode": mode}).status_code == 400


def test_fixed_mode_platform_ignores_auth_mode(client):
    r = client.put("/api/platforms/xinhang/account", json={"values": {}, "auth_mode": "api"})
    assert r.status_code == 200 and r.json()["auth_mode"] == "login"


def test_save_account_validation_422(client):
    assert client.put("/api/platforms/yutong/account", json={"values": "str"}).status_code == 422
    assert client.put("/api/platforms/yutong/account", json={"values": {"app_key": 123}}).status_code == 422
    assert client.put("/api/platforms/yutong/account", content=b"not json", headers={"content-type": "application/json"}).status_code == 422


def test_enable_toggle_and_credential_change_clears_session(client, store):
    client.put("/api/platforms/yutong/account", json={"values": {"app_key": "K", "app_secret": "S"}, "auth_mode": "api"})
    store.save_session("yutong_api", {"token": "x"})
    client.put("/api/platforms/yutong/account", json={"values": {}, "enabled": False})
    assert store.load_session("yutong_api") is not None and store.get_account("yutong")["enabled"] is False
    client.put("/api/platforms/yutong/account", json={"values": {"app_key": "K2"}})
    assert store.load_session("yutong_api") is None


# ---- 测试连接 ----
def test_platform_test_unknown_404(client):
    assert client.post("/api/platforms/nope/test").status_code == 404


def test_platform_test_success_and_failure(client, net, setacct):
    ois_setup(net, setacct)
    j = client.post("/api/platforms/yutong/test").json()
    assert j["ok"] is True and j["status"]["state"] in ("ok", "ready")
    net.reset_routes()
    net.route("getAuth", {"body": {"ack": "false"}, "message": "bad"})
    setacct("zhonghang", auth_mode="api", app_key="k", app_secret=FAKE["app_secret"])
    j = client.post("/api/platforms/zhonghang/test").json()
    assert j["ok"] is False and j["kind"] == "login_failed" and FAKE["app_secret"] not in json.dumps(j)


def test_platform_test_unexpected_exception_is_reported_not_500(client, monkeypatch, setacct, reg):
    setacct("yutong", auth_mode="api", app_key="k", app_secret="s")
    monkeypatch.setattr(reg["yutong"].__class__, "test_login", lambda self: 1 / 0)
    r = client.post("/api/platforms/yutong/test")
    assert r.status_code == 200 and r.json()["ok"] is False and "ZeroDivisionError" in r.json()["message"]


# ---- 查询 ----
@pytest.mark.parametrize("body,code", [
    ({}, 422), ({"country": "US"}, 422), ({"weight": 1}, 422), ({"country": "US", "weight": "abc"}, 422),
    ({"country": "US", "weight": 0}, 400), ({"country": "US", "weight": -1}, 400), ({"country": "  ", "weight": 1}, 400),
    ({"country": "", "weight": 1}, 400), ({"country": "US", "weight": 1e9}, 422), ({"country": "US", "weight": 1, "length": -1}, 422),
    ({"country": "US", "weight": 1, "height": 1e6}, 422), ({"country": "X" * 40, "weight": 1}, 422),
    ({"country": "US", "weight": 1, "postcode": "9" * 100}, 422), ({"country": "US", "weight": 1, "platforms": "yutong"}, 422),
    ({"country": 12, "weight": 1}, 422)])
def test_quote_invalid_inputs(client, body, code):
    assert client.post("/api/quote", json=body).status_code == code


@pytest.mark.parametrize("raw", ['{"country":"US","weight":NaN}', '{"country":"US","weight":Infinity}', '{"country":"US","weight":1,"length":NaN}'])
def test_quote_nan_inf_rejected(client, raw):
    assert client.post("/api/quote", content=raw, headers={"content-type": "application/json"}).status_code == 422


def test_quote_normalizes_country_and_records_history(client, net, setacct):
    ois_setup(net, setacct)
    j = client.post("/api/quote", json={"country": " us ", "weight": 1.5, "platforms": ["yutong"]}).json()
    assert j["query"]["country"] == "US" and j["query"]["country_name"] == "美国" and j["id"] >= 1
    assert client.get("/api/history").json()[0]["id"] == j["id"]


def test_quote_unknown_platform_ids_ignored(client):
    j = client.post("/api/quote", json={"country": "US", "weight": 1, "platforms": ["nope", "../etc/passwd"]}).json()
    assert j["platforms"] == [] and j["rows"] == []


def test_quote_without_platforms_runs_all_and_reports_states(client, net, setacct):
    ois_setup(net, setacct)
    net.route("price.jsp", {"x": 1})
    j = client.post("/api/quote", json={"country": "US", "weight": 1}).json()
    st = {p["id"]: p["state"] for p in j["platforms"]}
    assert st["yutong"] == "ok" and st["yuntu"] == "no_account" and st["yanwen"] in ("no_account", "pending")
    assert st["xinhang"] in ("ok", "error")      # 公开网站：无打桩时被拦截成 error，不影响其他平台


def test_quote_zero_dims_treated_as_absent(client, net, setacct):
    ois_setup(net, setacct)
    client.post("/api/quote", json={"country": "US", "weight": 1, "length": 0, "width": 5, "height": 5, "platforms": ["yutong"]})
    body = json.loads(net.to("queryQuoteList")[0]["kw"]["data"]["body1"])
    assert "pieces" not in body


def test_disabled_platform_skipped(client, net, setacct):
    ois_setup(net, setacct, enabled=False)
    j = client.post("/api/quote", json={"country": "US", "weight": 1, "platforms": ["yutong"]}).json()
    assert j["platforms"][0]["state"] == "skipped" and not net.calls


# ---- 历史 ----
def make_hist(client, net, setacct):
    ois_setup(net, setacct)
    return client.post("/api/quote", json={"country": "US", "weight": 2, "platforms": ["yutong"]}).json()["id"]


def test_history_crud(client, net, setacct):
    h1 = make_hist(client, net, setacct)
    h2 = make_hist(client, net, setacct)
    lst = client.get("/api/history").json()
    assert [x["id"] for x in lst] == [h2, h1] and lst[0]["min_total"] == 123.45 and lst[0]["count"] == 1
    assert client.get(f"/api/history/{h1}").json()["rows"][0]["channel"] == "美国专线小包"
    assert client.delete(f"/api/history/{h1}").json() == {"ok": True}
    assert client.get(f"/api/history/{h1}").status_code == 404
    assert client.delete("/api/history").json() == {"ok": True}
    assert client.get("/api/history").json() == []


@pytest.mark.parametrize("path", ["/api/history/99999", "/api/history/99999/csv"])
def test_history_missing_404(client, path):
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("hid", ["abc", "1.5", "-", "1;drop"])
def test_history_non_integer_id_422(client, hid):
    assert client.get(f"/api/history/{hid}").status_code == 422
    assert client.delete(f"/api/history/{hid}").status_code == 422


def test_delete_missing_history_is_idempotent(client):
    assert client.delete("/api/history/424242").status_code == 200


def test_history_with_sqlite_injection_like_values_is_safe(client, net, setacct, store):
    ois_setup(net, setacct)
    client.post("/api/quote", json={"country": "US", "weight": 1, "postcode": "'; DROP TABLE history;--", "platforms": ["yutong"]})
    assert len(client.get("/api/history").json()) == 1


# ---- 账号导入导出路由 ----
def test_template_and_export_routes(client, setacct):
    r = client.get("/api/accounts/template")
    assert r.status_code == 200 and r.content[:2] == b"PK" and "no-store" in r.headers["cache-control"]
    assert "attachment" in r.headers["content-disposition"] and "UTF-8''" in r.headers["content-disposition"]
    r = client.get("/api/accounts/template?fmt=csv")
    assert r.content.startswith("\ufeff平台".encode()) and "csv" in r.headers["content-type"]
    assert client.get("/api/accounts/template?fmt=../../x").status_code == 200       # 非法 fmt 回退 xlsx，不会读任意文件


def test_export_secrets_param_validation(client):
    assert client.get("/api/accounts/export?secrets=all").status_code == 400
    assert client.get("/api/accounts/export?secrets=plain").status_code == 400
    assert client.get("/api/accounts/export?secrets=mask").status_code == 200


def b64(b):
    return base64.b64encode(b).decode()


@pytest.mark.parametrize("payload,filename", [("%%%not-base64%%%", "a.csv"), ("", "a.csv"), (b64(b"\x00\x01\x02garbage"), "a.xlsx"),
                                              (b64(b"PK\x03\x04junk"), "a.xlsx"), (b64(b"x"), "a.xls")])
def test_import_preview_bad_files_are_400_not_500(client, payload, filename):
    r = client.post("/api/accounts/import/preview", json={"filename": filename, "data": payload})
    assert r.status_code == 400 and "读取文件失败" in r.json()["detail"]


def test_import_preview_size_limit(client):
    r = client.post("/api/accounts/import/preview", json={"filename": "big.csv", "data": "A" * (15 * 1024 * 1024)})
    assert r.status_code == 422


def test_import_apply_unknown_batch_400(client):
    r = client.post("/api/accounts/import/apply", json={"batch": "deadbeef"})
    assert r.status_code == 400 and "过期" in r.json()["detail"]
    assert client.post("/api/accounts/import/apply", json={}).status_code == 422


# ---- 静态与杂项 ----
def test_index_and_static(client):
    r = client.get("/")
    assert r.status_code == 200 and "货代询价助手" in r.text and r.headers["cache-control"] == "no-store"
    assert client.get("/static/app.js").status_code == 200 and client.get("/static/app.css").status_code == 200


@pytest.mark.parametrize("path", ["/static/../main.py", "/static/..%2fmain.py", "/static/%2e%2e/store.py", "/static/../../config.json"])
def test_static_path_traversal_blocked(client, path):
    r = client.get(path)
    assert r.status_code in (404, 400) and "class Store" not in r.text


def test_unknown_route_and_wrong_method(client):
    assert client.get("/api/nope").status_code == 404
    assert client.post("/api/platforms").status_code == 405
    assert client.put("/api/quote").status_code == 405
