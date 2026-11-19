import asyncio
import os
import csv
import io
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from urllib.parse import quote as urlquote

from . import __version__, importer, rowcheck
from .adapters import build_registry
from .adapters.base import AdapterError, Query
from .countries import COUNTRIES
from .store import Store

if os.environ.get("VERCEL") and not os.environ.get("FREIGHT_HUB_MODE", "").strip():
    os.environ["FREIGHT_HUB_MODE"] = "cloud"      # 在 Vercel 上运行 = 网页云端版（入口可能是 app/main.py，也可能是 api/index.py）
STATIC = Path(__file__).resolve().parent / "static"
CLOUD = os.environ.get("FREIGHT_HUB_MODE", "").strip().lower() == "cloud"   # Vercel Serverless 网页版：Postgres + RBAC + 审计


def _make_store():
    if not CLOUD:
        return Store()
    from .db import Db, database_url
    from .pgstore import DbStore
    from . import cloud as _cloud
    url = database_url()
    if not url:
        raise RuntimeError("云端模式需要数据库连接串（DATABASE_URL，由 Vercel 的 Neon 集成注入）")
    db = Db(url)
    db.ensure_schema(seed=_cloud.seed_roles)
    return DbStore(db)


store = _make_store()
registry = build_registry(store)
pool = ThreadPoolExecutor(max_workers=16)
# 桌面版：云途首次登录可能需要人工验证码，给足时间；网页版跑在 Vercel 函数里（maxDuration=60s），单平台必须在 50 秒内给出结果，
# 否则整个函数被平台强杀、所有平台一起丢结果
PLATFORM_TIMEOUT = 50 if os.environ.get("FREIGHT_HUB_MODE", "").strip().lower() == "cloud" else 240

app = FastAPI(title="货代询价助手", version=__version__)


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    """422 里不回显原始输入：NaN/Infinity 之类的输入无法再序列化，会把 422 变成 500；同时避免把密钥字段原样回显。"""
    errs = [{"loc": list(e.get("loc", [])), "msg": str(e.get("msg", "")), "type": e.get("type", "")} for e in exc.errors()]
    return JSONResponse({"detail": errs}, status_code=422)


class QuoteReq(BaseModel):
    country: str = Field(max_length=16)
    weight: float = Field(allow_inf_nan=False, le=100000)
    postcode: str = Field("", max_length=24)
    length: float = Field(0, ge=0, le=10000, allow_inf_nan=False)
    width: float = Field(0, ge=0, le=10000, allow_inf_nan=False)
    height: float = Field(0, ge=0, le=10000, allow_inf_nan=False)
    platforms: list[str] | None = None


class AccountReq(BaseModel):
    values: dict[str, str] = {}
    enabled: bool | None = None
    auth_mode: str | None = None  # login = 登录账号授权；api = 开放平台API授权
    env: str | None = None        # prod = 正式环境；sandbox = 测试环境（凭据按环境分开存，切换不丢）


MAX_IMPORT_B64 = 14 * 1024 * 1024   # 导入文件上限约 10MB（base64 后）


class ImportPreviewReq(BaseModel):
    filename: str = Field("", max_length=260)
    data: str = Field(max_length=MAX_IMPORT_B64)  # base64


class ImportApplyReq(BaseModel):
    batch: str
    rows: list[int] | None = None


ping_state = {"last": 0.0, "count": 0}
SERVER = os.environ.get("FREIGHT_HUB_MODE", "").strip().lower() in ("server", "cloud")   # 1.4.0 网页/服务器模式：启用登录、角色、CSRF


def _audit(request, action, target="", status="ok", detail=None):
    fn = getattr(app.state, "audit", None)
    if fn:
        fn(request, action, target, status, detail)


def _perm(request, p):
    u = _user(request)
    return bool(u and p in u.get("perms", ()))


def _user(request):
    return getattr(request.state, "user", None)


def _owner(request):
    """历史记录的可见范围：桌面版/管理员看全部（None），普通用户只看自己的。"""
    u = _user(request)
    if u and "perms" in u:
        return None if "history.view_all" in u["perms"] else u["name"]
    return u["name"] if u and u["role"] != "admin" else None


def _owner_del(request):
    u = _user(request)
    if u and "perms" in u:
        return None if "history.delete_all" in u["perms"] else u["name"]
    return _owner(request)


_BOOT = time.time()
_INSTANCE = os.urandom(3).hex()   # 函数实例标识：用来区分"同一个实例连续失败"还是"换了实例"


def _tcp_probe(host, port, t=5.0):
    import socket
    t0 = time.time()
    try:
        socket.create_connection((host, port), timeout=t).close()
        return {"ok": True, "ms": int((time.time() - t0) * 1000)}
    except OSError as e:
        return {"ok": False, "ms": int((time.time() - t0) * 1000), "err": type(e).__name__ + ": " + str(e)[:80]}


@app.post("/api/diag/recycle")
async def diag_recycle(request: Request):
    """管理员：让当前函数实例退出，下一个请求会落在全新的实例上（验证自愈用）。"""
    _audit(request, "diag.recycle", _INSTANCE)
    _recycle_instance("admin request")
    return {"ok": True, "old_instance": _INSTANCE}


@app.get("/api/diag/net")
async def diag_net():
    """管理员自检：本函数实例到各平台的 TCP 连通性（只做握手，不发任何业务请求）。"""
    from urllib.parse import urlparse
    targets = {"zhonghang": ("39.108.221.228", 21000), "xinhang": ("xinh.kingtrans.net", 443), "huanyang": ("ekttech.net", 443),
               "yutong": ("openapi.intelink.net.cn", 443), "yanwen": ("open-fat.yw56.com.cn", 443), "neon/外网对照": ("api.ipify.org", 443)}
    loop = asyncio.get_running_loop()
    res = await asyncio.gather(*[loop.run_in_executor(pool, _tcp_probe, h, p) for h, p in targets.values()])
    ip = ""
    try:
        import requests as _rq
        ip = _rq.get("https://api.ipify.org", timeout=5).text.strip()
    except Exception:  # noqa
        pass
    return {"instance": _INSTANCE, "age_s": int(time.time() - _BOOT), "egress_ip": ip, "probes": dict(zip(targets, res))}


@app.get("/api/ping")
def ping():
    ping_state["last"] = time.time()
    ping_state["count"] += 1
    return {"ok": True}


@app.get("/api/meta")
def meta(request: Request):
    d = {"version": __version__, "mode": "server" if SERVER else "desktop", "countries": [{"code": c, "name": n} for c, n in COUNTRIES]}
    u = _user(request)
    if SERVER:
        d.update(user=u["name"], role=u["role"], csrf=u["csrf"])
        if CLOUD:
            d.update(cloud=True, role_label=u["role_label"], perms=sorted(u["perms"]))
            _cl = app.state.cloud
            _pol = _cl.policy(u["name"])
            if _pol.get("qph"):
                _lim, _used = _cl.quota(u["name"])
                d["quota"] = {"limit": _lim, "used": _used, "left": max(0, _lim - _used)}
            if _pol.get("no_pw"):
                d["no_pw"] = True
            from . import cloud as _c2
            d["trial"] = bool(_pol.get("trial")) or _c2.trial_watermark_on()
        elif u["role"] == "admin":
            d["data_dir"] = str(store.dir)
    else:
        d["data_dir"] = str(store.dir)
    return d


@app.get("/api/platforms")
def platforms(request: Request):
    out = [a.status() for a in registry.values()]
    if CLOUD and not _perm(request, "accounts.manage"):    # 没有账号管理权限的角色（如试用客户）看不到任何账号字段 / 用户名 / 对接地址
        for s in out:
            s["fields"] = []
            s["last_error"] = None
            for e in s.get("envs") or []:
                e["fields"] = []
                e["url"] = ""
            for m in (s.get("modes") or {}).values():
                m["fields"] = []
    return out


@app.put("/api/platforms/{pid}/account")
def save_account(pid: str, req: AccountReq, request: Request):
    a = registry.get(pid)
    if not a:
        raise HTTPException(404, "未知平台")
    patch = {}
    allowed = a.all_field_keys()
    secrets = a.secret_keys()
    for k, v in req.values.items():
        if k in allowed and v is not None and (v != "" or k not in secrets):
            patch[k] = v if k in secrets else v.strip()
    if req.auth_mode is not None and not a.fixed_mode:
        if req.auth_mode not in ("login", "api"):
            raise HTTPException(400, "授权模式只能是 login 或 api")
        if req.auth_mode != a.auth_mode():
            patch["auth_mode"] = req.auth_mode
    if req.enabled is not None:
        patch["enabled"] = req.enabled
    env_changed = False
    if req.env is not None:
        if req.env not in a.env_choices():
            raise HTTPException(400, f"{a.name}没有「{'测试' if req.env == 'sandbox' else req.env}环境」，只有正式环境")
        env_changed = req.env != a.env()
    # 按环境落盘：凭据写入所选环境的那一套（正式=原键，测试=键@sandbox），另一套原样保留；旧版单套凭据首次保存时自动迁移
    raw = store.get_account(pid)
    flat = {k: v for k, v in patch.items() if k in allowed}
    rest = {k: v for k, v in patch.items() if k not in allowed}
    patch = {**rest, **a.env_patch(raw, flat, env=req.env, activate=req.env is not None)}
    # 凭据、环境或授权模式变更后清掉旧会话 / token，下次查询自动重新授权
    if any(k in patch for k in allowed) or "auth_mode" in patch or env_changed:
        store.clear_session(pid)
        store.clear_session(pid + "_api")
        store.runtime.pop(pid, None)
    store.set_account(pid, patch)
    _audit(request, "account.update", pid, detail={"fields": sorted(k.split("@")[0] + ("(测试)" if "@" in k else "") for k in patch if k not in ("enabled", "auth_mode", "env")),
                                                   **({"env": req.env} if req.env else {}),
                                                   **({"enabled": patch["enabled"]} if "enabled" in patch else {}),
                                                   **({"auth_mode": patch["auth_mode"]} if "auth_mode" in patch else {})})
    return a.status()


def _clean(msg):
    """对外展示的错误信息再脱敏一次：把已保存的密钥原文也替换掉。"""
    from .redact import redact
    secs = []
    try:
        cfg = store._read().get("accounts", {})
        for pid, a in registry.items():
            for k in a.secret_keys():
                for kk in (k, k + "@sandbox", "sb_" + k):
                    v = cfg.get(pid, {}).get(kk)
                    if v:
                        secs.append(v)
    except Exception:
        pass
    return redact(msg, secs)


def _download(data: bytes, mime: str, name: str):
    return Response(data, media_type=mime, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{urlquote(name)}",
                                                    "Cache-Control": "no-store", "X-Filename": urlquote(name)})


@app.get("/api/accounts/template")
def accounts_template(fmt: str = "xlsx"):
    return _download(*importer.build_template(registry, "csv" if fmt == "csv" else "xlsx"))


@app.get("/api/accounts/export")
def accounts_export(request: Request, fmt: str = "xlsx", secrets: str = "none"):
    if secrets not in ("none", "mask"):
        raise HTTPException(400, "密钥只能选择「不导出」或「打码」")
    _audit(request, "account.export", fmt, detail={"secrets": secrets})
    return _download(*importer.build_export(registry, store, "csv" if fmt == "csv" else "xlsx", secrets))


@app.post("/api/accounts/import/preview")
def accounts_import_preview(req: ImportPreviewReq):
    try:
        return importer.preview(req.filename, req.data, registry, store)
    except Exception as e:  # noqa
        raise HTTPException(400, f"读取文件失败：{e}")


@app.post("/api/accounts/import/apply")
def accounts_import_apply(req: ImportApplyReq, request: Request):
    try:
        res = importer.apply(req.batch, req.rows, registry, store)
    except KeyError as e:
        raise HTTPException(400, str(e.args[0]))
    _audit(request, "account.import", f"{len(res)} 项", detail={"results": [{k: r.get(k) for k in ("pid", "name", "action", "ok") if isinstance(r, dict) and k in r} for r in res][:30]})
    return {"results": res, "platforms": [a.status() for a in registry.values()]}


@app.post("/api/platforms/{pid}/test")
async def test_platform(pid: str, request: Request):
    a = registry.get(pid)
    if not a:
        raise HTTPException(404, "未知平台")
    loop = asyncio.get_running_loop()
    t0 = time.time()
    try:
        msg = await asyncio.wait_for(loop.run_in_executor(pool, a.test_login), PLATFORM_TIMEOUT)
        store.mark(pid, True)
        _audit(request, "account.test", pid)
        return {"ok": True, "message": msg, "elapsed_ms": int((time.time() - t0) * 1000), "status": a.status()}
    except AdapterError as e:
        if e.kind not in ("pending", "manual"):
            store.mark(pid, False, _clean(e))
        _audit(request, "account.test", pid, "fail", {"kind": e.kind})
        return {"ok": False, "kind": e.kind, "message": _clean(e), "elapsed_ms": int((time.time() - t0) * 1000), "status": a.status()}
    except Exception as e:  # noqa
        store.mark(pid, False, _clean(f"{type(e).__name__}: {e}"))
        return {"ok": False, "kind": "error", "message": _clean(f"{type(e).__name__}: {e}"), "status": a.status()}



# ---- 实例自愈：整个函数实例的出站网络"黑洞"时，主动退出让平台换新实例 --------------------------
# 现象（2026-11-02 实测）：某个函数实例在冷启动之后，到所有国内平台的 TCP 握手全部超时，而且这个实例一直保持这个状态，
# 后面所有请求都落在它身上，所以用户感觉"放一会儿授权就失效了"。重试同一个实例没用，必须换实例。
_inflight = {"n": 0}


def _recycle_instance(reason):
    import threading
    print(f"[fh] recycle instance {_INSTANCE}: {reason}", flush=True)

    def _go():
        for _ in range(20):                     # 最多等 20 秒，等其他进行中的查询结束再退出
            if _inflight["n"] <= 0:
                break
            time.sleep(1)
        os._exit(0)
    if CLOUD:
        threading.Timer(1.5, _go).start()


# ---- 错误分类 + 单平台熔断（互不影响）----
_BREAKER = {}                  # 平台 id → {"fails": 连续网络失败次数, "until": 暂停到何时}（每个函数实例各自一份）
BREAK_AFTER, BREAK_SECS = 3, 15


def _category(kind, msg):
    """把失败归成 5 类中文原因：网络 / 授权 / 配置缺失 / 平台错误 / 不支持。"""
    m = str(msg)
    if kind == "no_account":
        return "配置缺失"
    if kind == "login_failed":
        return "授权"
    if kind in ("pending", "manual"):
        return "不支持"
    if "网络请求失败" in m or "查询超时" in m or "ConnectTimeout" in m or "ConnectionError" in m or "Timeout" in m:
        return "网络"
    return "平台错误"


def _breaker_open(pid):
    b = _BREAKER.get(pid)
    return bool(b and b["until"] > time.time())


def _breaker_note(pid, cat):
    b = _BREAKER.setdefault(pid, {"fails": 0, "until": 0.0})
    if cat == "网络":
        b["fails"] += 1
        if b["fails"] >= BREAK_AFTER:
            b["until"] = time.time() + BREAK_SECS
            b["fails"] = 0
    else:
        b["fails"] = 0


async def run_platform(a, q):
    t0 = time.time()
    info = {"id": a.id, "name": a.name, "state": "ok", "count": 0, "message": "", "elapsed_ms": 0,
            "env": a.env() if a.auth_mode() == "api" else "prod", "env_tag": "【测试】" if (a.auth_mode() == "api" and a.is_test_env) else ""}
    rows = []
    st = a.status()
    if not a.implemented:
        info.update(state="pending", category="不支持", message=("【不支持】仅桌面版：该平台的「登录账号」模式需要本机浏览器（含验证码），网页版不能用；请用桌面版，或改用开放平台 API 授权" if a.desktop_blocked else a.note) if a.auth_mode() == "login" else (
            "【不支持】该平台开放接口无运价试算（仅连接测试）：" + (a.api_doc_note or "") if a.api_impl else "【不支持】需向服务商申请 API 文档"))
    elif a.needs_account and not a.has_account():
        info.update(state="no_account", category="配置缺失", message="【配置缺失】尚未配置账号，请到“平台账号”页填写（默认用开放平台 API 凭据）")
    elif not st["enabled"]:
        info.update(state="skipped", category="", message="已停用")
    else:
        loop = asyncio.get_running_loop()
        if _breaker_open(a.id):
            info.update(state="error", category="网络", message=f"【网络】刚连续 {BREAK_AFTER} 次连不上该平台，已暂停 {BREAK_SECS} 秒避免空等，稍后再点查询会自动重试")
            info["elapsed_ms"] = 0
            return info, rows
        try:
            rows = await asyncio.wait_for(loop.run_in_executor(pool, a.quote, q), PLATFORM_TIMEOUT)
            info["count"] = len(rows)
            store.mark(a.id, True)
            _breaker_note(a.id, "")
            if not rows:
                info["message"] = "该平台没有返回可用渠道"
        except AdapterError as e:
            cat = _category(e.kind, e)
            info.update(state=e.kind if e.kind in ("no_account", "pending", "manual", "login_failed") else "error", category=cat,
                        message=f"【{cat}】{_clean(e)}")
            _breaker_note(a.id, cat)
            if e.kind not in ("pending", "no_account"):
                store.mark(a.id, False, info["message"])
        except asyncio.TimeoutError:
            info.update(state="error", category="网络", message=f"【网络】查询超时（超过 {PLATFORM_TIMEOUT} 秒无响应）")
            _breaker_note(a.id, "网络")
            store.mark(a.id, False, info["message"])
        except Exception as e:  # noqa  单平台异常不影响其他平台
            raw = _clean(f"{type(e).__name__}: {e}")
            cat = _category("error", raw)
            info.update(state="error", category=cat, message=f"【{cat}】{raw}")
            _breaker_note(a.id, cat)
            store.mark(a.id, False, info["message"])
    info["elapsed_ms"] = int((time.time() - t0) * 1000)
    return info, rows


@app.post("/api/quote")
async def quote(req: QuoteReq, request: Request):
    if CLOUD:     # 按用户限额（试用账号：每小时 N 次）
        _cl = app.state.cloud
        _u = _user(request) or {}
        _lim, _used = _cl.quota(_u.get("name", ""))
        if _lim and _used >= _lim:
            _audit(request, "quote.limited", "", status="fail", detail={"limit": _lim})
            raise HTTPException(429, f"试用账号每小时最多查询 {_lim} 次，已用完，请稍后再试")
    code = req.country.strip().upper()
    if not code or req.weight <= 0:
        raise HTTPException(400, "请填写目的国和大于 0 的重量")
    name = next((n for c, n in COUNTRIES if c == code), code)
    q = Query(country=code, country_name=name, weight=req.weight, postcode=req.postcode.strip(),
              length=req.length, width=req.width, height=req.height)
    ids = req.platforms or list(registry.keys())
    targets = [registry[i] for i in ids if i in registry]
    _inflight["n"] += 1
    try:
        results = await asyncio.gather(*[run_platform(a, q) for a in targets])
    finally:
        _inflight["n"] -= 1
    plats = [r[0] for r in results]
    _net_bad = [p for p in plats if p.get("category") == "网络" and "暂停" not in p.get("message", "")]
    _net_try = [p for p in plats if p["state"] in ("ok", "error") and p["state"] != "skipped"]
    net_failed = len(_net_bad) >= 3 and len(_net_bad) == len(_net_try)      # 所有真正发起请求的平台都连不上
    if net_failed:
        _recycle_instance(f"{len(_net_bad)} platforms all network-failed")
    rows = [x for r in results for x in r[1]]
    for x in rows:   # 非人民币报价不能悄悄和人民币混排：加显式提示
        cur = (x.get("currency") or "CNY").upper()
        if cur != "CNY" and "未折算" not in (x.get("remark") or ""):
            x["remark"] = f"【币种 {cur}，未折算为人民币】" + (x.get("remark") or "")
            x.setdefault("fees", []).append({"label": "币种", "value": f"{cur}（未折算）"})
    rowcheck.mark(rows, req.weight)    # 打 excluded 标记（测试数据 / 中港段 / 价格低于 ¥10 / 非人民币）：不参与全场最低
    rows.sort(key=lambda x: (bool(x.get("excluded")), (x.get("currency") or "CNY").upper() != "CNY", x["total"]))   # 可比的人民币行在前   # 人民币在前，其余币种在后
    query = {"country": code, "country_name": name, "weight": req.weight, "postcode": q.postcode,
             "length": req.length, "width": req.width, "height": req.height}
    hid = store.add_history(query, plats, rows, user=(_user(request) or {}).get("name", ""))
    _audit(request, "quote.run", f"{code} {req.weight:g}kg", detail={"history_id": hid, "rows": len(rows), "platforms": {p["id"]: p["state"] for p in plats}})
    return {"id": hid, "ts": time.time(), "query": query, "platforms": plats, "rows": rows, "inst": {"id": _INSTANCE, "age_s": int(time.time() - _BOOT)}, "net_failed": net_failed}


@app.get("/api/history")
def history(request: Request):
    return store.list_history(owner=_owner(request))


@app.get("/api/history/{hid}")
def history_item(hid: int, request: Request):
    h = store.get_history(hid, owner=_owner(request))
    if not h:
        raise HTTPException(404, "记录不存在")
    if SERVER and _user(request) and h.get("user") and h["user"] != _user(request)["name"]:
        _audit(request, "history.view", f"#{hid}", detail={"owner": h["user"]})
    return h


@app.delete("/api/history/{hid}")
def history_delete(hid: int, request: Request):
    store.delete_history(hid, owner=_owner_del(request))
    _audit(request, "history.delete", f"#{hid}")
    return {"ok": True}


@app.delete("/api/history")
def history_clear(request: Request):
    own = _owner_del(request)
    store.delete_history(None, owner=own)
    _audit(request, "history.clear", own or "全部用户")
    return {"ok": True}


def _csv_safe(v):
    """防 CSV/Excel 公式注入：文本单元格以 = + - @ 制表符 回车 开头时前面加单引号（数字不处理）。"""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


@app.get("/api/history/{hid}/csv")
def history_csv(hid: int, request: Request):
    h = store.get_history(hid, owner=_owner(request))
    if not h:
        raise HTTPException(404, "记录不存在")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["平台", "渠道", "分组", "总价", "币种", "时效(天)", "轨迹", "备注"])
    for r in h["rows"]:
        w.writerow([_csv_safe(c) for c in (r["platform_name"], r["channel"], r["group"], r["total"], r["currency"], r["days"],
                                           {True: "有", False: "无"}.get(r["track"], ""), r["remark"])])
    data = "\ufeff" + buf.getvalue()
    q = h["query"]
    _audit(request, "history.export", f"#{hid}")
    fn = f"quote_{q['country']}_{q['weight']:g}kg_{h['id']}.csv"
    return Response(data.encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename={fn}"})


@app.get("/")
def index():
    if CLOUD:
        from . import cloud as _c
        return _c.render_page("index.html")
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


if CLOUD:
    from . import cloud as _cloud
    _cloud.install(app, store)
elif SERVER:
    from . import server as _server
    _server.install(app, store)

app.mount("/static", StaticFiles(directory=STATIC), name="static")
