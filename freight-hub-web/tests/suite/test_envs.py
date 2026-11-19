"""测试环境 / 正式环境：每个开放平台两套凭据分开存，切换不丢；测试环境的报价打标且不计入全场最低。"""
import json

import pytest

from app import rowcheck
from app.adapters.base import AdapterError, Query
from .conftest import Resp, ois_ok_routes, set_acct
from .test_adapters_other import YW_OK

Q = Query(country="US", country_name="美国", weight=1.0)
OPEN_PLATFORMS = ["yuntu", "zhonghang", "huanyang", "yanwen", "shunfeng", "yutong"]


def put(client, pid, **body):
    r = client.put(f"/api/platforms/{pid}/account", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_every_open_platform_offers_both_envs_and_xinhang_only_prod(reg):
    for pid in OPEN_PLATFORMS:
        s = reg[pid].status()
        assert [e["value"] for e in s["envs"]] == ["prod", "sandbox"] and not s["env_only"], pid
        assert s["env"] == "prod" and s["env_tag"] == ""
    x = reg["xinhang"].status()
    assert x["env_only"] and [e["value"] for e in x["envs"]] == ["prod"] and "没有测试环境" in x["env_note"]


def test_switching_env_keeps_both_credential_sets(client, store, reg):
    put(client, "yanwen", values={"user_id": "100000", "api_token": "TEST-TOKEN"}, env="sandbox", auth_mode="api")
    put(client, "yanwen", values={"user_id": "PROD-ID", "api_token": "PROD-TOKEN"}, env="prod", auth_mode="api")
    raw = store.get_account("yanwen")
    assert raw["user_id"] == "PROD-ID" and raw["user_id@sandbox"] == "100000" and raw["api_token@sandbox"] == "TEST-TOKEN" and raw["env"] == "prod"
    y = reg["yanwen"]
    assert y.account()["user_id"] == "PROD-ID"
    put(client, "yanwen", values={}, env="sandbox")                  # 只切换环境，不填任何字段
    assert y.account()["user_id"] == "100000" and y.account()["api_token"] == "TEST-TOKEN" and y.env() == "sandbox"
    put(client, "yanwen", values={}, env="prod")
    assert y.account()["user_id"] == "PROD-ID" and y.account()["api_token"] == "PROD-TOKEN"
    s = y.status()
    assert {e["value"]: e["configured"] for e in s["envs"]} == {"prod": True, "sandbox": True}


def test_blank_secret_in_one_env_does_not_touch_other_env(client, store, reg):
    put(client, "yanwen", values={"user_id": "A", "api_token": "TA"}, env="prod", auth_mode="api")
    put(client, "yanwen", values={"user_id": "B", "api_token": "TB"}, env="sandbox")
    put(client, "yanwen", values={"user_id": "B2", "api_token": ""}, env="sandbox")      # 密码框留空 = 不修改
    raw = store.get_account("yanwen")
    assert raw["api_token@sandbox"] == "TB" and raw["user_id@sandbox"] == "B2" and raw["api_token"] == "TA" and raw["user_id"] == "A"


def test_legacy_single_set_with_env_sandbox_is_treated_as_test_creds(client, store, reg):
    set_acct("yanwen", auth_mode="api", user_id="100000", api_token="T", env="sandbox")      # 旧版存法
    y = reg["yanwen"]
    assert y.env() == "sandbox" and y.has_account()
    prod_view = y.env_view(store.get_account("yanwen"), "prod")
    assert not prod_view.get("user_id")
    put(client, "yanwen", values={}, env="prod")                      # 切到正式：旧凭据迁到测试槽，正式为空
    raw = store.get_account("yanwen")
    assert raw["user_id@sandbox"] == "100000" and not raw.get("user_id")
    assert not y.has_account() and y.status()["state"] == "no_account"
    put(client, "yanwen", values={}, env="sandbox")
    assert y.account()["user_id"] == "100000" and y.has_account()


def test_yanwen_test_env_query_marks_rows_and_prod_is_missing_config(client, net, reg):
    net.reset_routes()
    net.route("calc.list", YW_OK)
    set_acct("yanwen", auth_mode="api", user_id="100000", api_token="TOK", env="sandbox")
    rows = reg["yanwen"].quote(Q)
    c = net.to("calc.list")[0]
    assert c["url"].startswith("https://open-fat.yw56.com.cn")
    assert rows and all(r["env"] == "sandbox" and "测试环境数据" in r["remark"] for r in rows)
    assert all(rowcheck.excluded_reason(r, 1.0) for r in rows)        # 不计入全场最低
    put(client, "yanwen", values={}, env="prod")
    with pytest.raises(AdapterError) as e:
        reg["yanwen"].quote(Q)
    assert e.value.kind == "no_account"
    put(client, "yanwen", values={"user_id": "9999", "api_token": "PT"}, env="prod")
    net.calls.clear() if hasattr(net, "calls") else None
    reg["yanwen"].quote(Q)
    assert net.to("calc.list")[-1]["url"].startswith("https://open.yw56.com.cn")
    rows = reg["yanwen"].quote(Q)
    assert all("env" not in r and "测试环境数据" not in (r.get("remark") or "") for r in rows)


def test_ois_test_env_requires_own_base_and_never_falls_back_to_prod(client, net, reg):
    ois_ok_routes(net)
    put(client, "yutong", values={"app_key": "PK", "app_secret": "PS"}, env="prod", auth_mode="api")
    put(client, "yutong", values={"app_key": "TK", "app_secret": "TS"}, env="sandbox")
    y = reg["yutong"]
    assert y.env() == "sandbox" and not y.has_account()                   # 测试环境没填接口域名 → 配置缺失
    with pytest.raises(AdapterError) as e:
        y.quote(Q)
    assert e.value.kind == "no_account" and not net.calls_to("openapi.intelink.net.cn") if hasattr(net, "calls_to") else True
    put(client, "yutong", values={"api_base": "https://ois-test.example.com/pos-web"}, env="sandbox")
    assert y.has_account()
    rows = y.quote(Q)
    assert rows and rows[0]["env"] == "sandbox"
    assert all("ois-test.example.com" in c["url"] for c in net.calls)
    put(client, "yutong", values={}, env="prod")
    assert y.account()["app_key"] == "PK" and y.has_account()


def test_shunfeng_env_sets_are_independent_and_common_fields_shared(client, store, reg):
    put(client, "shunfeng", values={"app_key": "PK", "app_secret": "PS", "aes_key": "A" * 43, "customer_code": "ICRM-CNE0103T471",
                                    "sender_post": "518052"}, env="prod", auth_mode="api")
    put(client, "shunfeng", values={"app_key": "SK", "app_secret": "SS", "aes_key": "B" * 43, "customer_code": "SBX-CODE"}, env="sandbox")
    s = reg["shunfeng"]
    raw = store.get_account("shunfeng")
    assert raw["customer_code"] == "ICRM-CNE0103T471" and raw["customer_code@sandbox"] == "SBX-CODE" and raw["sender_post"] == "518052"
    assert s.account()["customer_code"] == "SBX-CODE" and s.account()["sender_post"] == "518052"
    put(client, "shunfeng", values={}, env="prod")
    assert s.account()["customer_code"] == "ICRM-CNE0103T471" and s._env() == "prod"


def test_unknown_env_and_prod_only_platform_rejected(client):
    r = client.put("/api/platforms/xinhang/account", json={"values": {}, "env": "sandbox"})
    assert r.status_code == 400 and "只有正式环境" in r.text
    r = client.put("/api/platforms/yanwen/account", json={"values": {}, "env": "staging"})
    assert r.status_code == 400


def test_secrets_of_both_envs_never_in_status_json_after_save(client):
    put(client, "yanwen", values={"user_id": "1", "api_token": "SECRET-PROD-XYZ"}, env="prod", auth_mode="api")
    j = json.dumps(put(client, "yanwen", values={"user_id": "2", "api_token": "SECRET-TEST-XYZ"}, env="sandbox"), ensure_ascii=False)
    assert "SECRET-PROD-XYZ" not in j and "SECRET-TEST-XYZ" not in j


def test_import_export_roundtrip_keeps_env_sets(client, store, reg):
    set_acct("yanwen", auth_mode="api", **{"user_id": "P", "api_token": "PT", "user_id@sandbox": "100000", "api_token@sandbox": "TT"}, env="prod")
    from app import importer
    rows = importer.export_rows(reg, store, secrets="plain") if "secrets" in importer.export_rows.__code__.co_varnames else importer.export_rows(reg, store)
    yw = [r for r in rows if r[0] == "燕文"]
    assert len(yw) >= 2 and any("env=sandbox" in " ".join(map(str, r)) for r in yw)


def test_history_min_total_skips_test_env_rows():
    rows = rowcheck.mark([{"platform": "yanwen", "channel": "x", "total": 3.2, "currency": "CNY", "remark": "【测试环境数据，非真实报价】", "env": "sandbox"},
                          {"platform": "a", "channel": "y", "total": 150.0, "currency": "CNY", "remark": ""}], 1.0)
    assert rowcheck.lowest(rows) == 150.0


def test_shunfeng_legacy_sb_keys_become_test_env_creds(store, reg):
    """旧版顺丰把沙箱密钥存成 sb_app_key 等：读取时并入测试环境，正式密钥不受影响。"""
    store.set_account("shunfeng", {"app_key": "PK", "app_secret": "PS", "aes_key": "A" * 43, "customer_code": "PC",
                                   "sb_app_key": "SK", "sb_app_secret": "SS", "sb_aes_key": "B" * 43,
                                   "sb_customer_code": "SC", "env": "sandbox"})
    s = reg["shunfeng"]
    assert s.account()["app_key"] == "SK" and s.account()["customer_code"] == "SC" and s.env() == "sandbox"
    v = s.env_view(store.get_account("shunfeng"), "prod")
    assert v["app_key"] == "PK" and v["customer_code"] == "PC"
