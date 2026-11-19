"""1.5.1 稳定性：默认 API 授权、连接重试、错误分类、单平台熔断、不误锁账号。"""
import asyncio

import pytest
import requests

from app import main as m
from app.adapters import net as fhnet
from app.adapters.base import AdapterError, Query
from .conftest import Resp, ois_ok_routes, set_acct

Q = Query(country="NZ", country_name="新西兰", weight=0.3, postcode="0932")


def test_default_mode_is_api_for_every_platform_without_saved_choice(reg):
    for pid, a in reg.items():
        if a.fixed_mode:
            continue
        assert a.auth_mode() == "api", pid


def test_fallback_to_login_only_when_no_api_creds_but_login_creds(reg):
    set_acct("zhonghang", username="u", password="p")
    z = reg["zhonghang"]
    assert z.auth_mode() == "login" and z.mode_fallback and "暂用" in z.status()["auth_mode_label"]
    set_acct("zhonghang", username="u", password="p", app_key="k", app_secret="s")
    assert z.auth_mode() == "api" and not z.mode_fallback


def test_explicit_choice_beats_default(reg):
    set_acct("zhonghang", username="u", password="p", app_key="k", app_secret="s", auth_mode="login")
    assert reg["zhonghang"].auth_mode() == "login"


def test_yuntu_login_creds_only_in_cloud_is_api_not_desktop_blocked(reg, monkeypatch):
    monkeypatch.setenv("FREIGHT_HUB_MODE", "cloud")
    set_acct("yuntu", username="u", password="p")
    y = reg["yuntu"]
    assert y.auth_mode() == "api" and not y.desktop_blocked


def test_net_retries_connect_errors_with_fresh_connection(monkeypatch):
    calls = []

    def fake(method, url, timeout=None, **kw):
        calls.append(timeout)
        if len(calls) < 3:
            raise requests.ConnectTimeout("boom")
        return Resp({"ok": 1})
    monkeypatch.setattr(requests, "request", fake)
    monkeypatch.setattr(fhnet.time, "sleep", lambda s: None)
    assert fhnet.post("http://x/y", data={}).json() == {"ok": 1}
    assert len(calls) == 3 and all(t == (fhnet.CONNECT_TIMEOUT, 40) for t in calls)


def test_net_does_not_retry_read_timeout(monkeypatch):
    n = []

    def fake(method, url, **kw):
        n.append(1)
        raise requests.ReadTimeout("slow")
    monkeypatch.setattr(requests, "request", fake)
    with pytest.raises(requests.ReadTimeout):
        fhnet.post("http://x")
    assert len(n) == 1


def test_net_gives_up_after_attempts(monkeypatch):
    n = []

    def fake(method, url, **kw):
        n.append(1)
        raise requests.ConnectionError("down")
    monkeypatch.setattr(requests, "request", fake)
    monkeypatch.setattr(fhnet.time, "sleep", lambda s: None)
    with pytest.raises(requests.ConnectionError):
        fhnet.get("http://x")
    assert len(n) == fhnet.ATTEMPTS


def test_net_backoff_once_on_503(monkeypatch):
    seq = [Resp({}, 503), Resp({"ok": 1})]
    monkeypatch.setattr(requests, "request", lambda *a, **k: seq.pop(0))
    monkeypatch.setattr(fhnet.time, "sleep", lambda s: None)
    assert fhnet.get("http://x").status_code == 200


def test_ois_expired_token_refreshes_once(net, reg):
    set_acct("yutong", app_key="k", app_secret="s")
    ois_ok_routes(net)
    # 先放一个已过期的缓存 token，应当重新 getAuth 而不是用旧的
    m.store.save_session("yutong_api", {"kind": "ois", "key": "k", "base": "https://openapi.intelink.net.cn", "token": "OLD", "exp": 1})
    rows = reg["yutong"].quote(Q)
    assert rows and net.count("/ois/order/getAuth") == 1


def test_network_failure_is_categorised_and_does_not_lock_account(net, reg, monkeypatch):
    monkeypatch.setattr(fhnet.time, "sleep", lambda s: None)
    set_acct("yutong", app_key="k", app_secret="s")
    net.route("/ois/order/getAuth", requests.ConnectTimeout("t"))
    info, rows = asyncio.run(m.run_platform(reg["yutong"], Q))
    assert info["category"] == "网络" and info["message"].startswith("【网络】") and rows == []
    assert not m.store.login_locked("yutong")        # 网络问题不是授权失败，不能触发"登录锁"


def test_auth_failure_locks_and_is_categorised(net, reg):
    set_acct("yutong", app_key="k", app_secret="bad")
    net.route("/ois/order/getAuth", {"res": "x", "body": {"ack": "false"}, "message": "密钥错误"})
    info, _ = asyncio.run(m.run_platform(reg["yutong"], Q))
    assert info["category"] == "授权" and m.store.login_locked("yutong")
    n = net.count("/ois/order/getAuth")
    info2, _ = asyncio.run(m.run_platform(reg["yutong"], Q))      # 登录一次原则：失败后不再自动重试
    assert net.count("/ois/order/getAuth") == n and info2["category"] == "授权"


def test_missing_config_category(reg):
    info, _ = asyncio.run(m.run_platform(reg["huanyang"], Q))
    assert info["state"] == "no_account" and info["category"] == "配置缺失"


def test_circuit_breaker_isolates_failing_platform(net, reg, monkeypatch):
    monkeypatch.setattr(fhnet.time, "sleep", lambda s: None)
    m._BREAKER.clear()
    set_acct("yutong", app_key="k", app_secret="s")
    set_acct("huanyang", app_key="k", app_secret="s")
    net.route("openapi.intelink.net.cn", requests.ConnectTimeout("t"))
    ois_ok_routes(net)    # huanyang 的 ekttech 路由正常
    for _ in range(m.BREAK_AFTER):
        asyncio.run(m.run_platform(reg["yutong"], Q))
    n = len(net.calls)
    info, _ = asyncio.run(m.run_platform(reg["yutong"], Q))
    assert "已暂停" in info["message"] and len(net.calls) == n          # 熔断期内不再发请求
    info_h, rows_h = asyncio.run(m.run_platform(reg["huanyang"], Q))     # 别的平台完全不受影响
    assert info_h["state"] == "ok" and rows_h
    m._BREAKER.clear()


def test_platform_timeout_fits_vercel_maxduration():
    import json, pathlib
    vj = json.loads((pathlib.Path(m.__file__).resolve().parents[1] / "vercel.json").read_text())
    assert vj["functions"]["app/main.py"]["maxDuration"] >= 60
    assert fhnet.ATTEMPTS * fhnet.CONNECT_TIMEOUT < 25
