"""1.4.0 服务器模式：用户系统、会话、CSRF、角色权限（app/server.py）。
用一个最小 FastAPI 应用 + server.install() 在进程内测试中间件与策略表（真实应用的集成见 test_web_e2e.py）。"""
import os
import stat
import time
import types

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app import server

pytestmark = pytest.mark.unit
PW = "Admin-Pass-123"


def build(tmp_path, monkeypatch, **env):
    for k in ("FREIGHT_HUB_ADMIN_PASSWORD", "FREIGHT_HUB_CERT", "FREIGHT_HUB_HTTPS", "FREIGHT_HUB_TRUST_PROXY", "FREIGHT_HUB_ALLOWED_ORIGINS", "FREIGHT_HUB_SESSION_HOURS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("FREIGHT_HUB_ADMIN_PASSWORD", PW)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    app = FastAPI()
    calls = []

    @app.get("/")
    def index():
        return {"page": "index"}

    @app.get("/api/meta")
    def meta(request: Request):
        return {"who": request.state.user["name"]}

    @app.get("/api/platforms")
    def plats():
        return []

    @app.put("/api/platforms/{pid}/account")
    def save_acc(pid: str):
        calls.append(("save", pid))
        return {"ok": True}

    @app.post("/api/platforms/{pid}/test")
    def test_conn(pid: str):
        calls.append(("test", pid))
        return {"ok": True}

    @app.post("/api/quote")
    def quote():
        return {"ok": True}

    @app.get("/api/accounts/export")
    def exp():
        return {"secret": "x"}

    @app.post("/api/accounts/import/apply")
    def imp():
        return {"ok": True}

    @app.get("/api/accounts/template")
    def tpl():
        return {"ok": True}

    @app.get("/api/ping")
    def ping():
        return {"ok": True}

    @app.delete("/api/history")
    def hclear():
        return {"ok": True}

    @app.get("/api/unknown-new-endpoint")
    def unk():
        return {"ok": True}

    store = types.SimpleNamespace(dir=tmp_path)
    (tmp_path / "static").mkdir(exist_ok=True)
    server.install(app, store)
    app.calls = calls
    return app


@pytest.fixture
def app(tmp_path, monkeypatch):
    return build(tmp_path, monkeypatch)


def mk(app, **kw):
    kw.setdefault("base_url", "http://testserver")
    return TestClient(app, follow_redirects=False, **kw)


def login(c, user="admin", pw=PW):
    r = c.post("/api/auth/login", json={"username": user, "password": pw})
    if r.status_code == 200:
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return r


def add_user(app, name="bob", role="user", pw="Bob-Pass-12345"):
    app.state.users.create(name, pw, role)
    return name, pw


# ---------------- 口令哈希 ----------------
def test_hash_is_salted_pbkdf2_and_verifies():
    a, b = server.hash_password("Secret-123"), server.hash_password("Secret-123")
    assert a != b and a.startswith("pbkdf2_sha256$240000$") and "Secret-123" not in a
    assert server.verify_password("Secret-123", a) and not server.verify_password("secret-123", a)


@pytest.mark.parametrize("enc", ["", "garbage", "md5$1$a$b", "pbkdf2_sha256$x$y$z", "pbkdf2_sha256$1000$!!$!!"])
def test_verify_rejects_malformed_hashes(enc):
    assert server.verify_password("x", enc) is False


@pytest.mark.parametrize("pw,ok", [("short", False), ("1234567", False), ("12345678", True), ("x" * 256, True), ("x" * 257, False), (None, False), (123456789, False)])
def test_password_policy(pw, ok):
    assert (server.check_new_password(pw) is None) is ok


# ---------------- 策略表（默认拒绝） ----------------
@pytest.mark.parametrize("method,path,need", [
    ("GET", "/login", None), ("GET", "/static/app.js", None), ("GET", "/api/ping", None), ("POST", "/api/auth/login", None),
    ("GET", "/", "user"), ("GET", "/api/meta", "user"), ("POST", "/api/quote", "user"), ("GET", "/api/history", "user"),
    ("GET", "/api/history/5", "user"), ("GET", "/api/history/5/csv", "user"), ("DELETE", "/api/history/5", "user"), ("DELETE", "/api/history", "user"),
    ("POST", "/api/auth/password", "user"), ("GET", "/api/accounts/template", "user"),
    ("PUT", "/api/platforms/yutong/account", "admin"), ("POST", "/api/platforms/yutong/test", "admin"),
    ("GET", "/api/accounts/export", "admin"), ("POST", "/api/accounts/import/apply", "admin"), ("POST", "/api/accounts/import/preview", "admin"),
    ("GET", "/api/users", "admin"), ("POST", "/api/users", "admin"), ("DELETE", "/api/users/bob", "admin"),
    ("GET", "/api/some/new/endpoint", "admin"), ("GET", "/api/history/abc", "admin"), ("DELETE", "/api/platforms/x", "admin"),
    ("GET", "/api/history/../accounts/export", "admin"),
])
def test_policy_table(method, path, need):
    assert server.required_role(method, path) == need


# ---------------- 用户库 ----------------
def test_userstore_crud_and_file_perms(tmp_path):
    us = server.UserStore(tmp_path / "users.json")
    us.create("Alice", "Alice-Pass-1", "admin")
    us.create("bob", "Bob-Pass-123")
    assert [u["username"] for u in us.public_list()] == ["Alice", "bob"]
    assert "hash" not in str(us.public_list())
    assert stat.S_IMODE(os.stat(tmp_path / "users.json").st_mode) == 0o600
    assert "Alice-Pass-1" not in (tmp_path / "users.json").read_text()
    assert us.get("ALICE")["username"] == "Alice"              # 大小写不敏感
    with pytest.raises(ValueError):
        us.create("alice", "Another-Pass-1")                   # 重名（不分大小写）
    for bad in ("a", "x" * 33, "bad name", "中文名", "a/b", ""):
        with pytest.raises(ValueError):
            us.create(bad, "Valid-Pass-123")
    with pytest.raises(ValueError):
        us.create("carol", "short")
    with pytest.raises(ValueError):
        us.create("carol", "Valid-Pass-123", "root")
    us.update("bob", role="admin")
    us.update("bob", disabled=True)
    assert us.authenticate("bob", "Bob-Pass-123") is None
    us.update("bob", disabled=False, password="New-Pass-1234")
    assert us.authenticate("bob", "New-Pass-1234") and not us.authenticate("bob", "Bob-Pass-123")
    us.delete("bob")
    with pytest.raises(KeyError):
        us.delete("bob")
    with pytest.raises(KeyError):
        us.update("ghost", role="user")


def test_userstore_keeps_last_admin(tmp_path):
    us = server.UserStore(tmp_path / "u.json")
    us.create("root1", "Root-Pass-123", "admin")
    us.create("u1", "User-Pass-1234")
    for fn in (lambda: us.update("root1", role="user"), lambda: us.update("root1", disabled=True), lambda: us.delete("root1")):
        with pytest.raises(ValueError):
            fn()
    us.create("root2", "Root-Pass-1234", "admin")
    us.update("root1", role="user")                            # 有第二个管理员后可以降级


def test_userstore_corrupt_file_backed_up(tmp_path):
    p = tmp_path / "u.json"
    p.write_text("{not json")
    us = server.UserStore(p)
    assert us.all() == {} and (tmp_path / "u.json.bak").exists()


def test_authenticate_unknown_user_still_hashes(tmp_path, monkeypatch):
    us = server.UserStore(tmp_path / "u.json")
    called = []
    orig = server.verify_password
    monkeypatch.setattr(server, "verify_password", lambda pw, enc: called.append(1) or orig(pw, enc))
    assert us.authenticate("nobody", "whatever") is None and called


# ---------------- 首个管理员 ----------------
def test_bootstrap_with_env_password(tmp_path, monkeypatch):
    monkeypatch.setenv("FREIGHT_HUB_ADMIN_PASSWORD", "Env-Admin-Pass-1")
    us = server.UserStore(tmp_path / "u.json")
    assert server.bootstrap_admin(us, tmp_path) is None
    assert us.authenticate("admin", "Env-Admin-Pass-1") and not (tmp_path / "INITIAL_ADMIN_PASSWORD.txt").exists()
    assert server.bootstrap_admin(us, tmp_path) is None        # 已有用户不再创建


def test_bootstrap_random_password_file_private_and_not_logged(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("FREIGHT_HUB_ADMIN_PASSWORD", raising=False)
    app = FastAPI()
    server.install(app, types.SimpleNamespace(dir=tmp_path))
    f = tmp_path / "INITIAL_ADMIN_PASSWORD.txt"
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
    pw = [l for l in f.read_text(encoding="utf-8").splitlines() if l.startswith("初始密码")][0].split("：", 1)[1]
    assert len(pw) >= 12 and app.state.users.authenticate("admin", pw)
    out = capsys.readouterr().out
    assert pw not in out and "INITIAL_ADMIN_PASSWORD" in out


def test_bootstrap_weak_env_password_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("FREIGHT_HUB_ADMIN_PASSWORD", "123")
    us = server.UserStore(tmp_path / "u.json")
    assert server.bootstrap_admin(us, tmp_path) is not None
    assert not us.authenticate("admin", "123")


# ---------------- 会话 ----------------
def test_sessions_idle_and_absolute_expiry(monkeypatch):
    s = server.Sessions(idle=200, absolute=300)
    t = [1000.0]
    monkeypatch.setattr(server.time, "time", lambda: t[0])
    tok = s.create("a", "user")
    t[0] += 90
    assert s.get(tok)
    t[0] += 90
    assert s.get(tok)                    # 活动会续期
    t[0] += 130
    assert s.get(tok) is None            # 绝对 300 秒到期（虽然一直在活动）
    tok = s.create("a", "user")
    t[0] += 201
    assert s.get(tok) is None            # 空闲到期
    assert s.get(None) is None and s.get("nope") is None


def test_sessions_tokens_unique_and_drop_user():
    s = server.Sessions()
    toks = {s.create("a", "user") for _ in range(50)}
    assert len(toks) == 50 and all(len(t) >= 40 for t in toks)
    s.create("B", "user")
    s.drop_user("b")
    assert all(v["name"] != "B" for v in s.d.values())


def test_throttle_locks_and_resets(monkeypatch):
    th = server.Throttle()
    for _ in range(server.MAX_FAILS - 1):
        th.fail("k")
    assert not th.blocked("k")
    th.fail("k")
    assert th.blocked("k") and not th.blocked("other")
    t = time.time()
    monkeypatch.setattr(server.time, "time", lambda: t + server.LOCK_SECONDS + 1)
    assert not th.blocked("k")
    th.fail("k")                           # 锁过期后重新计数
    assert not th.blocked("k")
    th.ok("k")


# ---------------- 中间件：认证 ----------------
def test_unauthenticated_page_redirects_api_401(app):
    c = mk(app)
    r = c.get("/")
    assert r.status_code == 302 and r.headers["location"] == "/login"
    r = c.get("/api/meta")
    assert r.status_code == 401 and "未登录" in r.json()["detail"]
    assert c.get("/api/ping").status_code == 200                # 公开
    assert c.get("/login").status_code in (200, 404)           # 登录页公开（测试用最小应用里 login.html 来自真实 static）


def test_login_success_cookie_flags_and_session(app):
    c = mk(app)
    r = login(c)
    assert r.status_code == 200 and r.json()["role"] == "admin" and len(r.json()["csrf"]) > 20
    sc = r.headers["set-cookie"]
    assert "HttpOnly" in sc and "SameSite=lax" in sc and "Path=/" in sc and "Secure" not in sc
    assert c.get("/api/meta").json() == {"who": "admin"}
    assert c.get("/").status_code == 200


def test_login_wrong_password_and_unknown_user_same_message(app):
    c = mk(app)
    a = c.post("/api/auth/login", json={"username": "admin", "password": "nope"})
    b = c.post("/api/auth/login", json={"username": "ghost", "password": "nope"})
    assert a.status_code == b.status_code == 401 and a.json() == b.json()
    assert "set-cookie" not in a.headers


@pytest.mark.parametrize("body", [{}, {"username": "admin"}, {"password": "x"}, {"username": None, "password": None}, [], "x"])
def test_login_bad_bodies_never_500(app, body):
    assert mk(app).post("/api/auth/login", json=body).status_code in (401, 422)


def test_login_throttle_after_five_failures(app):
    c = mk(app)
    for _ in range(server.MAX_FAILS):
        assert c.post("/api/auth/login", json={"username": "admin", "password": "bad"}).status_code == 401
    r = c.post("/api/auth/login", json={"username": "admin", "password": PW})
    assert r.status_code == 429 and "5 分钟" in r.json()["detail"]      # 正确密码在锁定期内也拒绝
    other = mk(app)
    assert login(other, "admin", PW).status_code == 429               # 同 IP 同用户名
    add_user(app)
    assert login(mk(app), "bob", "Bob-Pass-12345").status_code == 200  # 不影响其他用户名


def test_session_fixation_new_token_each_login(app):
    c = mk(app)
    login(c)
    t1 = c.cookies.get(server.COOKIE)
    login(c)
    t2 = c.cookies.get(server.COOKIE)
    assert t1 != t2 and t1 not in app.state.sessions.d


def test_forged_and_garbage_cookie_rejected(app):
    c = mk(app)
    c.cookies.set(server.COOKIE, "A" * 43)
    assert c.get("/api/meta").status_code == 401
    c.cookies.set(server.COOKIE, "x" * 5000)
    assert c.get("/api/meta").status_code == 401


def test_logout_invalidates_session(app):
    c = mk(app)
    login(c)
    tok = c.cookies.get(server.COOKIE)
    assert c.post("/api/auth/logout").status_code == 200
    c2 = mk(app)
    c2.cookies.set(server.COOKIE, tok)
    assert c2.get("/api/meta").status_code == 401


def test_logged_in_login_page_redirects_home(app):
    c = mk(app)
    login(c)
    assert c.get("/login").status_code == 302


# ---------------- CSRF / Origin ----------------
def test_csrf_required_for_state_changing_requests(app):
    c = mk(app)
    r = c.post("/api/auth/login", json={"username": "admin", "password": PW})
    assert r.status_code == 200
    assert c.post("/api/quote").status_code == 403 and "CSRF" in c.post("/api/quote").json()["detail"]
    assert c.post("/api/quote", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert c.post("/api/quote", headers={"X-CSRF-Token": r.json()["csrf"]}).status_code == 200
    assert c.get("/api/meta").status_code == 200                       # GET 不需要


def test_csrf_token_is_per_session(app):
    a, b = mk(app), mk(app)
    ra, rb = login(a), login(b)
    assert ra.json()["csrf"] != rb.json()["csrf"]
    del a.headers["X-CSRF-Token"]
    assert a.post("/api/quote", headers={"X-CSRF-Token": rb.json()["csrf"]}).status_code == 403


@pytest.mark.parametrize("origin,ok", [("http://testserver", True), ("http://evil.example.com", False), ("null", False), ("http://testserver.evil.com", False)])
def test_origin_check_on_login_and_writes(app, origin, ok):
    c = mk(app)
    r = c.post("/api/auth/login", json={"username": "admin", "password": PW}, headers={"Origin": origin})
    assert (r.status_code == 200) is ok
    if ok:
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
        assert c.post("/api/quote", headers={"Origin": "http://evil.example.com"}).status_code == 403
        assert c.post("/api/quote", headers={"Origin": origin}).status_code == 200


def test_allowed_origins_env(tmp_path, monkeypatch):
    app = build(tmp_path, monkeypatch, FREIGHT_HUB_ALLOWED_ORIGINS="quote.example.com, other.example.org")
    c = mk(app)
    assert c.post("/api/auth/login", json={"username": "admin", "password": PW}, headers={"Origin": "https://quote.example.com"}).status_code == 200


def test_forwarded_host_only_trusted_behind_proxy(tmp_path, monkeypatch):
    hdr = {"Origin": "https://quote.example.com", "X-Forwarded-Host": "quote.example.com"}
    app = build(tmp_path, monkeypatch)
    assert mk(app).post("/api/auth/login", json={"username": "admin", "password": PW}, headers=hdr).status_code == 403
    app2 = build(tmp_path / "p", monkeypatch, FREIGHT_HUB_TRUST_PROXY="1") if (tmp_path / "p").mkdir() is None else None
    assert mk(app2).post("/api/auth/login", json={"username": "admin", "password": PW}, headers=hdr).status_code == 200


# ---------------- 角色 ----------------
def test_user_role_restrictions_and_admin_allowed(app):
    name, pw = add_user(app)
    u, a = mk(app), mk(app)
    login(u, name, pw)
    login(a)
    assert u.get("/api/meta").status_code == 200 and u.post("/api/quote").status_code == 200
    assert u.delete("/api/history").status_code == 200
    for method, path in [("PUT", "/api/platforms/yutong/account"), ("POST", "/api/platforms/yutong/test"), ("GET", "/api/accounts/export"),
                         ("POST", "/api/accounts/import/apply"), ("GET", "/api/users"), ("POST", "/api/users"), ("GET", "/api/unknown-new-endpoint")]:
        r = u.request(method, path, json={} if method != "GET" else None)
        assert r.status_code == 403 and "管理员" in r.json()["detail"], (method, path)
    assert app.calls == []                                             # 被拒绝的请求根本没执行到处理函数
    assert a.put("/api/platforms/yutong/account").status_code == 200 and a.get("/api/accounts/export").status_code == 200
    assert u.get("/api/accounts/template").status_code == 200


def test_role_change_takes_effect_immediately(app):
    name, pw = add_user(app)
    u, a = mk(app), mk(app)
    login(u, name, pw)
    login(a)
    assert u.get("/api/accounts/export").status_code == 403
    assert a.put(f"/api/users/{name}", json={"role": "admin"}).status_code == 200
    assert u.get("/api/accounts/export").status_code == 200
    assert a.put(f"/api/users/{name}", json={"role": "user"}).status_code == 200
    assert u.get("/api/accounts/export").status_code == 403


def test_disable_and_delete_user_kill_sessions(app):
    name, pw = add_user(app)
    a = mk(app)
    login(a)
    u = mk(app)
    login(u, name, pw)
    assert a.put(f"/api/users/{name}", json={"disabled": True}).status_code == 200
    assert u.get("/api/meta").status_code == 401
    assert login(mk(app), name, pw).status_code == 401                 # 停用后不能登录
    a.put(f"/api/users/{name}", json={"disabled": False})
    u2 = mk(app)
    assert login(u2, name, pw).status_code == 200
    assert a.delete(f"/api/users/{name}").status_code == 200
    assert u2.get("/api/meta").status_code == 401


# ---------------- 用户管理 / 改密码 ----------------
def test_admin_user_management_flow_and_validation(app):
    a = mk(app)
    login(a)
    r = a.post("/api/users", json={"username": "bob", "password": "Bob-Pass-12345", "role": "user"})
    assert r.status_code == 200 and [u["username"] for u in r.json()] == ["admin", "bob"]
    assert "hash" not in r.text and "Bob-Pass" not in r.text
    assert a.post("/api/users", json={"username": "bob", "password": "Bob-Pass-12345"}).status_code == 400
    assert a.post("/api/users", json={"username": "x y", "password": "Bob-Pass-12345"}).status_code == 400
    assert a.post("/api/users", json={"username": "bob2", "password": "short"}).status_code == 400
    assert a.put("/api/users/ghost", json={"role": "user"}).status_code == 404
    assert a.delete("/api/users/ghost").status_code == 404
    assert a.put("/api/users/admin", json={"role": "user"}).status_code == 400          # 不能降级自己
    assert a.put("/api/users/admin", json={"disabled": True}).status_code == 400
    assert a.delete("/api/users/admin").status_code == 400
    assert a.put("/api/users/bob", json={"role": "superuser"}).status_code == 400
    assert a.put("/api/users/bob", json={"password": "New-Bob-Pass-1"}).status_code == 200
    assert login(mk(app), "bob", "New-Bob-Pass-1").status_code == 200


def test_change_own_password_invalidates_other_sessions(app):
    name, pw = add_user(app)
    d1, d2 = mk(app), mk(app)
    login(d1, name, pw)
    login(d2, name, pw)
    assert d1.post("/api/auth/password", json={"old": "wrong", "new": "Brand-New-Pass-1"}).status_code == 400
    assert d1.post("/api/auth/password", json={"old": pw, "new": "short"}).status_code == 400
    assert d1.post("/api/auth/password", json={"old": pw, "new": "Brand-New-Pass-1"}).status_code == 200
    assert d1.get("/api/meta").status_code == 200                      # 当前设备保持登录
    assert d2.get("/api/meta").status_code == 401                      # 其他设备被踢下线
    assert login(mk(app), name, pw).status_code == 401 and login(mk(app), name, "Brand-New-Pass-1").status_code == 200


# ---------------- 响应头 / HTTPS ----------------
def test_security_headers_and_no_store(app):
    c = mk(app)
    login(c)
    r = c.get("/api/meta")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer" and r.headers["cache-control"] == "no-store"
    assert "strict-transport-security" not in r.headers


def test_https_sets_secure_cookie_and_hsts(tmp_path, monkeypatch):
    app = build(tmp_path, monkeypatch, FREIGHT_HUB_HTTPS="1")
    r = mk(app).post("/api/auth/login", json={"username": "admin", "password": PW})
    assert "Secure" in r.headers["set-cookie"] and "strict-transport-security" in r.headers


def test_proxy_forwarded_proto_https_sets_secure(tmp_path, monkeypatch):
    app = build(tmp_path, monkeypatch, FREIGHT_HUB_TRUST_PROXY="1")
    r = mk(app).post("/api/auth/login", json={"username": "admin", "password": PW}, headers={"X-Forwarded-Proto": "https"})
    assert "Secure" in r.headers["set-cookie"]
    app2 = build(tmp_path / "x", monkeypatch) if (tmp_path / "x").mkdir() is None else None
    r = mk(app2).post("/api/auth/login", json={"username": "admin", "password": PW}, headers={"X-Forwarded-Proto": "https"})
    assert "Secure" not in r.headers["set-cookie"]                    # 不在代理后面时不信任该头


def test_throttle_key_uses_forwarded_ip_only_behind_proxy(tmp_path, monkeypatch):
    app = build(tmp_path, monkeypatch, FREIGHT_HUB_TRUST_PROXY="1")
    for _ in range(server.MAX_FAILS):
        mk(app).post("/api/auth/login", json={"username": "admin", "password": "bad"}, headers={"X-Forwarded-For": "1.1.1.1"})
    r = mk(app).post("/api/auth/login", json={"username": "admin", "password": PW}, headers={"X-Forwarded-For": "2.2.2.2"})
    assert r.status_code == 200                                       # 另一个来源 IP 不受影响
    r = mk(app).post("/api/auth/login", json={"username": "admin", "password": PW}, headers={"X-Forwarded-For": "1.1.1.1"})
    assert r.status_code == 429


def test_session_hours_env(tmp_path, monkeypatch):
    app = build(tmp_path, monkeypatch, FREIGHT_HUB_SESSION_HOURS="0.5")
    assert app.state.sessions.idle == 1800
