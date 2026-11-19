"""云端网页版（Vercel + Postgres）：RBAC 权限矩阵、审计日志、首次设置、会话 / CSRF / 限流、静态加密、桌面专属标记。
数据库用 SQLite（与 Postgres 同一套 SQL）；真实 Neon 由部署后的线上验证覆盖。"""
import base64
import csv
import importlib
import io
import json
import sys
import time

import pytest
from fastapi.testclient import TestClient

from app import rbac
from app.db import Db
from app.secretbox import SecretBox, SecretBoxError, generate_key

pytestmark = pytest.mark.unit
PW = "Admin-Pass-12345"
CODE = "setup-code-for-tests"


@pytest.fixture
def cloud(monkeypatch, tmp_path):
    for k in ("VERCEL", "FREIGHT_HUB_TRUST_PROXY", "FREIGHT_HUB_HTTPS", "FREIGHT_HUB_ALLOWED_ORIGINS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("FREIGHT_HUB_MODE", "cloud")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/c.db")
    monkeypatch.setenv("FH_DATA_KEY", generate_key())
    monkeypatch.setenv("FH_SETUP_CODE", CODE)
    saved = sys.modules.pop("app.main", None)
    m = importlib.import_module("app.main")
    yield m
    import app as _pkg
    if saved is not None:
        sys.modules["app.main"] = saved
        _pkg.main = saved               # `from app import main` 取的是包属性，也要还原
    else:
        sys.modules.pop("app.main", None)


def mk(cloud, base="https://t.test"):
    return TestClient(cloud.app, base_url=base)


def setup_admin(c):
    r = c.post("/api/setup", json={"code": CODE, "username": "admin", "password": PW})
    assert r.status_code == 200, r.text


def login(c, user="admin", pw=PW, remember=True):
    r = c.post("/api/auth/login", json={"username": user, "password": pw, "remember": remember})
    assert r.status_code == 200, r.text
    c.csrf = r.json()["csrf"]
    return r


def H(c):
    return {"x-csrf-token": c.csrf}


@pytest.fixture
def adm(cloud):
    c = mk(cloud)
    setup_admin(c)
    login(c)
    c.cloud = cloud
    return c


def add_user(adm, name, role, pw="User-Pass-12345"):
    r = adm.post("/api/users", json={"username": name, "password": pw, "role": role}, headers=H(adm))
    assert r.status_code == 200, r.text
    c = mk(adm.cloud)
    login(c, name, pw)
    return c


def audit_actions(adm, **q):
    return [(x["action"], x["status"], x["username"]) for x in adm.get("/api/audit", params={"size": 200, **q}).json()["items"]]


# ---------------------------------------------------------------- 加密
def test_secretbox_roundtrip_and_aad():
    b = SecretBox(generate_key())
    blob = b.seal({"password": "p@ss"}, "account:x")
    assert "p@ss" not in blob and blob.startswith("fh1.")
    assert b.open(blob, "account:x") == {"password": "p@ss"}
    with pytest.raises(SecretBoxError):
        b.open(blob, "account:y")           # 换用途解不开
    with pytest.raises(SecretBoxError):
        SecretBox(generate_key()).open(blob, "account:x")   # 换密钥解不开
    raw = bytearray(base64.urlsafe_b64decode(blob[4:] + "=="))
    raw[-1] ^= 1
    with pytest.raises(SecretBoxError):
        b.open("fh1." + base64.urlsafe_b64encode(bytes(raw)).decode(), "account:x")   # 篡改检测
    with pytest.raises(SecretBoxError):
        SecretBox("short")


def test_db_named_to_positional():
    d = Db(":memory:")
    q, args = d._to_positional("SELECT * FROM t WHERE a=:a AND b=:b AND c LIKE '%:a%' AND a2=:a", {"a": 1, "b": 2})
    assert q == "SELECT * FROM t WHERE a=$1 AND b=$2 AND c LIKE '%:a%' AND a2=$1" and args == [1, 2]


def test_rbac_catalog_sane():
    assert set(rbac.close_perms(["users.manage"])) == {"users.manage", "users.view"}
    assert "history.view" in rbac.close_perms(["history.delete_all"])
    assert rbac.clean_perms(["nope", "audit.view", "audit.view"]) == ["audit.view"]
    for m, p, perm in rbac.ROUTE_PERMS:
        assert perm is None or perm in rbac.ALL
    for name, label, descr, perms, system in rbac.DEFAULT_ROLES:
        assert set(perms) <= rbac.ALL
    assert rbac.match_route("DELETE", "/api/audit") == (False, None)       # 审计日志没有删除接口
    assert rbac.match_route("POST", "/api/platforms/yuntu/order") == (False, None)   # 没有下单接口


# ---------------------------------------------------------------- 首次设置 / 登录 / 会话
def test_setup_requires_code_and_closes(cloud):
    c = mk(cloud)
    assert c.get("/api/setup/status").json() == {"needed": True, "enabled": True}
    assert c.get("/login", follow_redirects=False).headers["location"] == "/setup"
    assert c.post("/api/setup", json={"code": "wrong", "username": "admin", "password": PW}).status_code == 403
    assert c.post("/api/setup", json={"code": CODE, "username": "a b", "password": PW}).status_code == 400
    assert c.post("/api/setup", json={"code": CODE, "username": "admin", "password": "short"}).status_code == 400
    setup_admin(c)
    assert c.post("/api/setup", json={"code": CODE, "username": "evil", "password": PW}).status_code == 409
    assert c.get("/setup", follow_redirects=False).headers["location"] == "/login"
    assert c.get("/api/setup/status").json()["needed"] is False


def test_setup_disabled_without_code(cloud, monkeypatch):
    monkeypatch.delenv("FH_SETUP_CODE")
    c = mk(cloud)
    assert c.post("/api/setup", json={"code": "", "username": "admin", "password": PW}).status_code == 403


def test_login_cookie_flags_remember_and_throttle(cloud, monkeypatch):
    cloud.app.state.cloud.https = cloud.app.state.cloud.trust = True      # Vercel 上：HTTPS + 信任代理头
    c = mk(cloud)
    setup_admin(c)
    r = login(c, remember=True)
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "secure" in sc and "samesite=lax" in sc and "max-age=2592000" in sc
    c2 = mk(cloud)
    sc2 = login(c2, remember=False).headers["set-cookie"].lower()
    assert "max-age" not in sc2 and "httponly" in sc2
    bad = mk(cloud)
    for _ in range(5):
        assert bad.post("/api/auth/login", json={"username": "admin", "password": "nope"}).status_code == 401
    assert bad.post("/api/auth/login", json={"username": "admin", "password": PW}).status_code == 429   # 正确口令也被锁
    assert mk(cloud).post("/api/auth/login", json={"username": "ghost", "password": "x"}).status_code == 401


def test_unauthenticated_and_csrf_origin(adm, cloud):
    anon = mk(cloud)
    assert anon.get("/api/meta").status_code == 401
    assert anon.get("/", follow_redirects=False).headers["location"] == "/login"
    assert adm.post("/api/users", json={"username": "x1", "password": "Xxxxxxxx1", "role": "viewer"}).status_code == 403    # 缺 CSRF
    assert adm.post("/api/users", json={"username": "x1", "password": "Xxxxxxxx1", "role": "viewer"},
                    headers={**H(adm), "origin": "https://evil.example"}).status_code == 403
    assert adm.post("/api/users", json={"username": "x1", "password": "Xxxxxxxx1", "role": "viewer"},
                    headers={**H(adm), "origin": "https://t.test"}).status_code == 200
    r = adm.get("/api/meta")
    assert r.headers["x-frame-options"] == "DENY" and "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"


def test_session_expiry_and_logout_and_disable(adm, cloud):
    bob = add_user(adm, "bob", "operator")
    assert bob.get("/api/meta").status_code == 200
    adm.put("/api/users/bob", json={"disabled": True}, headers=H(adm))
    assert bob.get("/api/meta").status_code == 401           # 停用立即失效
    adm.put("/api/users/bob", json={"disabled": False}, headers=H(adm))
    c = mk(cloud)
    login(c, "bob", "User-Pass-12345", remember=False)
    cl = cloud.app.state.cloud
    cl.db.x("UPDATE sessions SET seen=:t", t=time.time() - 9 * 3600)   # 非记住我：8 小时空闲过期
    assert c.get("/api/meta").status_code == 401
    d = mk(cloud)
    login(d, "bob", "User-Pass-12345", remember=True)
    cl.db.x("UPDATE sessions SET seen=:t, created=:t", t=time.time() - 20 * 86400)   # 记住我：30 天内仍有效
    assert d.get("/api/meta").status_code == 200
    cl.db.x("UPDATE sessions SET seen=:t, created=:t", t=time.time() - 31 * 86400)
    assert d.get("/api/meta").status_code == 401
    e = mk(cloud)
    login(e, "bob", "User-Pass-12345")
    assert e.post("/api/auth/logout", headers=H(e)).status_code == 200
    assert e.get("/api/meta").status_code == 401


def test_change_password_revokes_other_sessions(adm, cloud):
    other = mk(cloud)
    login(other)
    r = adm.post("/api/auth/password", json={"old": "bad", "new": "New-Pass-12345"}, headers=H(adm))
    assert r.status_code == 400
    assert adm.post("/api/auth/password", json={"old": PW, "new": "New-Pass-12345"}, headers=H(adm)).status_code == 200
    assert other.get("/api/meta").status_code == 401 and adm.get("/api/meta").status_code == 200
    assert mk(cloud).post("/api/auth/login", json={"username": "admin", "password": "New-Pass-12345"}).status_code == 200


# ---------------------------------------------------------------- RBAC
def test_default_roles_matrix_enforced(adm):
    roles = {r["name"]: r for r in adm.get("/api/roles").json()["roles"]}
    assert set(roles) == {"admin", "manager", "operator", "auditor", "viewer", "demo"} and set(roles["admin"]["perms"]) == rbac.ALL
    viewer, op, aud = add_user(adm, "v1", "viewer"), add_user(adm, "o1", "operator"), add_user(adm, "a1", "auditor")
    body = {"country": "US", "weight": 1, "platforms": ["none"]}
    assert viewer.post("/api/quote", json=body, headers=H(viewer)).status_code == 403
    assert viewer.get("/api/platforms").status_code == 200 and viewer.get("/api/history").status_code == 200
    assert op.post("/api/quote", json=body, headers=H(op)).status_code == 200
    for c in (viewer, op):
        assert c.get("/api/users").status_code == 403 and c.get("/api/roles").status_code == 403 and c.get("/api/audit").status_code == 403
        assert c.put("/api/platforms/yuntu/account", json={"values": {"username": "x"}}, headers=H(c)).status_code == 403
    assert aud.get("/api/audit").status_code == 200 and aud.get("/api/users").status_code == 200 and aud.get("/api/roles").status_code == 200
    assert aud.post("/api/quote", json=body, headers=H(aud)).status_code == 403
    assert aud.post("/api/users", json={"username": "zz", "password": "Zzzzzzzz1", "role": "viewer"}, headers=H(aud)).status_code == 403
    assert aud.get("/api/audit/export").status_code == 200
    assert op.get("/api/audit/export").status_code == 403
    assert op.delete("/api/audit").status_code == 403 and adm.delete("/api/audit", headers=H(adm)).status_code == 403   # 审计不可删
    assert adm.get("/api/does-not-exist").status_code == 403                    # 默认拒绝
    me = op.get("/api/meta").json()
    assert me["cloud"] and "query.run" in me["perms"] and "users.manage" not in me["perms"] and me["role_label"] == "业务员"


def test_matrix_change_takes_effect_immediately(adm):
    op = add_user(adm, "o1", "operator")
    body = {"country": "US", "weight": 1, "platforms": ["none"]}
    assert op.post("/api/quote", json=body, headers=H(op)).status_code == 200
    perms = [p for p in adm.get("/api/roles").json()["roles"][2]["perms"] if p != "query.run"]
    r = adm.put("/api/roles/operator", json={"perms": perms}, headers=H(adm))
    assert r.status_code == 200
    assert op.post("/api/quote", json=body, headers=H(op)).status_code == 403          # 不用重新登录，立刻生效
    r = adm.put("/api/roles/operator", json={"perms": ["audit.export"]}, headers=H(adm))
    got = next(x for x in r.json()["roles"] if x["name"] == "operator")["perms"]
    assert got == ["audit.view", "audit.export"] or set(got) == {"audit.view", "audit.export"}   # 依赖自动补全
    assert op.get("/api/audit").status_code == 200


def test_custom_role_crud_and_guards(adm):
    assert adm.post("/api/roles", json={"name": "Bad Name", "label": "x"}, headers=H(adm)).status_code == 400
    assert adm.post("/api/roles", json={"name": "sales_a", "label": "销售 A 组", "perms": ["query.run", "bogus"]}, headers=H(adm)).status_code == 200
    r = next(x for x in adm.get("/api/roles").json()["roles"] if x["name"] == "sales_a")
    assert set(r["perms"]) == {"query.run", "platforms.view"}
    assert adm.post("/api/roles", json={"name": "sales_a", "label": "dup"}, headers=H(adm)).status_code == 400
    assert adm.put("/api/roles/admin", json={"perms": []}, headers=H(adm)).status_code == 400       # 系统角色不可改
    assert adm.delete("/api/roles/admin", headers=H(adm)).status_code == 400
    add_user(adm, "s1", "sales_a")
    assert adm.delete("/api/roles/sales_a", headers=H(adm)).status_code == 400                       # 有人用不能删
    adm.delete("/api/users/s1", headers=H(adm))
    assert adm.delete("/api/roles/sales_a", headers=H(adm)).status_code == 200
    assert adm.put("/api/roles/ghost", json={"label": "x"}, headers=H(adm)).status_code == 404


def test_privilege_escalation_blocked(adm):
    adm.post("/api/roles", json={"name": "hr", "label": "人事", "perms": ["users.manage"]}, headers=H(adm))
    hr = add_user(adm, "hr1", "hr")
    H_ = H(hr)
    # 不能把用户提到 admin / 高于自己的角色，不能创建高权限角色，不能授予自己没有的权限
    assert hr.post("/api/users", json={"username": "x2", "password": "Xxxxxxxx1", "role": "admin"}, headers=H_).status_code == 403
    assert hr.post("/api/users", json={"username": "x3", "password": "Xxxxxxxx1", "role": "viewer"}, headers=H_).status_code == 403    # viewer 的权限 hr 自己没有
    assert hr.post("/api/users", json={"username": "x3", "password": "Xxxxxxxx1", "role": "hr"}, headers=H_).status_code == 200
    assert hr.put("/api/users/x3", json={"role": "manager"}, headers=H_).status_code == 403
    assert hr.put("/api/users/admin", json={"password": "Hacked-12345"}, headers=H_).status_code == 403
    assert hr.delete("/api/users/admin", headers=H_).status_code in (400, 403)
    assert hr.post("/api/roles", json={"name": "x", "label": "x", "perms": []}, headers=H_).status_code == 403    # 无 roles.manage
    assert mk(adm.cloud).post("/api/auth/login", json={"username": "admin", "password": "Hacked-12345"}).status_code == 401


def test_users_guards_last_admin_and_self(adm):
    assert adm.put("/api/users/admin", json={"disabled": True}, headers=H(adm)).status_code == 400
    assert adm.put("/api/users/admin", json={"role": "viewer"}, headers=H(adm)).status_code == 400
    assert adm.delete("/api/users/admin", headers=H(adm)).status_code == 400
    adm.post("/api/users", json={"username": "adm2", "password": "Adm2-Pass-123", "role": "admin"}, headers=H(adm))
    assert adm.put("/api/users/adm2", json={"role": "viewer"}, headers=H(adm)).status_code == 200      # 还有别的管理员，可以降级
    assert adm.post("/api/users", json={"username": "ADMIN", "password": "Xxxxxxxx1", "role": "viewer"}, headers=H(adm)).status_code == 400   # 大小写不敏感去重
    assert adm.post("/api/users", json={"username": "u9", "password": "short", "role": "viewer"}, headers=H(adm)).status_code == 400
    assert adm.post("/api/users", json={"username": "u9", "password": "Xxxxxxxx1", "role": "ghost"}, headers=H(adm)).status_code == 400


# ---------------------------------------------------------------- 审计
def test_audit_records_who_what_when_ip(adm, cloud, monkeypatch):
    c = mk(cloud)
    cl = cloud.app.state.cloud
    cl.trust = True
    c.post("/api/auth/login", json={"username": "admin", "password": "wrong"}, headers={"x-vercel-forwarded-for": "203.0.113.9, 10.0.0.1"})
    login(c)
    items = adm.get("/api/audit", params={"action": "auth.login_failed"}).json()["items"]
    assert items and items[0]["ip"] == "203.0.113.9" and items[0]["status"] == "fail" and items[0]["target"] == "admin"
    assert "wrong" not in json.dumps(adm.get("/api/audit", params={"size": 200}).json())          # 口令不入日志
    adm.post("/api/users", json={"username": "bob", "password": "Bobbob-1234", "role": "operator"}, headers=H(adm))
    adm.put("/api/users/bob", json={"role": "viewer"}, headers=H(adm))
    adm.post("/api/roles", json={"name": "r1", "label": "R1"}, headers=H(adm))
    adm.put("/api/roles/r1", json={"perms": ["query.run"]}, headers=H(adm))
    adm.delete("/api/users/bob", headers=H(adm))
    acts = [a[0] for a in audit_actions(adm)]
    for a in ("setup.admin", "auth.login", "auth.login_failed", "user.create", "user.update", "user.delete", "role.create", "role.update"):
        assert a in acts, a
    upd = adm.get("/api/audit", params={"action": "role.update"}).json()["items"][0]
    assert upd["detail"]["added"] == ["platforms.view", "query.run"] and upd["role"] == "admin" and upd["username"] == "admin" and upd["time"][:4] == "2026"
    # 越权被拒也记
    op = add_user(adm, "o1", "operator")
    assert op.get("/api/users", params={}).status_code == 403
    denied = adm.get("/api/audit", params={"action": "access.denied", "user": "o1"}).json()["items"]
    assert denied and denied[0]["status"] == "fail" and "users.view" in json.dumps(denied[0]["detail"])


def test_audit_filter_paging_export(adm):
    for i in range(7):
        adm.post("/api/roles", json={"name": f"rx{i}", "label": f"R{i}"}, headers=H(adm))
    d = adm.get("/api/audit", params={"size": 10, "action": "role.create"}).json()
    assert d["total"] == 7 and len(d["items"]) == 7
    d = adm.get("/api/audit", params={"size": 10, "page": 1}).json()
    assert d["total"] == 9 and len(d["items"]) == 9 and d["items"][0]["id"] > d["items"][1]["id"]      # 新的在前
    for i in range(7, 12):
        adm.post("/api/roles", json={"name": f"rx{i}", "label": f"R{i}"}, headers=H(adm))
    p2 = adm.get("/api/audit", params={"size": 10, "page": 2}).json()          # 最小每页 10 条
    assert p2["total"] == 14 and len(p2["items"]) == 4 and p2["page"] == 2
    assert adm.get("/api/audit", params={"status": "fail"}).json()["total"] == 0
    assert adm.get("/api/audit", params={"q": "rx3"}).json()["total"] >= 1
    assert adm.get("/api/audit", params={"since": time.time() + 3600}).json()["total"] == 0
    assert adm.get("/api/audit", params={"until": time.time() + 3600, "since": time.time() - 3600, "user": "ADM"}).json()["total"] >= 8
    assert adm.get("/api/audit", params={"ip": "zzz"}).json()["total"] == 0
    # 导出：带 BOM、可被 csv 解析、只导出筛选后的、公式注入防护、导出本身入审计
    adm.post("/api/auth/login", json={"username": "=HYPERLINK(\"http://x\")", "password": "x"})
    r = adm.get("/api/audit/export", params={"action": "auth.login_failed"})
    assert r.status_code == 200 and r.content.startswith(b"\xef\xbb\xbf") and "attachment" in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.text.lstrip("\ufeff"))))
    assert rows[0][:5] == ["时间(UTC+8)", "用户", "角色", "IP", "操作"] and len(rows) == 2
    assert rows[1][6].startswith("'=")
    assert ("audit.export", "ok", "admin") in audit_actions(adm)
    assert adm.get("/api/audit", params={"size": 5, "page": "x"}).status_code == 400


# ---------------------------------------------------------------- 历史隔离
def test_history_scoping_by_permission(adm):
    o1, o2, mgr = add_user(adm, "o1", "operator"), add_user(adm, "o2", "operator"), add_user(adm, "m1", "manager")
    ids = {}
    for n, c in (("o1", o1), ("o2", o2)):
        ids[n] = c.post("/api/quote", json={"country": "US", "weight": 1, "platforms": ["none"]}, headers=H(c)).json()["id"]
    assert [h["user"] for h in o1.get("/api/history").json()] == ["o1"]
    assert o1.get("/api/history/%d" % ids["o2"]).status_code == 404
    assert o1.get("/api/history/%d/csv" % ids["o2"]).status_code == 404
    assert {h["user"] for h in mgr.get("/api/history").json()} == {"o1", "o2"}                       # history.view_all
    assert mgr.get("/api/history/%d" % ids["o1"]).status_code == 200
    assert ("history.view", "ok", "m1") in audit_actions(adm)                                          # 看别人的记录会留痕
    assert o1.delete("/api/history/%d" % ids["o2"], headers=H(o1)).status_code == 200
    assert o2.get("/api/history/%d" % ids["o2"]).status_code == 200                                    # 删不掉别人的
    assert o1.delete("/api/history", headers=H(o1)).status_code == 200
    assert len(o2.get("/api/history").json()) == 1                                                     # 清空只清自己的
    assert mgr.delete("/api/history", headers=H(mgr)).status_code == 200                               # history.delete_all
    assert adm.get("/api/history").json() == []


# ---------------------------------------------------------------- 平台账号：静态加密 / 不外泄 / 桌面专属
SECRET = "S3cr3t-Value-ABCDEF"


def test_accounts_encrypted_at_rest_and_masked(adm, cloud):
    r = adm.put("/api/platforms/yutong/account", json={"values": {"app_key": "KEY-ID-123", "app_secret": SECRET, "username": "u", "password": SECRET + "pw"}, "auth_mode": "api"}, headers=H(adm))
    assert r.status_code == 200
    assert SECRET not in json.dumps(adm.get("/api/platforms").json()) and SECRET not in r.text           # 密钥不回显
    raw = cloud.store.db.one("SELECT blob FROM accounts WHERE pid='yutong'")["blob"]
    assert raw.startswith("fh1.") and SECRET not in raw and "KEY-ID-123" not in raw                      # 库里是密文
    assert cloud.store.get_account("yutong")["app_secret"] == SECRET                                      # 服务端能解密用
    # 换了密钥：不崩，当作未配置
    from app.pgstore import DbStore
    other = DbStore(cloud.store.db, SecretBox(generate_key()))
    assert other.get_account("yutong") == {}
    ev = [x for x in adm.get("/api/audit", params={"action": "account.update"}).json()["items"]]
    assert ev and SECRET not in json.dumps(ev) and "app_secret" in json.dumps(ev[0]["detail"])           # 审计只记改了哪些字段
    ex = adm.get("/api/accounts/export", params={"fmt": "csv", "secrets": "none"})
    assert ex.status_code == 200 and SECRET not in ex.text


def test_platform_sessions_and_batches_encrypted(cloud):
    st = cloud.store
    st.save_session("zhonghang", {"token": "TOKEN-VALUE-XYZ"})
    assert "TOKEN-VALUE-XYZ" not in st.db.one("SELECT blob FROM psessions WHERE pid='zhonghang'")["blob"]
    assert st.load_session("zhonghang") == {"token": "TOKEN-VALUE-XYZ"}
    st.clear_session("zhonghang")
    assert st.load_session("zhonghang") is None
    st.batch_put("b1", [{"patch": {"password": SECRET}}])
    assert SECRET not in st.db.one("SELECT blob FROM batches WHERE id='b1'")["blob"]
    assert st.batch_pop("b1") == [{"patch": {"password": SECRET}}]
    assert st.batch_pop("b1") is None                                                                       # 只能用一次
    st.batch_put("b2", [1])
    st.db.x("UPDATE batches SET ts=:t", t=time.time() - 4000)
    assert st.batch_pop("b2") is None                                                                       # 30 分钟过期
    st.lock_login("yuntu"); assert st.login_locked("yuntu"); st.unlock_login("yuntu"); assert not st.login_locked("yuntu")
    st.mark("yuntu", False, "bad " + SECRET)
    assert st.runtime.get("yuntu")["last_error"]
    st.mark("yuntu", True)
    assert st.runtime.get("yuntu")["last_error"] is None


def test_import_flow_works_across_requests(adm):
    csv_text = "平台,授权方式,账号,密码\n中航,登录,zhuser,zhpass-" + SECRET + "\n"
    pv = adm.post("/api/accounts/import/preview", json={"filename": "a.csv", "data": base64.b64encode(csv_text.encode("utf-8")).decode()}, headers=H(adm))
    assert pv.status_code == 200, pv.text
    bid = pv.json()["batch"]
    ap = adm.post("/api/accounts/import/apply", json={"batch": bid}, headers=H(adm))
    assert ap.status_code == 200 and ap.json()["results"]
    assert adm.post("/api/accounts/import/apply", json={"batch": bid}, headers=H(adm)).status_code == 400   # 批次用过即废
    assert adm.cloud.store.get_account("zhonghang").get("username") == "zhuser"
    assert ("account.import", "ok", "admin") in audit_actions(adm)


def test_yuntu_login_mode_is_desktop_only_in_cloud(adm):
    # 默认就是开放平台 API；只有显式选「登录账号」才进入桌面专属状态
    pl0 = {p["id"]: p for p in adm.get("/api/platforms").json()}
    assert pl0["yuntu"]["auth_mode"] == "api" and pl0["yuntu"]["label"] != "仅桌面版"
    adm.put("/api/platforms/yuntu/account", json={"values": {}, "auth_mode": "login"}, headers=H(adm))
    pl = {p["id"]: p for p in adm.get("/api/platforms").json()}
    y = pl["yuntu"]
    assert y["auth_mode"] == "login" and y["label"] == "仅桌面版" and y["implemented"] is False
    assert y["modes"]["login"]["desktop_only"] is True and y["modes"]["api"].get("desktop_only") in (None, False)
    r = adm.post("/api/quote", json={"country": "US", "weight": 1, "platforms": ["yuntu"]}, headers=H(adm)).json()
    assert r["platforms"][0]["state"] == "pending" and "仅桌面版" in r["platforms"][0]["message"] and r["rows"] == []
    t = adm.post("/api/platforms/yuntu/test", json={}, headers=H(adm)).json()
    assert t["ok"] is False and "仅桌面版" in t["message"]
    # 中航登录是纯 HTTP（V5 接口），不属于桌面专属
    assert pl["zhonghang"]["modes"]["login"]["desktop_only"] is False
    # 云途切到 API 授权就能用（不再桌面专属）
    adm.put("/api/platforms/yuntu/account", json={"values": {}, "auth_mode": "api"}, headers=H(adm))
    assert adm.get("/api/platforms").json()[0]["id"] != "" and not any(p["label"] == "仅桌面版" for p in adm.get("/api/platforms").json())


def test_db_outage_returns_503_not_traceback(adm, cloud, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down: postgres://user:pw@host")
    monkeypatch.setattr(cloud.app.state.cloud.db, "_run", boom)
    r = adm.get("/api/meta")
    assert r.status_code == 503 and "postgres" not in r.text and "pw@" not in r.text


def test_desktop_mode_unchanged(client):
    assert client.get("/api/meta").json()["mode"] == "desktop"


# ---------------------------------------------------------------- 试用版：水印 + demo 账号
def test_watermark_env_controlled_on_all_pages(adm, cloud, monkeypatch):
    anon = mk(cloud)
    monkeypatch.delenv("FH_TRIAL_WATERMARK", raising=False)
    assert "fh-wm" not in anon.get("/login").text and "fh-wm" not in adm.get("/").text          # 默认关
    monkeypatch.setenv("FH_TRIAL_WATERMARK", "1")
    for html in (anon.get("/login").text, adm.get("/").text):
        assert 'id="fh-wm"' in html and 'id="fh-wmb"' in html and "未授权版本 / 试用" in html
        assert "pointer-events:none!important" in html and "MutationObserver" in html          # 防随手去掉的守护脚本
        assert html.index("fh-wm") > html.index("<body")
    assert "no-store" in anon.get("/login").headers["cache-control"]
    monkeypatch.setenv("FH_TRIAL_WATERMARK", "0")
    assert "fh-wm" not in anon.get("/login").text
    # 未初始化时的设置页也有
    monkeypatch.setenv("FH_TRIAL_WATERMARK", "1")


def test_watermark_on_setup_page(cloud, monkeypatch):
    monkeypatch.setenv("FH_TRIAL_WATERMARK", "1")
    c = mk(cloud)
    r = c.get("/setup")
    assert r.status_code == 200 and 'id="fh-wm"' in r.text


def test_demo_role_restrictions_and_quota(adm, cloud):
    from app.cloud import trial_watermark_on  # noqa: F401
    cl = cloud.app.state.cloud
    assert "demo" in {r["name"] for r in adm.get("/api/roles").json()["roles"]}
    adm.put("/api/platforms/yutong/account", json={"values": {"app_key": "KEY-ID-123", "app_secret": SECRET, "username": "uu-visible", "password": "x"}, "auth_mode": "api"}, headers=H(adm))
    d = add_user(adm, "demo", "demo")
    cl.set_policy("demo", {"qph": 3, "no_pw": True, "trial": True})
    # 能做的
    assert d.get("/api/platforms").status_code == 200
    plat = json.dumps(d.get("/api/platforms").json(), ensure_ascii=False)
    assert "uu-visible" not in plat and "KEY-ID-123" not in plat and SECRET not in plat          # 看不到任何账号字段
    assert "uu-visible" in json.dumps(adm.get("/api/platforms").json())                           # 管理员仍能看到
    # 不能做的
    for method, path in (("get", "/api/accounts/export"), ("get", "/api/users"), ("get", "/api/roles"), ("get", "/api/audit"), ("get", "/api/audit/export")):
        assert getattr(d, method)(path).status_code == 403, path
    assert d.put("/api/platforms/yutong/account", json={"values": {}}, headers=H(d)).status_code == 403
    assert d.post("/api/platforms/yutong/test", headers=H(d)).status_code == 403
    assert d.post("/api/accounts/import/preview", headers=H(d)).status_code == 403
    assert d.post("/api/users", json={"username": "x1", "password": "Abcdefg12345", "role": "admin"}, headers=H(d)).status_code == 403
    # 改密码被拒（共享账号）
    r = d.post("/api/auth/password", json={"old": "User-Pass-12345", "new": "Another-Pass-9999"}, headers=H(d))
    assert r.status_code == 403 and "试用" in r.json()["detail"]
    # meta 告诉前端
    m = d.get("/api/meta").json()
    assert m["quota"] == {"limit": 3, "used": 0, "left": 3} and m["no_pw"] is True and "history.export" not in m["perms"]
    # 限额：前 3 次成功，第 4 次 429，并留痕；管理员不受影响
    body = {"country": "US", "weight": 1, "platforms": ["none"]}
    for _ in range(3):
        assert d.post("/api/quote", json=body, headers=H(d)).status_code == 200
    r = d.post("/api/quote", json=body, headers=H(d))
    assert r.status_code == 429 and "3" in r.json()["detail"]
    assert d.get("/api/meta").json()["quota"]["left"] == 0
    assert ("quote.limited", "fail", "demo") in audit_actions(adm)
    assert adm.post("/api/quote", json=body, headers=H(adm)).status_code == 200
    # 自己的历史：只能看到自己的 3 条，管理员的看不到
    hs = d.get("/api/history").json()
    assert len(hs) == 3 and {h["user"] for h in hs} == {"demo"}
    assert d.get("/api/history/%d/csv" % hs[0]["id"]).status_code == 403                        # 不能导出
    # 一小时前的查询不计入
    cl.db.x("UPDATE audit SET ts=ts-4000 WHERE username='demo' AND action='quote.run'")
    assert d.post("/api/quote", json=body, headers=H(d)).status_code == 200


def test_policy_roundtrip_and_no_policy_unlimited(adm, cloud):
    cl = cloud.app.state.cloud
    assert cl.policy("nobody") == {} and cl.quota("nobody") == (0, 0)
    cl.set_policy("Bob", {"qph": 5})
    assert cl.policy("bob") == {"qph": 5} and cl.quota("bob")[0] == 5
    cl.set_policy("bob", {})
    assert cl.policy("bob") == {}
    o = add_user(adm, "oper1", "operator")
    assert "quota" not in o.get("/api/meta").json()
