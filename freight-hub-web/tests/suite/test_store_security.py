"""配置存储与安全：600 权限、无明文泄露（日志/导出/CSV/状态/历史）、脱敏、损坏配置保护、登录一次锁。"""
import json
import os
import sqlite3
import stat

import pytest
import requests

from app.adapters.base import AdapterError
from app.redact import redact
from tests.suite.conftest import DATA_DIR, FAKE, SENTINELS
from tests.suite.test_adapters_ois import ois_setup

pytestmark = pytest.mark.unit
posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX 权限位")


def mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


@posix_only
def test_config_file_is_0600_after_every_write(store):
    for i in range(3):
        store.set_account("yutong", {"app_key": f"k{i}", "app_secret": FAKE["app_secret"]})
        assert mode(store.cfg_path) == 0o600
    assert not list(store.dir.glob("*.tmp")), "不应遗留临时文件"


@posix_only
def test_tmp_file_created_private_without_umask_window(store, monkeypatch):
    os.umask(0o022)
    seen = []
    orig = os.replace

    def spy(src, dst):
        seen.append(mode(src))
        return orig(src, dst)
    monkeypatch.setattr(os, "replace", spy)
    store.set_account("yutong", {"app_secret": "S"})
    assert seen == [0o600], "临时文件在 rename 之前就必须是 0600"


@posix_only
def test_sessions_and_lock_files_0600(store):
    store.save_session("yutong_api", {"token": FAKE["token"]})
    store.lock_login("yutong")
    assert mode(store.dir / "sessions" / "yutong_api.json") == 0o600 and mode(store.dir / "login_lock.json") == 0o600


def test_corrupt_config_is_backed_up_not_silently_destroyed(store):
    store.set_account("yutong", {"app_key": "KEEP", "app_secret": FAKE["app_secret"]})
    good = store.cfg_path.read_bytes()
    store.cfg_path.write_bytes(good[: len(good) // 2])     # 截断 → 损坏
    assert store.get_account("yutong") == {}
    store.set_account("zhonghang", {"app_key": "new"})     # 触发覆盖保存
    backups = list(store.dir.glob("config.json.corrupt-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == good[: len(good) // 2]
    if os.name != "nt":
        assert mode(backups[0]) == 0o600


@pytest.mark.parametrize("content", ["[]", "null", '"str"', '{"accounts": []}', "", "{"])
def test_malformed_config_shapes_do_not_crash(store, content):
    store.cfg_path.write_text(content, encoding="utf-8")
    assert store.get_account("yutong") == {}
    store.set_account("yutong", {"app_key": "k"})
    assert store.get_account("yutong") == {"app_key": "k"}


def test_missing_config_file_recreated_empty(store):
    store.cfg_path.unlink()
    assert store.get_account("x") == {}


def test_set_account_ignores_none_and_keeps_other_fields(store):
    store.set_account("p", {"a": "1", "b": "2"})
    store.set_account("p", {"a": None, "b": "3"})
    assert store.get_account("p") == {"a": "1", "b": "3"}


def test_concurrent_set_account_no_lost_updates(store):
    import threading
    ts = [threading.Thread(target=lambda i=i: store.set_account("p", {f"k{i}": str(i)})) for i in range(30)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(store.get_account("p")) == 30


# ---------------- 脱敏 ----------------
@pytest.mark.parametrize("text,leak", [
    ("url: /token?appKey=A&appSecret=SUPERSECRET&x=1", "SUPERSECRET"), ("?password=hunter2&u=1", "hunter2"), ("token=abcdef sign=123456", "abcdef"),
    ("Max retries /oauth2?secret=zzzz1234)", "zzzz1234"), ("APPSECRET=UPPER", "UPPER"), ("checkWord=CW123&msgDigest=DIGEST", "CW123")])
def test_redact_query_string_secrets(text, leak):
    assert leak not in redact(text)


def test_redact_known_secret_values_and_short_values():
    assert "VERYSECRETVALUE" not in redact("boom VERYSECRETVALUE boom", ["VERYSECRETVALUE"])
    assert redact("abc", ["ab"]) == "abc"      # 太短的值不替换，避免误伤


def test_last_error_in_status_is_redacted(store, reg, setacct):
    setacct("yutong", auth_mode="api", app_key="k", app_secret=FAKE["app_secret"])
    store.mark("yutong", False, f"fail url?appSecret={FAKE['app_secret']}")
    assert FAKE["app_secret"] not in json.dumps(reg["yutong"].status())


def test_network_error_message_scrubbed_end_to_end(client, net, setacct):
    setacct("shunfeng", auth_mode="api", api_kind="sfglobal", app_key="FAKE-SFG-APPKEY", app_secret=FAKE["app_secret"], aes_key="A" * 43)
    net.route("/openapi/api/token", requests.ConnectionError(f"Max retries url: /openapi/api/token?appKey=FAKE-SFG-APPKEY&appSecret={FAKE['app_secret']}"))
    j = client.post("/api/quote", json={"country": "US", "weight": 1, "platforms": ["shunfeng"]}).json()
    assert FAKE["app_secret"] not in json.dumps(j, ensure_ascii=False)
    assert FAKE["app_secret"] not in json.dumps(client.get("/api/platforms").json())


def test_unexpected_exception_text_with_secret_is_scrubbed(client, setacct, reg, monkeypatch):
    setacct("yutong", auth_mode="api", app_key="k", app_secret=FAKE["app_secret"])

    def boom(self, q):
        raise RuntimeError(f"bad value {FAKE['app_secret']}")
    monkeypatch.setattr(reg["yutong"].__class__, "api_quote", boom)
    j = client.post("/api/quote", json={"country": "US", "weight": 1, "platforms": ["yutong"]}).json()
    assert FAKE["app_secret"] not in json.dumps(j, ensure_ascii=False)
    assert FAKE["app_secret"] not in json.dumps(client.get("/api/platforms").json())


# ---------------- 全链路无明文泄露 ----------------
def test_no_plaintext_leak_anywhere(client, net, setacct, store, capsys, caplog):
    caplog.set_level("DEBUG")
    ois_setup(net, setacct)
    setacct("zhonghang", auth_mode="login", username=FAKE["username"], password=FAKE["password"])
    net.route("tms-saas", {"x": 1})
    outputs = []
    outputs.append(client.get("/api/platforms").text)
    q = client.post("/api/quote", json={"country": "US", "weight": 1}).json()
    outputs.append(json.dumps(q, ensure_ascii=False))
    outputs.append(client.get("/api/history").text)
    outputs.append(client.get(f"/api/history/{q['id']}").text)
    outputs.append(client.get(f"/api/history/{q['id']}/csv").text)
    for fmt in ("csv",):
        for sec in ("none", "mask"):
            outputs.append(client.get(f"/api/accounts/export?fmt={fmt}&secrets={sec}").content.decode("utf-8-sig"))
    xl = client.get("/api/accounts/export?secrets=none").content
    outputs.append(xl.decode("latin1"))
    import zipfile, io
    z = zipfile.ZipFile(io.BytesIO(xl))
    outputs += [z.read(n).decode("utf-8", "ignore") for n in z.namelist()]
    outputs.append(client.post("/api/platforms/yutong/test").text)
    cap = capsys.readouterr()
    outputs += [cap.out, cap.err, caplog.text]
    con = sqlite3.connect(store.dir / "history.db")
    outputs.append(json.dumps(con.execute("select * from history").fetchall(), ensure_ascii=False))
    blob = "\n".join(outputs)
    for s in SENTINELS + [FAKE["password"]]:
        assert s not in blob, f"泄露了 {s[:12]}…"
    # 导出「打码」不能还原
    mask = client.get("/api/accounts/export?fmt=csv&secrets=mask").content.decode("utf-8-sig")
    assert "FAKE-APPSECRET-SENTINEL-9f8e7d6c" not in mask and "****" in mask


def test_export_none_has_empty_secret_cells_and_keeps_plain_fields(client, setacct):
    setacct("yutong", auth_mode="api", app_key="FAKE-APPKEY-0001", app_secret=FAKE["app_secret"])
    t = client.get("/api/accounts/export?fmt=csv&secrets=none").content.decode("utf-8-sig")
    assert "FAKE-APPKEY-0001" in t and FAKE["app_secret"] not in t


def test_history_never_contains_account_fields(client, net, setacct, store):
    ois_setup(net, setacct)
    client.post("/api/quote", json={"country": "US", "weight": 1, "platforms": ["yutong"]})
    raw = open(store.dir / "history.db", "rb").read()
    assert FAKE["app_secret"].encode() not in raw and b"FAKE-APPKEY-0001" not in raw


# ---------------- 登录一次锁 ----------------
def test_failed_login_locks_and_later_queries_do_not_touch_network(client, net, setacct, store):
    setacct("zhonghang", auth_mode="api", app_key="k", app_secret=FAKE["app_secret"])
    net.route("getAuth", {"body": {"ack": "false"}, "message": "密钥错误"})
    r1 = client.post("/api/quote", json={"country": "US", "weight": 1, "platforms": ["zhonghang"]}).json()
    assert r1["platforms"][0]["state"] == "login_failed" and net.count("getAuth") == 1
    for _ in range(5):
        r = client.post("/api/quote", json={"country": "US", "weight": 1, "platforms": ["zhonghang"]}).json()
        assert r["platforms"][0]["state"] == "login_failed" and "暂停自动重试" in r["platforms"][0]["message"]
    assert net.count("getAuth") == 1, "登录失败后绝不能反复尝试登录（会锁号）"


def test_lock_survives_restart_and_clears_on_credential_change(client, net, setacct, store):
    setacct("zhonghang", auth_mode="api", app_key="k", app_secret=FAKE["app_secret"])
    net.route("getAuth", {"body": {"ack": "false"}, "message": "x"})
    client.post("/api/quote", json={"country": "US", "weight": 1, "platforms": ["zhonghang"]})
    from app.store import Store
    assert Store().login_locked("zhonghang")                     # 新进程读取同一目录依然锁定
    client.put("/api/platforms/zhonghang/account", json={"values": {"app_key": "k2"}})
    assert not store.login_locked("zhonghang")
    client.post("/api/quote", json={"country": "US", "weight": 1, "platforms": ["zhonghang"]})
    assert net.count("getAuth") == 2


def test_enable_toggle_does_not_clear_lock(client, store, setacct):
    setacct("zhonghang", auth_mode="api", app_key="k", app_secret="s")
    store.lock_login("zhonghang")
    client.put("/api/platforms/zhonghang/account", json={"values": {}, "enabled": False})
    assert store.login_locked("zhonghang")


def test_manual_test_connection_bypasses_lock_once_and_success_unlocks(client, net, setacct, store):
    ois_setup(net, setacct)
    store.lock_login("yutong")
    j = client.post("/api/platforms/yutong/test").json()
    assert j["ok"] is True and not store.login_locked("yutong")


def test_manual_test_failure_keeps_lock(client, net, setacct, store):
    setacct("zhonghang", auth_mode="api", app_key="k", app_secret="s")
    net.route("getAuth", {"body": {"ack": "false"}, "message": "x"})
    client.post("/api/platforms/zhonghang/test")
    assert store.login_locked("zhonghang") and net.count("getAuth") == 1


def test_network_timeouts_do_not_lock(client, net, setacct, store):
    setacct("zhonghang", auth_mode="api", app_key="k", app_secret="s")
    net.route("getAuth", requests.ReadTimeout("t"))
    client.post("/api/quote", json={"country": "US", "weight": 1, "platforms": ["zhonghang"]})
    assert not store.login_locked("zhonghang")
    client.post("/api/quote", json={"country": "US", "weight": 1, "platforms": ["zhonghang"]})
    assert net.count("getAuth") == 2   # 超时不是密码错误：允许重试


def test_lock_is_per_platform(client, net, setacct, store):
    setacct("zhonghang", auth_mode="api", app_key="k", app_secret="s")
    ois_setup(net, setacct)
    store.lock_login("zhonghang")
    j = client.post("/api/quote", json={"country": "US", "weight": 1, "platforms": ["zhonghang", "yutong"]}).json()
    st = {p["id"]: p["state"] for p in j["platforms"]}
    assert st["zhonghang"] == "login_failed" and st["yutong"] == "ok"
