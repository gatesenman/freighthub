"""云端网页版（Vercel Serverless + Postgres）：用户 / 角色权限矩阵（RBAC）/ 会话 / 审计日志 / 首次设置。

由 main.py 在 FREIGHT_HUB_MODE=cloud 时安装。所有状态都在数据库里（Serverless 实例无状态、随时被回收）：
- 会话：只存令牌的 SHA-256；Cookie HttpOnly + Secure（HTTPS）+ SameSite=Lax；记住我 30 天，否则 8 小时空闲过期；
- CSRF：非 GET 请求必须带与会话绑定的 X-CSRF-Token，并校验 Origin；
- 登录限流：同 IP+用户名连续 5 次失败锁 5 分钟，同 IP 累计 25 次失败锁 15 分钟（数据库计数，多实例共享）；
- 权限：每个请求从数据库读取角色当前权限，改权限立刻生效；接口默认拒绝；
- 审计：谁（用户 / 角色）、做了什么（动作 / 对象 / 结果）、何时、从哪个 IP；只能追加，没有任何修改 / 删除接口。
本软件仍然只读：没有下单 / 重置密钥 / 改平台密码的接口。
"""
import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel

from . import rbac
from .server import (MAX_PASSWORD, MIN_PASSWORD, USERNAME_RE, check_new_password, hash_password, verify_password, _DUMMY)

COOKIE = "fh_session"
SAFE = ("GET", "HEAD", "OPTIONS")
STATIC = Path(__file__).resolve().parent / "static"
REMEMBER_SECONDS = 30 * 86400
SHORT_IDLE, SHORT_ABS = 8 * 3600, 24 * 3600
TZ = timezone(timedelta(hours=8))

ACTIONS = {
    "setup.admin": "首次设置创建管理员", "auth.login": "登录成功", "auth.login_failed": "登录失败", "auth.logout": "退出登录",
    "auth.password": "修改自己的密码", "access.denied": "越权访问被拒绝",
    "user.create": "新增用户", "user.update": "修改用户", "user.delete": "删除用户",
    "role.create": "新增角色", "role.update": "修改角色权限", "role.delete": "删除角色",
    "account.update": "修改平台账号", "account.test": "测试平台连接", "account.import": "导入平台账号", "account.export": "导出平台账号配置",
    "quote.run": "执行询价", "quote.limited": "询价被限额拒绝", "history.view": "查看他人历史", "history.export": "导出历史 CSV", "history.delete": "删除历史", "history.clear": "清空历史",
    "audit.export": "导出审计日志",
}


def env_bool(name, default=False):
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def _sha(t):
    return hashlib.sha256((t or "").encode()).hexdigest()


class LoginReq(BaseModel):
    username: str = ""
    password: str = ""
    remember: bool = True


class SetupReq(BaseModel):
    code: str = ""
    username: str = "admin"
    password: str = ""


class PwReq(BaseModel):
    old: str = ""
    new: str = ""


class UserReq(BaseModel):
    username: str = ""
    password: str = ""
    role: str = "operator"


class UserPatch(BaseModel):
    password: str | None = None
    role: str | None = None
    disabled: bool | None = None


class RoleReq(BaseModel):
    name: str = ""
    label: str = ""
    descr: str = ""
    perms: list[str] = []


class RolePatch(BaseModel):
    label: str | None = None
    descr: str | None = None
    perms: list[str] | None = None


def seed_roles(db):
    now = time.time()
    for name, label, descr, perms, system in rbac.DEFAULT_ROLES:
        db.x("INSERT INTO roles (name, label, descr, perms, system, created, updated) VALUES (:n,:l,:d,:p,:s,:t,:t) ON CONFLICT (name) DO NOTHING",
             n=name, l=label, d=descr, p=json.dumps(rbac.clean_perms(perms)), s=system, t=now)


# ---------------------------------------------------------------- 试用水印 / 页面输出
WM_TEXT = "未授权版本 / 试用"


def trial_watermark_on():
    return os.environ.get("FH_TRIAL_WATERMARK", "").strip().lower() in ("1", "true", "yes", "on")


def _wm_block():
    """水印：整屏斜铺 + 顶部徽标；内联脚本监视并还原被删除 / 隐藏 / 改样式的元素（防止随手 F12 去掉）。"""
    import base64
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="300" height="190"><text x="150" y="100" text-anchor="middle" '
           'transform="rotate(-28 150 95)" font-family="Microsoft YaHei,PingFang SC,sans-serif" font-size="26" font-weight="700" '
           'fill="#c42b1c" fill-opacity="0.16">' + WM_TEXT + '</text></svg>')
    uri = "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode()
    css_w = ("position:fixed!important;left:0!important;top:0!important;right:0!important;bottom:0!important;width:100vw!important;height:100vh!important;"
             "z-index:2147483646!important;pointer-events:none!important;display:block!important;opacity:1!important;visibility:visible!important;"
             "background-image:url(" + uri + ")!important;background-repeat:repeat!important;transform:none!important;filter:none!important;clip-path:none!important;mix-blend-mode:normal!important")
    css_b = ("position:fixed!important;left:50%!important;top:2px!important;transform:translateX(-50%)!important;z-index:2147483647!important;"
             "pointer-events:none!important;display:block!important;opacity:1!important;visibility:visible!important;background:#c42b1c!important;color:#fff!important;"
             "font:700 12px/18px 'Microsoft YaHei',sans-serif!important;padding:1px 14px!important;border-radius:9px!important;letter-spacing:1px!important;filter:none!important;clip-path:none!important")
    js = ("(function(){var W=%s,B=%s,T=%s;function ok(e,css){if(!e||!document.documentElement.contains(e))return false;"
          "var c=getComputedStyle(e);if(c.display==='none'||c.visibility!=='visible'||parseFloat(c.opacity)<0.9||c.pointerEvents!=='none')return false;"
          "var r=e.getBoundingClientRect();return r.width>40&&r.height>10&&e.getAttribute('style')===css&&e.parentNode===document.body&&e.textContent===(e.id==='fh-wmb'?T:'')}"
          "function mk(id,css,t){var o=document.getElementById(id);if(o&&o.parentNode)o.parentNode.removeChild(o);var d=document.createElement('div');d.id=id;"
          "d.setAttribute('style',css);d.setAttribute('aria-hidden','true');if(t)d.textContent=t;document.body.appendChild(d);return d}"
          "function chk(){var w=document.getElementById('fh-wm'),b=document.getElementById('fh-wmb');if(!ok(w,W))mk('fh-wm',W,'');if(!ok(b,B))mk('fh-wmb',B,T);"
          "if(document.body.lastChild.id!=='fh-wmb'&&document.body.lastChild.id!=='fh-wm'){document.body.appendChild(document.getElementById('fh-wm'));document.body.appendChild(document.getElementById('fh-wmb'))}}"
          "function go(){chk();new MutationObserver(chk).observe(document.documentElement,{childList:true,subtree:true,attributes:true,attributeFilter:['style','class','id','hidden']});setInterval(chk,700)}"
          "if(document.body)go();else document.addEventListener('DOMContentLoaded',go)})();") % (json.dumps(css_w), json.dumps(css_b), json.dumps(WM_TEXT))
    return ('<div id="fh-wm" style="%s" aria-hidden="true"></div><div id="fh-wmb" style="%s" aria-hidden="true">%s</div><script>%s</script>'
            % (css_w, css_b, WM_TEXT, js))


def render_page(name):
    """输出 HTML 页面；FH_TRIAL_WATERMARK=1 时注入水印（登录页 / 设置页 / 主界面都经过这里）。"""
    html = (STATIC / name).read_text(encoding="utf-8")
    if trial_watermark_on():
        html = html.replace("</body>", _wm_block() + "</body>", 1) if "</body>" in html else html + _wm_block()
    return Response(html, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})


def client_ip(request, trust):
    if trust:
        for h in ("x-vercel-forwarded-for", "x-real-ip"):
            v = request.headers.get(h)
            if v:
                return v.split(",")[0].strip()[:64]
        xf = request.headers.get("x-forwarded-for")
        if xf:
            return xf.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "?")[:64]


def origin_ok(request, allowed, trust):
    origin = request.headers.get("origin")
    if not origin:
        return True
    from urllib.parse import urlparse
    o = urlparse(origin).netloc.lower()
    hosts = {request.headers.get("host", "").lower()}
    if trust and request.headers.get("x-forwarded-host"):
        hosts.add(request.headers["x-forwarded-host"].split(",")[0].strip().lower())
    return o in hosts or o in allowed


class Cloud:
    def __init__(self, db):
        self.db = db
        self.trust = env_bool("FREIGHT_HUB_TRUST_PROXY") or bool(os.environ.get("VERCEL"))
        self.https = env_bool("FREIGHT_HUB_HTTPS") or bool(os.environ.get("VERCEL"))
        self.allowed = {x.strip().lower() for x in os.environ.get("FREIGHT_HUB_ALLOWED_ORIGINS", "").split(",") if x.strip()}

    # ---------- 角色 ----------
    def role(self, name):
        r = self.db.one("SELECT * FROM roles WHERE name=:n", n=name)
        if r:
            r["perms"] = json.loads(r["perms"])
        return r

    def roles(self):
        cnt = {r["role"]: r["c"] for r in self.db.q("SELECT role, COUNT(*) AS c FROM users GROUP BY role")}
        out = []
        for r in self.db.q("SELECT * FROM roles ORDER BY system DESC, name"):
            perms = rbac.clean_perms(json.loads(r["perms"]))
            if r["system"]:
                perms = list(rbac.PERM_KEYS)
            out.append({"name": r["name"], "label": r["label"], "descr": r["descr"], "perms": perms, "system": bool(r["system"]), "users": int(cnt.get(r["name"], 0))})
        return out

    # ---------- 用户 ----------
    def user_count(self):
        return int(self.db.one("SELECT COUNT(*) AS c FROM users")["c"])

    def users(self):
        rows = self.db.q("SELECT u.username, u.role, u.disabled, u.created, u.last_login, u.last_ip, r.label FROM users u LEFT JOIN roles r ON r.name=u.role ORDER BY LOWER(u.username)")
        return [{"username": r["username"], "role": r["role"], "role_label": r["label"] or r["role"], "disabled": bool(r["disabled"]),
                 "created": r["created"], "last_login": r["last_login"], "last_ip": r["last_ip"]} for r in rows]

    # 按用户的策略（meta 表 policy:<用户名小写> 的 JSON）：qph = 每小时最多询价次数；no_pw = 不允许自己改密码（共享的试用账号）
    def policy(self, name):
        r = self.db.one("SELECT v FROM meta WHERE k=:k", k="policy:" + (name or "").lower())
        try:
            return json.loads(r["v"]) if r else {}
        except Exception:
            return {}

    def set_policy(self, name, pol):
        k = "policy:" + name.lower()
        self.db.x("DELETE FROM meta WHERE k=:k", k=k)
        if pol:
            self.db.x("INSERT INTO meta (k, v) VALUES (:k, :v)", k=k, v=json.dumps(pol))

    def quota(self, name):
        """(上限, 本小时已用)；没有限额返回 (0, 0)。"""
        lim = int(self.policy(name).get("qph") or 0)
        if not lim:
            return 0, 0
        r = self.db.one("SELECT COUNT(*) AS c FROM audit WHERE username=:u AND action='quote.run' AND ts>:t", u=name, t=time.time() - 3600)
        return lim, int(r["c"] if r else 0)

    def get_user(self, name):
        return self.db.one("SELECT * FROM users WHERE username_lc=:u", u=(name or "").lower())

    def active_admins(self, exclude=None):
        sql = "SELECT username_lc FROM users WHERE disabled=0 AND role='admin'"
        p = {}
        if exclude:
            sql += " AND username_lc<>:e"
            p["e"] = exclude.lower()
        return self.db.q(sql, **p)

    # ---------- 会话 ----------
    def session(self, token):
        if not token:
            return None
        th = _sha(token)
        r = self.db.one("SELECT s.th, s.csrf, s.created, s.seen, s.remember, u.username, u.role, u.disabled, u.must_change, r.perms, r.label, r.system "
                        "FROM sessions s JOIN users u ON u.username_lc=s.username_lc LEFT JOIN roles r ON r.name=u.role WHERE s.th=:t", t=th)
        if not r:
            return None
        now = time.time()
        idle, absolute = (REMEMBER_SECONDS, REMEMBER_SECONDS) if r["remember"] else (SHORT_IDLE, SHORT_ABS)
        if r["disabled"] or now - r["seen"] > idle or now - r["created"] > absolute:
            self.db.x("DELETE FROM sessions WHERE th=:t", t=th)
            return None
        if now - r["seen"] > 300:
            self.db.x("UPDATE sessions SET seen=:n WHERE th=:t", n=now, t=th)
        perms = set(rbac.PERM_KEYS) if r["system"] else set(rbac.clean_perms(json.loads(r["perms"] or "[]")))
        return {"name": r["username"], "role": r["role"], "role_label": r["label"] or r["role"], "perms": perms, "csrf": r["csrf"], "th": th, "remember": bool(r["remember"])}

    def new_session(self, username, request, remember):
        tok = secrets.token_urlsafe(32)
        now = time.time()
        self.db.x("INSERT INTO sessions (th, username_lc, csrf, created, seen, ip, ua, remember) VALUES (:t,:u,:c,:n,:n,:i,:a,:r)",
                  t=_sha(tok), u=username.lower(), c=secrets.token_urlsafe(24), n=now, i=client_ip(request, self.trust),
                  a=(request.headers.get("user-agent") or "")[:200], r=1 if remember else 0)
        self.db.x("DELETE FROM sessions WHERE seen<:t", t=now - REMEMBER_SECONDS - 86400)     # 顺手清理过期会话
        return tok

    def drop_user_sessions(self, username, keep=None):
        if keep:
            self.db.x("DELETE FROM sessions WHERE username_lc=:u AND th<>:k", u=username.lower(), k=keep)
        else:
            self.db.x("DELETE FROM sessions WHERE username_lc=:u", u=username.lower())

    # ---------- 登录限流 ----------
    def blocked(self, key):
        r = self.db.one("SELECT lock_until FROM throttle WHERE k=:k", k=key)
        return bool(r and r["lock_until"] > time.time())

    def fail(self, key, limit, lock):
        now = time.time()
        r = self.db.one("SELECT n, lock_until, ts FROM throttle WHERE k=:k", k=key)
        stale = (not r) or (r["lock_until"] and r["lock_until"] < now) or (r["ts"] or 0) < now - 3600
        n = 1 if stale else r["n"] + 1
        until = now + lock if n >= limit else 0
        self.db.x("INSERT INTO throttle (k, n, lock_until, ts) VALUES (:k,:n,:u,:t) ON CONFLICT (k) DO UPDATE SET n=EXCLUDED.n, lock_until=EXCLUDED.lock_until, ts=EXCLUDED.ts",
                  k=key, n=n, u=until, t=now)

    def ok(self, key):
        self.db.x("DELETE FROM throttle WHERE k=:k", k=key)

    # ---------- 审计 ----------
    def audit(self, request, action, target="", status="ok", detail=None, user=None):
        u = user or (getattr(request.state, "user", None) if request is not None else None) or {}
        try:
            d = json.dumps(detail, ensure_ascii=False)[:2000] if detail not in (None, "") else None
            self.db.x("INSERT INTO audit (ts, username, role, ip, ua, action, target, status, detail) VALUES (:t,:u,:r,:i,:a,:ac,:tg,:s,:d)",
                      t=time.time(), u=(u.get("name") or "")[:64], r=(u.get("role") or "")[:32],
                      i=client_ip(request, self.trust) if request is not None else "", a=((request.headers.get("user-agent") or "")[:200] if request is not None else ""),
                      ac=action, tg=(target or "")[:200], s=status, d=d)
        except Exception as e:  # noqa  审计写失败不能拖垮业务，但要留痕到日志
            print(f"[FreightHub] 审计写入失败：{type(e).__name__}", flush=True)

    def audit_query(self, f, limit, offset):
        where, p = [], {}
        if f.get("user"):
            where.append("LOWER(username) LIKE :u"); p["u"] = "%" + f["user"].lower() + "%"
        if f.get("action"):
            where.append("action=:ac"); p["ac"] = f["action"]
        if f.get("status") in ("ok", "fail"):
            where.append("status=:st"); p["st"] = f["status"]
        if f.get("ip"):
            where.append("ip LIKE :ip"); p["ip"] = f["ip"] + "%"
        if f.get("q"):
            where.append("(LOWER(COALESCE(target,'')) LIKE :q OR LOWER(COALESCE(detail,'')) LIKE :q)"); p["q"] = "%" + f["q"].lower() + "%"
        if f.get("since") is not None:
            where.append("ts>=:s"); p["s"] = f["since"]
        if f.get("until") is not None:
            where.append("ts<=:e"); p["e"] = f["until"]
        w = (" WHERE " + " AND ".join(where)) if where else ""
        total = int(self.db.one("SELECT COUNT(*) AS c FROM audit" + w, **p)["c"])
        rows = self.db.q("SELECT id, ts, username, role, ip, ua, action, target, status, detail FROM audit" + w + " ORDER BY id DESC LIMIT :lim OFFSET :off", lim=limit, off=offset, **p)
        return total, rows


def _csv_safe(v):
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def _fnum(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def install(app, store):
    from .db import Db  # noqa
    cl = Cloud(store.db)
    app.state.cloud = cl
    app.state.audit = cl.audit
    setup_code = lambda: os.environ.get("FH_SETUP_CODE", "")  # noqa

    def secure(request):
        return cl.https or (cl.trust and request.headers.get("x-forwarded-proto", "").lower() == "https")

    def err(status, msg):
        return JSONResponse({"detail": msg}, status_code=status)

    def deny(status, msg, request):
        if request.url.path.startswith("/api/"):
            return err(status, msg)
        return RedirectResponse("/login", status_code=302, headers={"Cache-Control": "no-store"}) if status == 401 else err(status, msg)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path, method = request.url.path, request.method.upper()
        request.state.user = None
        public_page = path in ("/login", "/setup", "/favicon.ico") or path.startswith("/static/")
        sess = None
        tok = request.cookies.get(COOKIE)
        try:
            if tok and not path.startswith("/static/"):
                sess = cl.session(tok)
            request.state.user = sess
            if method not in SAFE and not origin_ok(request, cl.allowed, cl.trust):
                resp = err(403, "来源校验失败（Origin）")
            elif public_page or rbac.is_public(method, path):
                resp = await call_next(request)
            elif not sess:
                resp = deny(401, "未登录或登录已过期", request)
            elif not path.startswith("/api/"):
                resp = await call_next(request)      # 首页等：登录即可
            else:
                known, perm = rbac.match_route(method, path)
                if not known:
                    resp = err(403, "该接口未开放")
                elif perm and perm not in sess["perms"]:
                    cl.audit(request, "access.denied", f"{method} {path}", "fail", {"need": perm})
                    resp = err(403, f"没有权限：{dict((p[0], p[1]) for p in rbac.PERMISSIONS).get(perm, perm)}")
                elif method not in SAFE and not hmac.compare_digest(request.headers.get("x-csrf-token", ""), sess["csrf"]):
                    resp = err(403, "CSRF 校验失败，请刷新页面重试")
                else:
                    resp = await call_next(request)
        except Exception as e:  # noqa  数据库不可用等：不泄露细节
            print(f"[FreightHub] 请求处理失败：{type(e).__name__}: {str(e)[:200]}", flush=True)
            resp = err(503, "服务暂时不可用（数据库连接失败），请稍后重试")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if path.startswith("/api/") or path in ("/", "/login", "/setup"):
            resp.headers["Cache-Control"] = "no-store"
        if secure(request):
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return resp

    # ---------- 页面 ----------
    @app.get("/login")
    def login_page(request: Request):
        if request.state.user:
            return RedirectResponse("/", status_code=302, headers={"Cache-Control": "no-store"})
        if cl.user_count() == 0:
            return RedirectResponse("/setup", status_code=302, headers={"Cache-Control": "no-store"})
        return render_page("login.html")

    @app.get("/setup")
    def setup_page(request: Request):
        if cl.user_count() > 0:
            return RedirectResponse("/login", status_code=302, headers={"Cache-Control": "no-store"})
        return render_page("setup.html")

    @app.get("/api/setup/status")
    def setup_status():
        return {"needed": cl.user_count() == 0, "enabled": bool(setup_code())}

    @app.post("/api/setup")
    def setup(req: SetupReq, request: Request):
        ip = client_ip(request, cl.trust)
        key = "setup:" + ip
        if cl.blocked(key):
            return err(429, "尝试次数过多，请稍后再试")
        if cl.user_count() > 0:
            return err(409, "系统已经初始化")
        code = setup_code()
        if not code:
            return err(403, "未启用首次设置（服务端没有配置 FH_SETUP_CODE）")
        if not hmac.compare_digest(req.code.strip(), code):
            cl.fail(key, 5, 900)
            cl.audit(request, "setup.admin", req.username[:64], "fail", {"reason": "设置码错误"}, user={"name": req.username[:64]})
            return err(403, "设置码不正确")
        name = req.username.strip()
        if not USERNAME_RE.match(name):
            return err(400, "用户名只能含字母、数字、_ . @ -，长度 2–32")
        e = check_new_password(req.password)
        if e:
            return err(400, e)
        n = cl.db.x("INSERT INTO users (username_lc, username, pw, role, disabled, created, must_change) "
                    "SELECT CAST(:l AS TEXT),CAST(:u AS TEXT),CAST(:p AS TEXT),'admin',0,CAST(:t AS DOUBLE PRECISION),0 WHERE NOT EXISTS (SELECT 1 FROM users)", l=name.lower(), u=name, p=hash_password(req.password), t=time.time())
        if n != 1:
            return err(409, "系统已经初始化")
        cl.audit(request, "setup.admin", name, "ok", user={"name": name, "role": "admin"})
        return {"ok": True}

    # ---------- 认证 ----------
    @app.post("/api/auth/login")
    def login(req: LoginReq, request: Request):
        ip = client_ip(request, cl.trust)
        uname = (req.username or "").strip()[:64]
        k1, k2 = f"u:{ip}|{uname.lower()}", f"ip:{ip}"
        if cl.blocked(k1) or cl.blocked(k2):
            cl.audit(request, "auth.login_failed", uname, "fail", {"reason": "限流锁定"}, user={"name": uname})
            return err(429, "失败次数过多，请稍后再试")
        u = cl.get_user(uname)
        good = verify_password(req.password or "", u["pw"] if u else _DUMMY) and u is not None
        if good and u["disabled"]:
            good, why = False, "账号已停用"
        else:
            why = "用户名或密码错误"
        if not good:
            cl.fail(k1, 5, 300)
            cl.fail(k2, 25, 900)
            cl.audit(request, "auth.login_failed", uname, "fail", {"reason": why}, user={"name": uname})
            return err(401, "用户名或密码错误")
        cl.ok(k1)
        old = request.cookies.get(COOKIE)
        if old:
            cl.db.x("DELETE FROM sessions WHERE th=:t", t=_sha(old))
        tok = cl.new_session(u["username"], request, req.remember)
        cl.db.x("UPDATE users SET last_login=:t, last_ip=:i WHERE username_lc=:u", t=time.time(), i=ip, u=u["username_lc"])
        s = cl.session(tok)
        cl.audit(request, "auth.login", u["username"], "ok", {"remember": bool(req.remember)}, user=s)
        resp = JSONResponse({"user": s["name"], "role": s["role"], "csrf": s["csrf"]})
        kw = {"max_age": REMEMBER_SECONDS} if req.remember else {}
        resp.set_cookie(COOKIE, tok, httponly=True, samesite="lax", secure=secure(request), path="/", **kw)
        return resp

    @app.post("/api/auth/logout")
    def logout(request: Request):
        s = request.state.user
        cl.audit(request, "auth.logout", s["name"])
        cl.db.x("DELETE FROM sessions WHERE th=:t", t=s["th"])
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @app.get("/api/auth/me")
    def me(request: Request):
        u = request.state.user
        return {"user": u["name"], "role": u["role"], "role_label": u["role_label"], "perms": sorted(u["perms"]), "csrf": u["csrf"]}

    @app.post("/api/auth/password")
    def change_password(req: PwReq, request: Request):
        s = request.state.user
        if cl.policy(s["name"]).get("no_pw"):
            cl.audit(request, "auth.password", s["name"], "fail", {"reason": "试用账号不允许改密码"})
            return err(403, "试用账号是共享账号，不能修改密码")
        u = cl.get_user(s["name"])
        if not verify_password(req.old or "", u["pw"]):
            cl.audit(request, "auth.password", s["name"], "fail", {"reason": "原密码不正确"})
            return err(400, "原密码不正确")
        e = check_new_password(req.new)
        if e:
            return err(400, e)
        cl.db.x("UPDATE users SET pw=:p, must_change=0 WHERE username_lc=:u", p=hash_password(req.new), u=u["username_lc"])
        cl.drop_user_sessions(s["name"], keep=s["th"])
        cl.audit(request, "auth.password", s["name"])
        return {"ok": True}

    # ---------- 用户管理 ----------
    def can_assign(actor, role):
        """只能分配权限不超过自己的角色（防提权）。"""
        r = cl.role(role)
        if not r:
            return None
        perms = set(rbac.PERM_KEYS) if r["system"] else set(r["perms"])
        return perms <= actor["perms"]

    @app.get("/api/users")
    def list_users():
        return cl.users()

    @app.post("/api/users")
    def create_user(req: UserReq, request: Request):
        a = request.state.user
        name = req.username.strip()
        if not USERNAME_RE.match(name):
            return err(400, "用户名只能含字母、数字、_ . @ -，长度 2–32")
        e = check_new_password(req.password)
        if e:
            return err(400, e)
        ok = can_assign(a, req.role)
        if ok is None:
            return err(400, "角色不存在")
        if not ok:
            cl.audit(request, "user.create", name, "fail", {"role": req.role, "reason": "不能分配高于自己权限的角色"})
            return err(403, "不能分配权限高于自己的角色")
        if cl.get_user(name):
            return err(400, "用户名已存在")
        cl.db.x("INSERT INTO users (username_lc, username, pw, role, disabled, created, must_change) VALUES (:l,:u,:p,:r,0,:t,0)",
                l=name.lower(), u=name, p=hash_password(req.password), r=req.role, t=time.time())
        cl.audit(request, "user.create", name, detail={"role": req.role})
        return cl.users()

    @app.put("/api/users/{name}")
    def update_user(name: str, req: UserPatch, request: Request):
        a = request.state.user
        u = cl.get_user(name)
        if not u:
            return err(404, "用户不存在")
        selfie = u["username_lc"] == a["name"].lower()
        if u["role"] == "admin" and a["role"] != "admin":
            return err(403, "只有系统管理员可以修改管理员账号")
        changes = {}
        sets, p = [], {"u": u["username_lc"]}
        if req.role is not None and req.role != u["role"]:
            ok = can_assign(a, req.role)
            if ok is None:
                return err(400, "角色不存在")
            if not ok:
                cl.audit(request, "user.update", u["username"], "fail", {"role": req.role, "reason": "不能分配高于自己权限的角色"})
                return err(403, "不能分配权限高于自己的角色")
            if selfie:
                return err(400, "不能修改自己的角色")
            if u["role"] == "admin" and not cl.active_admins(exclude=u["username_lc"]):
                return err(400, "至少要保留一个启用的系统管理员")
            sets.append("role=:r"); p["r"] = req.role; changes["role"] = f"{u['role']} → {req.role}"
        if req.disabled is not None and bool(req.disabled) != bool(u["disabled"]):
            if req.disabled and selfie:
                return err(400, "不能停用自己")
            if req.disabled and u["role"] == "admin" and not cl.active_admins(exclude=u["username_lc"]):
                return err(400, "至少要保留一个启用的系统管理员")
            sets.append("disabled=:d"); p["d"] = 1 if req.disabled else 0; changes["disabled"] = bool(req.disabled)
        if req.password is not None:
            e = check_new_password(req.password)
            if e:
                return err(400, e)
            sets.append("pw=:pw"); p["pw"] = hash_password(req.password); changes["password"] = "已重置"
        if sets:
            cl.db.x("UPDATE users SET " + ", ".join(sets) + " WHERE username_lc=:u", **p)
            if "pw" in p or p.get("d") == 1 or "r" in p:
                cl.drop_user_sessions(u["username"], keep=a["th"] if selfie else None)
        cl.audit(request, "user.update", u["username"], detail=changes)
        return cl.users()

    @app.delete("/api/users/{name}")
    def delete_user(name: str, request: Request):
        a = request.state.user
        u = cl.get_user(name)
        if not u:
            return err(404, "用户不存在")
        if u["username_lc"] == a["name"].lower():
            return err(400, "不能删除自己")
        if u["role"] == "admin" and (a["role"] != "admin" or not cl.active_admins(exclude=u["username_lc"])):
            return err(400, "不能删除管理员（至少保留一个启用的系统管理员，且只有管理员能删除管理员）")
        cl.db.x("DELETE FROM users WHERE username_lc=:u", u=u["username_lc"])
        cl.drop_user_sessions(u["username"])
        cl.audit(request, "user.delete", u["username"], detail={"role": u["role"]})
        return cl.users()

    # ---------- 角色 / 权限矩阵 ----------
    @app.get("/api/roles")
    def list_roles():
        return {"roles": cl.roles(), "permissions": [{"key": k, "label": l, "group": g, "descr": d} for k, l, g, d in rbac.PERMISSIONS], "implies": rbac.IMPLIES}

    def check_perms(a, perms):
        perms = rbac.close_perms(perms)
        extra = set(perms) - a["perms"]
        if extra:
            return None, f"不能授予自己没有的权限：{', '.join(sorted(extra))}"
        return perms, None

    @app.post("/api/roles")
    def create_role(req: RoleReq, request: Request):
        a = request.state.user
        name = req.name.strip().lower()
        if not rbac.valid_role_name(name):
            return err(400, "角色标识只能用小写字母 / 数字 / 下划线，字母开头，2–24 位")
        if not req.label.strip():
            return err(400, "请填写角色名称")
        if cl.role(name):
            return err(400, "角色标识已存在")
        perms, e = check_perms(a, req.perms)
        if e:
            return err(403, e)
        now = time.time()
        cl.db.x("INSERT INTO roles (name, label, descr, perms, system, created, updated) VALUES (:n,:l,:d,:p,0,:t,:t)",
                n=name, l=req.label.strip()[:40], d=req.descr.strip()[:200], p=json.dumps(perms), t=now)
        cl.audit(request, "role.create", name, detail={"perms": perms})
        return list_roles()

    @app.put("/api/roles/{name}")
    def update_role(name: str, req: RolePatch, request: Request):
        a = request.state.user
        r = cl.role(name)
        if not r:
            return err(404, "角色不存在")
        if r["system"]:
            return err(400, "系统内置角色不可修改")
        sets, p, changes = ["updated=:t"], {"n": name, "t": time.time()}, {}
        if req.label is not None:
            if not req.label.strip():
                return err(400, "角色名称不能为空")
            sets.append("label=:l"); p["l"] = req.label.strip()[:40]
        if req.descr is not None:
            sets.append("descr=:d"); p["d"] = req.descr.strip()[:200]
        if req.perms is not None:
            perms, e = check_perms(a, req.perms)
            # 只能调整自己有权限的那部分：如果角色原本含有自己没有的权限，也不能改动它们
            locked = set(r["perms"]) - a["perms"]
            if e:
                return err(403, e)
            if locked:
                return err(403, "该角色包含你自己没有的权限，不能修改")
            added, removed = sorted(set(perms) - set(r["perms"])), sorted(set(r["perms"]) - set(perms))
            sets.append("perms=:p"); p["p"] = json.dumps(perms)
            changes.update(added=added, removed=removed)
        cl.db.x("UPDATE roles SET " + ", ".join(sets) + " WHERE name=:n", **p)
        cl.audit(request, "role.update", name, detail=changes or {"label": req.label, "descr": req.descr})
        return list_roles()

    @app.delete("/api/roles/{name}")
    def delete_role(name: str, request: Request):
        r = cl.role(name)
        if not r:
            return err(404, "角色不存在")
        if r["system"]:
            return err(400, "系统内置角色不可删除")
        n = int(cl.db.one("SELECT COUNT(*) AS c FROM users WHERE role=:r", r=name)["c"])
        if n:
            return err(400, f"还有 {n} 个用户使用该角色，请先改为其他角色")
        cl.db.x("DELETE FROM roles WHERE name=:n", n=name)
        cl.audit(request, "role.delete", name)
        return list_roles()

    # ---------- 审计日志 ----------
    def filters(request):
        g = request.query_params.get
        return {"user": (g("user") or "").strip()[:64], "action": (g("action") or "").strip()[:40], "status": g("status") or "",
                "ip": (g("ip") or "").strip()[:64], "q": (g("q") or "").strip()[:100], "since": _fnum(g("since")), "until": _fnum(g("until"))}

    def fmt_ts(t):
        return datetime.fromtimestamp(t, TZ).strftime("%Y-%m-%d %H:%M:%S")

    @app.get("/api/audit")
    def audit_list(request: Request):
        try:
            page = max(1, int(request.query_params.get("page", "1")))
            size = min(200, max(10, int(request.query_params.get("size", "50"))))
        except ValueError:
            return err(400, "分页参数不正确")
        total, rows = cl.audit_query(filters(request), size, (page - 1) * size)
        for r in rows:
            r["action_label"] = ACTIONS.get(r["action"], r["action"])
            r["time"] = fmt_ts(r["ts"])
            try:
                r["detail"] = json.loads(r["detail"]) if r["detail"] else None
            except ValueError:
                pass
        return {"total": total, "page": page, "size": size, "items": rows, "actions": [{"key": k, "label": v} for k, v in ACTIONS.items()]}

    @app.get("/api/audit/export")
    def audit_export(request: Request):
        f = filters(request)
        total, rows = cl.audit_query(f, 50000, 0)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["时间(UTC+8)", "用户", "角色", "IP", "操作", "操作代码", "对象", "结果", "详情", "客户端"])
        for r in rows:
            w.writerow([_csv_safe(c) for c in (fmt_ts(r["ts"]), r["username"], r["role"], r["ip"], ACTIONS.get(r["action"], r["action"]), r["action"],
                                               r["target"], "成功" if r["status"] == "ok" else "失败", r["detail"], r["ua"])])
        cl.audit(request, "audit.export", f"{len(rows)} 条", detail={k: v for k, v in f.items() if v not in (None, "")})
        fn = "audit_" + datetime.now(TZ).strftime("%Y%m%d_%H%M%S") + ".csv"
        return Response(("\ufeff" + buf.getvalue()).encode("utf-8"), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename={fn}", "Cache-Control": "no-store"})
