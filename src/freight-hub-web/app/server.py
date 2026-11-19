"""1.4.0 网页 / 服务器模式：用户系统、会话、CSRF、角色权限。

只在环境变量 FREIGHT_HUB_MODE=server 时由 main.py 安装；桌面版完全不受影响。

- 用户保存在 data/users.json（0600），口令用 PBKDF2-HMAC-SHA256（24 万次迭代、随机盐）存储，不存明文。
- 会话：服务端内存 + HttpOnly / SameSite=Lax Cookie（HTTPS 时加 Secure）；登录时换新令牌（防固定会话）。
- CSRF：所有非 GET/HEAD/OPTIONS 请求必须带 X-CSRF-Token（与会话绑定），并校验 Origin 与 Host 一致。
- 角色：admin（全部，含平台账号 / 导入导出 / 用户管理）、user（只能询价和看自己的历史）。
  接口默认「仅管理员」，只有明确列入 USER_ROUTES 的才对普通用户开放（默认拒绝）。
- 登录限流：同一 IP+用户名连续 5 次失败，锁定 5 分钟。
本软件仍然是只读：这里没有任何下单 / 重置密钥 / 改平台密码的接口；「修改密码」只指修改本软件自己的登录口令。
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from .store import _write_private

COOKIE = "fh_session"
ITERATIONS = 240_000
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{2,32}$")
MIN_PASSWORD = 8
MAX_PASSWORD = 256
MAX_FAILS = 5
LOCK_SECONDS = 300
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
STATIC = Path(__file__).resolve().parent / "static"

# 普通用户（role=user）可以访问的接口；其余 /api/* 一律只有管理员能访问。
USER_ROUTES = [
    ("GET", r"/api/meta"), ("GET", r"/api/platforms"), ("POST", r"/api/quote"),
    ("GET", r"/api/history"), ("GET", r"/api/history/\d+"), ("GET", r"/api/history/\d+/csv"),
    ("DELETE", r"/api/history"), ("DELETE", r"/api/history/\d+"),
    ("GET", r"/api/auth/me"), ("POST", r"/api/auth/logout"), ("POST", r"/api/auth/password"),
    ("GET", r"/api/accounts/template"),      # 导入模板只是空表头，不含任何凭据
]
PUBLIC_ROUTES = [("GET", r"/api/ping"), ("POST", r"/api/auth/login")]


def env_bool(name, default=False):
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


# ---------------- 口令 ----------------
def hash_password(pw: str, iters: int = ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, iters)
    return "pbkdf2_sha256$%d$%s$%s" % (iters, base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(pw: str, enc: str) -> bool:
    try:
        algo, iters, salt, dk = enc.split("$")
        if algo != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), base64.b64decode(salt), int(iters))
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except Exception:
        return False


_DUMMY = hash_password("dummy-password-for-timing", 1000)


def check_new_password(pw: str):
    if not isinstance(pw, str) or len(pw) < MIN_PASSWORD:
        return f"密码至少 {MIN_PASSWORD} 位"
    if len(pw) > MAX_PASSWORD:
        return "密码过长"
    return None


# ---------------- 用户库 ----------------
class UserStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.RLock()

    def _read(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"users": {}}
        except Exception:
            # 损坏时不要悄悄覆盖，先备份
            try:
                _write_private(self.path.with_suffix(".json.bak"), self.path.read_bytes())
            except Exception:
                pass
            return {"users": {}}

    def _write(self, d):
        tmp = self.path.with_suffix(".tmp")
        _write_private(tmp, json.dumps(d, ensure_ascii=False, indent=2).encode("utf-8"))
        os.replace(tmp, self.path)

    def all(self):
        with self.lock:
            return self._read()["users"]

    def get(self, name):
        return self.all().get((name or "").lower())

    def public_list(self):
        return [{"username": u["username"], "role": u["role"], "disabled": bool(u.get("disabled")), "created": u.get("created"),
                 "last_login": u.get("last_login")} for u in sorted(self.all().values(), key=lambda x: x["username"].lower())]

    def admins(self):
        return [u for u in self.all().values() if u["role"] == "admin" and not u.get("disabled")]

    def create(self, username, password, role="user"):
        if not USERNAME_RE.match(username or ""):
            raise ValueError("用户名只能含字母、数字、_ . @ -，长度 2–32")
        if role not in ("admin", "user"):
            raise ValueError("角色只能是 admin 或 user")
        e = check_new_password(password)
        if e:
            raise ValueError(e)
        with self.lock:
            d = self._read()
            if username.lower() in d["users"]:
                raise ValueError("用户名已存在")
            d["users"][username.lower()] = {"username": username, "role": role, "hash": hash_password(password), "created": time.time(), "disabled": False}
            self._write(d)

    def update(self, username, password=None, role=None, disabled=None):
        with self.lock:
            d = self._read()
            u = d["users"].get(username.lower())
            if not u:
                raise KeyError(username)
            if role is not None:
                if role not in ("admin", "user"):
                    raise ValueError("角色只能是 admin 或 user")
                u["role"] = role
            if disabled is not None:
                u["disabled"] = bool(disabled)
            if password is not None:
                e = check_new_password(password)
                if e:
                    raise ValueError(e)
                u["hash"] = hash_password(password)
            # 不允许把最后一个可用管理员降级 / 停用
            if not [x for x in d["users"].values() if x["role"] == "admin" and not x.get("disabled")]:
                raise ValueError("至少要保留一个可用的管理员")
            self._write(d)

    def delete(self, username):
        with self.lock:
            d = self._read()
            if username.lower() not in d["users"]:
                raise KeyError(username)
            u = d["users"].pop(username.lower())
            if u["role"] == "admin" and not [x for x in d["users"].values() if x["role"] == "admin" and not x.get("disabled")]:
                raise ValueError("至少要保留一个可用的管理员")
            self._write(d)

    def touch_login(self, username):
        with self.lock:
            d = self._read()
            if username.lower() in d["users"]:
                d["users"][username.lower()]["last_login"] = time.time()
                self._write(d)

    def authenticate(self, username, password):
        u = self.get(username)
        enc = u["hash"] if u else _DUMMY       # 用户不存在也做一次哈希，避免通过耗时探测用户名
        ok = verify_password(password or "", enc)
        return u if (u and ok and not u.get("disabled")) else None


# ---------------- 会话 ----------------
class Sessions:
    def __init__(self, idle=8 * 3600, absolute=24 * 3600):
        self.idle, self.absolute = idle, absolute
        self.d = {}
        self.lock = threading.Lock()

    def create(self, username, role):
        tok = secrets.token_urlsafe(32)
        now = time.time()
        with self.lock:
            self.d[tok] = {"name": username, "role": role, "csrf": secrets.token_urlsafe(24), "created": now, "seen": now}
        return tok

    def get(self, tok):
        if not tok:
            return None
        with self.lock:
            s = self.d.get(tok)
            if not s:
                return None
            now = time.time()
            if now - s["seen"] > self.idle or now - s["created"] > self.absolute:
                self.d.pop(tok, None)
                return None
            s["seen"] = now
            return s

    def drop(self, tok):
        with self.lock:
            self.d.pop(tok, None)

    def drop_user(self, username):
        with self.lock:
            for t in [t for t, s in self.d.items() if s["name"].lower() == username.lower()]:
                del self.d[t]

    def refresh_role(self, username, role):
        with self.lock:
            for s in self.d.values():
                if s["name"].lower() == username.lower():
                    s["role"] = role


class Throttle:
    def __init__(self):
        self.d, self.lock = {}, threading.Lock()

    def blocked(self, key):
        with self.lock:
            e = self.d.get(key)
            return bool(e and e[1] > time.time())

    def fail(self, key):
        with self.lock:
            n, until = self.d.get(key, (0, 0))
            n = 1 if until and until < time.time() else n + 1
            self.d[key] = (n, time.time() + LOCK_SECONDS if n >= MAX_FAILS else 0)

    def ok(self, key):
        with self.lock:
            self.d.pop(key, None)


# ---------------- 请求辅助 ----------------
def match(table, method, path):
    return any(m == method and re.fullmatch(p, path) for m, p in table)


def required_role(method, path):
    """返回 None（公开）/ 'user' / 'admin'。"""
    if method == "HEAD":
        method = "GET"
    if path in ("/login", "/favicon.ico") or path.startswith("/static/"):
        return None
    if not path.startswith("/api/"):
        return "user"        # 首页等：登录后才能看
    if match(PUBLIC_ROUTES, method, path):
        return None
    if match(USER_ROUTES, method, path):
        return "user"
    return "admin"


def client_ip(request, trust_proxy):
    if trust_proxy:
        xf = request.headers.get("x-forwarded-for")
        if xf:
            return xf.split(",")[0].strip()
    return request.client.host if request.client else "?"


def origin_ok(request, allowed, trust_proxy):
    origin = request.headers.get("origin")
    if not origin:
        return True          # 非浏览器客户端（curl / 脚本）没有 Origin；它们没有 Cookie 可被借用，仍需会话 + CSRF 令牌
    from urllib.parse import urlparse
    o = urlparse(origin).netloc.lower()
    hosts = {request.headers.get("host", "").lower()}
    if trust_proxy and request.headers.get("x-forwarded-host"):
        hosts.add(request.headers["x-forwarded-host"].split(",")[0].strip().lower())
    return o in hosts or o in allowed


class LoginReq(BaseModel):
    username: str = ""
    password: str = ""


class PwReq(BaseModel):
    old: str = ""
    new: str = ""


class UserReq(BaseModel):
    username: str = ""
    password: str = ""
    role: str = "user"


class UserPatch(BaseModel):
    password: str | None = None
    role: str | None = None
    disabled: bool | None = None


def bootstrap_admin(users: UserStore, data_dir: Path):
    """第一次启动没有任何用户时创建管理员 admin。
    口令来自 FREIGHT_HUB_ADMIN_PASSWORD；没设置就随机生成一个，写到 data/INITIAL_ADMIN_PASSWORD.txt（0600），日志里只提示文件位置，不打印口令。"""
    if users.all():
        return None
    pw = os.environ.get("FREIGHT_HUB_ADMIN_PASSWORD")
    where = None
    if not pw or check_new_password(pw):
        pw = secrets.token_urlsafe(12)
        where = data_dir / "INITIAL_ADMIN_PASSWORD.txt"
        _write_private(where, ("用户名：admin\n初始密码：%s\n登录后请尽快在「工具 → 修改我的密码」里改掉，并删除本文件。\n" % pw).encode("utf-8"))
    users.create("admin", pw, "admin")
    return where


def install(app, store):
    users = UserStore(store.dir / "users.json")
    sessions = Sessions(idle=float(os.environ.get("FREIGHT_HUB_SESSION_HOURS", "8")) * 3600)
    throttle = Throttle()
    trust_proxy = env_bool("FREIGHT_HUB_TRUST_PROXY")
    allowed_origins = {x.strip().lower() for x in os.environ.get("FREIGHT_HUB_ALLOWED_ORIGINS", "").split(",") if x.strip()}
    https = env_bool("FREIGHT_HUB_HTTPS") or bool(os.environ.get("FREIGHT_HUB_CERT"))
    where = bootstrap_admin(users, store.dir)
    if where:
        print(f"[FreightHub] 已创建管理员 admin，初始密码保存在：{where}", flush=True)
    app.state.users, app.state.sessions = users, sessions

    def secure_cookie(request):
        return https or (trust_proxy and request.headers.get("x-forwarded-proto", "").lower() == "https")

    def deny(status, msg, request):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": msg}, status_code=status)
        return RedirectResponse("/login", status_code=302) if status == 401 else JSONResponse({"detail": msg}, status_code=status)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path, method = request.url.path, request.method.upper()
        need = required_role(method, path)
        sess = sessions.get(request.cookies.get(COOKIE))
        request.state.user = None
        if sess:
            u = users.get(sess["name"])
            if not u or u.get("disabled"):
                sessions.drop(request.cookies.get(COOKIE))
                sess = None
            else:
                sess["role"] = u["role"]       # 角色变更立即生效
                request.state.user = sess
        if method not in SAFE_METHODS and not origin_ok(request, allowed_origins, trust_proxy):
            resp = JSONResponse({"detail": "来源校验失败（Origin）"}, status_code=403)
        elif need is not None and not sess:
            resp = deny(401, "未登录或登录已过期", request)
        elif need == "admin" and sess["role"] != "admin":
            resp = JSONResponse({"detail": "需要管理员权限"}, status_code=403)
        elif (need is not None and method not in SAFE_METHODS
              and not hmac.compare_digest(request.headers.get("x-csrf-token", ""), sess["csrf"])):
            resp = JSONResponse({"detail": "CSRF 校验失败，请刷新页面重试"}, status_code=403)
        else:
            resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        if path.startswith("/api/") or path in ("/", "/login"):
            resp.headers["Cache-Control"] = "no-store"
        if secure_cookie(request):
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return resp

    @app.get("/login")
    def login_page(request: Request):
        if request.state.user:
            return RedirectResponse("/", status_code=302)
        return FileResponse(STATIC / "login.html", headers={"Cache-Control": "no-store"})

    @app.post("/api/auth/login")
    def login(req: LoginReq, request: Request):
        key = (client_ip(request, trust_proxy), req.username.lower())
        if throttle.blocked(key):
            return JSONResponse({"detail": "失败次数过多，请 5 分钟后再试"}, status_code=429)
        u = users.authenticate(req.username, req.password)
        if not u:
            throttle.fail(key)
            return JSONResponse({"detail": "用户名或密码错误"}, status_code=401)
        throttle.ok(key)
        old = request.cookies.get(COOKIE)
        if old:
            sessions.drop(old)
        tok = sessions.create(u["username"], u["role"])
        users.touch_login(u["username"])
        s = sessions.get(tok)
        resp = JSONResponse({"user": u["username"], "role": u["role"], "csrf": s["csrf"]})
        resp.set_cookie(COOKIE, tok, httponly=True, samesite="lax", secure=secure_cookie(request), path="/", max_age=int(sessions.absolute))
        return resp

    @app.post("/api/auth/logout")
    def logout(request: Request):
        sessions.drop(request.cookies.get(COOKIE))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @app.get("/api/auth/me")
    def me(request: Request):
        u = request.state.user
        return {"user": u["name"], "role": u["role"], "csrf": u["csrf"]}

    @app.post("/api/auth/password")
    def change_password(req: PwReq, request: Request):
        u = request.state.user
        if not users.authenticate(u["name"], req.old):
            return JSONResponse({"detail": "原密码不正确"}, status_code=400)
        try:
            users.update(u["name"], password=req.new)
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=400)
        keep = request.cookies.get(COOKIE)
        for t in [t for t, s in list(sessions.d.items()) if s["name"].lower() == u["name"].lower() and t != keep]:
            sessions.drop(t)      # 改密码后其他设备的登录失效
        return {"ok": True}

    # ---- 用户管理（仅管理员；required_role 默认就是 admin） ----
    @app.get("/api/users")
    def list_users():
        return users.public_list()

    @app.post("/api/users")
    def create_user(req: UserReq):
        try:
            users.create(req.username.strip(), req.password, req.role)
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=400)
        return users.public_list()

    @app.put("/api/users/{name}")
    def update_user(name: str, req: UserPatch, request: Request):
        me_ = request.state.user
        try:
            if name.lower() == me_["name"].lower() and (req.role not in (None, "admin") or req.disabled):
                return JSONResponse({"detail": "不能降级或停用自己"}, status_code=400)
            users.update(name, password=req.password, role=req.role, disabled=req.disabled)
        except KeyError:
            return JSONResponse({"detail": "用户不存在"}, status_code=404)
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=400)
        if req.password is not None or req.disabled:
            sessions.drop_user(name)
        return users.public_list()

    @app.delete("/api/users/{name}")
    def delete_user(name: str, request: Request):
        if name.lower() == request.state.user["name"].lower():
            return JSONResponse({"detail": "不能删除自己"}, status_code=400)
        try:
            users.delete(name)
        except KeyError:
            return JSONResponse({"detail": "用户不存在"}, status_code=404)
        except ValueError as e:
            return JSONResponse({"detail": str(e)}, status_code=400)
        sessions.drop_user(name)
        return users.public_list()
