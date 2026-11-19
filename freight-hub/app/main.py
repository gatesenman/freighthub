import asyncio
import os
import uuid
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

from . import __version__, importer
from .edition import is_trial
from .adapters import build_registry
from .adapters.base import AdapterError, Query
from .countries import COUNTRIES
from .store import Store

STATIC = Path(__file__).resolve().parent / "static"
store = Store()
registry = build_registry(store)
pool = ThreadPoolExecutor(max_workers=16)
PLATFORM_TIMEOUT = 240  # 云途首次登录可能需要人工验证码，给足时间

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
SERVER = os.environ.get("FREIGHT_HUB_MODE", "").lower() == "server"   # 1.4.0 网页/服务器模式：启用登录、角色、CSRF


def _user(request):
    return getattr(request.state, "user", None)


def _owner(request):
    """历史记录的可见范围：桌面版/管理员看全部（None），普通用户只看自己的。"""
    u = _user(request)
    return u["name"] if u and u["role"] != "admin" else None


@app.get("/api/ping")
def ping():
    ping_state["last"] = time.time()
    ping_state["count"] += 1
    return {"ok": True}


@app.get("/api/meta")
def meta(request: Request):
    d = {"version": __version__, "trial": is_trial(), "mode": "server" if SERVER else "desktop", "countries": [{"code": c, "name": n} for c, n in COUNTRIES]}
    u = _user(request)
    if SERVER:
        d.update(user=u["name"], role=u["role"], csrf=u["csrf"])
        if u["role"] == "admin":
            d["data_dir"] = str(store.dir)
    else:
        d["data_dir"] = str(store.dir)
    return d


@app.get("/api/platforms")
def platforms():
    return [a.status() for a in registry.values()]


@app.put("/api/platforms/{pid}/account")
def save_account(pid: str, req: AccountReq):
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
def accounts_export(fmt: str = "xlsx", secrets: str = "none"):
    if secrets not in ("none", "mask"):
        raise HTTPException(400, "密钥只能选择「不导出」或「打码」")
    return _download(*importer.build_export(registry, store, "csv" if fmt == "csv" else "xlsx", secrets))


@app.post("/api/accounts/import/preview")
def accounts_import_preview(req: ImportPreviewReq):
    try:
        return importer.preview(req.filename, req.data, registry, store)
    except Exception as e:  # noqa
        raise HTTPException(400, f"读取文件失败：{e}")


@app.post("/api/accounts/import/apply")
def accounts_import_apply(req: ImportApplyReq):
    try:
        res = importer.apply(req.batch, req.rows, registry, store)
    except KeyError as e:
        raise HTTPException(400, str(e.args[0]))
    return {"results": res, "platforms": [a.status() for a in registry.values()]}


@app.post("/api/platforms/{pid}/test")
async def test_platform(pid: str):
    a = registry.get(pid)
    if not a:
        raise HTTPException(404, "未知平台")
    loop = asyncio.get_running_loop()
    t0 = time.time()
    try:
        msg = await asyncio.wait_for(loop.run_in_executor(pool, a.test_login), PLATFORM_TIMEOUT)
        store.mark(pid, True)
        return {"ok": True, "message": msg, "elapsed_ms": int((time.time() - t0) * 1000), "status": a.status()}
    except AdapterError as e:
        if e.kind not in ("pending", "manual"):
            store.mark(pid, False, _clean(e))
        return {"ok": False, "kind": e.kind, "message": _clean(e), "elapsed_ms": int((time.time() - t0) * 1000), "status": a.status()}
    except Exception as e:  # noqa
        store.mark(pid, False, _clean(f"{type(e).__name__}: {e}"))
        return {"ok": False, "kind": "error", "message": _clean(f"{type(e).__name__}: {e}"), "status": a.status()}


def _mark_test_rows(rows):
    """测试环境返回的是模拟数据：打 excluded 标记，不参与全场最低 / 平台最低 / 历史最低价。"""
    for x in rows:
        if x.get("env") == "sandbox":
            x["excluded"] = "测试环境数据，非真实报价"


async def run_platform(a, q):
    t0 = time.time()
    info = {"id": a.id, "name": a.name, "state": "ok", "count": 0, "message": "", "elapsed_ms": 0,
            "env": a.env() if a.auth_mode() == "api" else "prod", "env_tag": "【测试】" if (a.auth_mode() == "api" and a.is_test_env) else ""}
    rows = []
    st = a.status()
    if not a.implemented:
        info.update(state="pending", message=a.note if a.auth_mode() == "login" else (
            "该平台开放接口无运价试算（仅连接测试）：" + (a.api_doc_note or "") if a.api_impl else "需向服务商申请 API 文档"))
    elif a.needs_account and not a.has_account():
        info.update(state="no_account", message="尚未配置账号，请到“平台账号”页填写")
    elif not st["enabled"]:
        info.update(state="skipped", message="已停用")
    else:
        loop = asyncio.get_running_loop()
        try:
            rows = await asyncio.wait_for(loop.run_in_executor(pool, a.quote, q), PLATFORM_TIMEOUT)
            info["count"] = len(rows)
            store.mark(a.id, True)
            if not rows:
                info["message"] = "该平台没有返回可用渠道"
        except AdapterError as e:
            info.update(state=e.kind if e.kind in ("no_account", "pending", "manual", "login_failed") else "error", message=_clean(e))
            if e.kind not in ("pending", "no_account"):
                store.mark(a.id, False, info["message"])
        except asyncio.TimeoutError:
            info.update(state="error", message="查询超时")
            store.mark(a.id, False, "查询超时")
        except Exception as e:  # noqa  单平台异常不影响其他平台
            info.update(state="error", message=_clean(f"{type(e).__name__}: {e}"))
            store.mark(a.id, False, info["message"])
    info["elapsed_ms"] = int((time.time() - t0) * 1000)
    return info, rows


@app.post("/api/quote")
async def quote(req: QuoteReq, request: Request):
    code = req.country.strip().upper()
    if not code or req.weight <= 0:
        raise HTTPException(400, "请填写目的国和大于 0 的重量")
    name = next((n for c, n in COUNTRIES if c == code), code)
    q = Query(country=code, country_name=name, weight=req.weight, postcode=req.postcode.strip(),
              length=req.length, width=req.width, height=req.height)
    ids = req.platforms or list(registry.keys())
    targets = [registry[i] for i in ids if i in registry]
    results = await asyncio.gather(*[run_platform(a, q) for a in targets])
    plats = [r[0] for r in results]
    rows = [x for r in results for x in r[1]]
    for x in rows:   # 非人民币报价不能悄悄和人民币混排：加显式提示
        cur = (x.get("currency") or "CNY").upper()
        if cur != "CNY" and "未折算" not in (x.get("remark") or ""):
            x["remark"] = f"【币种 {cur}，未折算为人民币】" + (x.get("remark") or "")
            x.setdefault("fees", []).append({"label": "币种", "value": f"{cur}（未折算）"})
    _mark_test_rows(rows)
    rows.sort(key=lambda x: (bool(x.get("excluded")), (x.get("currency") or "CNY").upper() != "CNY", x["total"]))   # 可比的人民币行在前
    query = {"country": code, "country_name": name, "weight": req.weight, "postcode": q.postcode,
             "length": req.length, "width": req.width, "height": req.height}
    hid = store.add_history(query, plats, rows, user=(_user(request) or {}).get("name", ""))
    return {"id": hid, "ts": time.time(), "query": query, "platforms": plats, "rows": rows}


class QuickReq(BaseModel):
    """快速询价：要么给 text（「美国 2kg 30x20x10」），要么给结构化字段；platforms 可只查一个（浮窗逐平台出结果用）。"""
    text: str = Field("", max_length=120)
    country: str = Field("", max_length=16)
    weight: float = Field(0, allow_inf_nan=False, le=100000)
    length: float = Field(0, ge=0, le=10000, allow_inf_nan=False)
    width: float = Field(0, ge=0, le=10000, allow_inf_nan=False)
    height: float = Field(0, ge=0, le=10000, allow_inf_nan=False)
    postcode: str = Field("", max_length=24)
    platforms: list[str] | None = None
    save: bool = False


_quick_cache = {}      # token -> {query, info, rows, user, ts}：浮窗查到的完整结果，点击行时「合并存历史」用（不接受客户端回传的行，防伪造）
shell_hooks = {"status": None, "settings": None, "open_popup": None}     # 桌面外壳（run.py）挂接


def _quick_put(entry):
    now = time.time()
    for k in [k for k, v in _quick_cache.items() if now - v["ts"] > 900]:
        _quick_cache.pop(k, None)
    while len(_quick_cache) >= 60:
        _quick_cache.pop(next(iter(_quick_cache)), None)
    tok = uuid.uuid4().hex[:16]
    _quick_cache[tok] = dict(entry, ts=now)
    return tok


class QuickCommitReq(BaseModel):
    tokens: list[str] = Field(max_length=32)


@app.post("/api/quote/quick/commit")
def quote_quick_commit(req: QuickCommitReq, request: Request):
    me = (_user(request) or {}).get("name", "")
    ents = [_quick_cache.get(t) for t in req.tokens]
    ents = [e for e in ents if e and e["user"] == me]
    if not ents:
        raise HTTPException(404, "浮窗结果已过期，请重新查询")
    query = ents[0]["query"]
    infos = [e["info"] for e in ents]
    rows = [x for e in ents for x in e["rows"]]
    for x in rows:
        cur = (x.get("currency") or "CNY").upper()
        if cur != "CNY" and "未折算" not in (x.get("remark") or ""):
            x["remark"] = f"【币种 {cur}，未折算为人民币】" + (x.get("remark") or "")
    rows.sort(key=lambda x: ((x.get("currency") or "CNY").upper() != "CNY", x["total"]))
    return {"history_id": store.add_history(query, infos, rows, user=me)}


@app.post("/api/quote/quick")
async def quote_quick(req: QuickReq, request: Request):
    from . import quick
    try:
        if req.text.strip():
            p = quick.parse_quick_text(req.text)
        else:
            hit = quick.resolve_country(req.country)
            if not hit:
                c = req.country.strip()
                raise ValueError(f"没认出目的国「{c}」：请写国家中文名或两位代码（如 美国 / US）" if c else "请填写目的国（中文名或两位代码）")
            if req.weight <= 0:
                raise ValueError("请填写大于 0 的重量")
            p = {"country": hit[0], "country_name": hit[1], "weight": req.weight, "length": req.length, "width": req.width, "height": req.height}
    except ValueError as e:
        raise HTTPException(400, str(e))
    q = Query(country=p["country"], country_name=p["country_name"], weight=p["weight"], postcode=req.postcode.strip(),
              length=p["length"], width=p["width"], height=p["height"])
    ids = req.platforms or list(registry.keys())
    targets = [registry[i] for i in ids if i in registry]
    if not targets:
        raise HTTPException(400, "没有可查询的平台")
    results = await asyncio.gather(*[run_platform(a, q) for a in targets])
    infos = [r[0] for r in results]
    rows = [x for r in results for x in r[1]]
    _mark_test_rows(rows)
    plats, overall = quick.summarize(infos, rows)
    tokens = [_quick_put({"query": None, "info": i, "rows": [r for r in rows if r.get("platform") == i["id"]], "user": (_user(request) or {}).get("name", "")}) for i in infos]
    query = {"country": p["country"], "country_name": p["country_name"], "weight": p["weight"], "postcode": q.postcode,
             "length": p["length"], "width": p["width"], "height": p["height"]}
    for t in tokens:
        _quick_cache[t]["query"] = query
    hid = None
    if req.save:
        rows.sort(key=lambda x: (bool(x.get("excluded")), (x.get("currency") or "CNY").upper() != "CNY", x["total"]))
        hid = store.add_history(query, infos, rows, user=(_user(request) or {}).get("name", ""))
    return {"query": query, "platforms": plats, "overall": overall, "history_id": hid, "tokens": tokens, "ts": time.time()}


class SettingsReq(BaseModel):
    hotkey: str | None = Field(None, max_length=40)
    popup_autoclose: bool | None = None
    close_to_tray: bool | None = None
    quick_enabled: bool | None = None
    quick_last: dict | None = None


@app.get("/api/shell/settings")
def shell_settings_get():
    from . import settings
    return settings.load()


@app.put("/api/shell/settings")
def shell_settings_put(req: SettingsReq):
    from . import settings
    patch = {k: v for k, v in req.model_dump().items() if v is not None}
    try:
        cur = settings.update(patch)
    except ValueError as e:
        raise HTTPException(400, str(e))
    st = None
    if shell_hooks["settings"] and any(k in patch for k in ("hotkey", "quick_enabled", "close_to_tray")):
        st = shell_hooks["settings"](cur)         # 重新注册快捷键；返回状态（可能含「被占用」提示）
    return {"settings": cur, "shell": st or shell_status()}


def shell_status():
    if shell_hooks["status"]:
        return shell_hooks["status"]()
    return {"active": False, "hotkey_ok": False, "tray_ok": False, "message": "桌面外壳未启动（浏览器 / 网页模式下没有全局快捷键与托盘）"}


@app.get("/api/shell/status")
def shell_status_get():
    return shell_status()


@app.post("/api/shell/popup")
def shell_popup(body: dict | None = None):
    """工具栏输入框 / 测试用：在鼠标处唤出浮窗，可带 text 预填并自动查询。"""
    if not shell_hooks["open_popup"]:
        raise HTTPException(409, "桌面外壳未启动，无法唤出浮窗")
    shell_hooks["open_popup"](str((body or {}).get("text", ""))[:120])
    return {"ok": True}


@app.get("/quick")
def quick_page():
    return FileResponse(STATIC / "quick.html", headers={"Cache-Control": "no-store"})


@app.get("/api/history")
def history(request: Request):
    return store.list_history(owner=_owner(request))


@app.get("/api/history/{hid}")
def history_item(hid: int, request: Request):
    h = store.get_history(hid, owner=_owner(request))
    if not h:
        raise HTTPException(404, "记录不存在")
    return h


@app.delete("/api/history/{hid}")
def history_delete(hid: int, request: Request):
    store.delete_history(hid, owner=_owner(request))
    return {"ok": True}


@app.delete("/api/history")
def history_clear(request: Request):
    store.delete_history(None, owner=_owner(request))
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
    fn = f"quote_{q['country']}_{q['weight']:g}kg_{h['id']}.csv"
    return Response(data.encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename={fn}"})


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


if SERVER:
    from . import server as _server
    _server.install(app, store)

app.mount("/static", StaticFiles(directory=STATIC), name="static")
