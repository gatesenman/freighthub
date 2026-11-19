import math
import re
import time
from dataclasses import dataclass, field, asdict


def is_cloud():
    """云端（Vercel Serverless）网页版：没有本机浏览器，不能走 CDP 登录。"""
    import os
    return os.environ.get("FREIGHT_HUB_MODE", "").strip().lower() == "cloud"


class AdapterError(Exception):
    """适配器可预期的失败（账号缺失、登录失败、需要人工验证等）。kind 用于前端显示状态。"""

    def __init__(self, message, kind="error"):
        super().__init__(message)
        self.kind = kind  # error | no_account | login_failed | manual | pending


@dataclass
class Query:
    country: str
    country_name: str = ""
    weight: float = 1.0
    postcode: str = ""
    length: float = 0
    width: float = 0
    height: float = 0

    def dims(self):
        return (self.length or 0) > 0 and (self.width or 0) > 0 and (self.height or 0) > 0


def to_float(v):
    if v is None or v == "":
        return None
    try:
        f = float(str(v).replace(",", "").strip())
    except Exception:
        return None
    return f if math.isfinite(f) else None   # 拒绝 NaN / Infinity


def parse_days(text):
    """'5-8' -> (5, 8)；'7' -> (7, 7)；解析不了 -> (None, None)"""
    if not text:
        return None, None
    nums = [int(math.ceil(float(x))) for x in re.findall(r"\d+(?:\.\d+)?", str(text))]
    if not nums:
        return None, None
    return min(nums), max(nums)


def make_row(platform, platform_name, channel, total, **kw):
    dmin, dmax = parse_days(kw.get("days"))
    return {
        "platform": platform,
        "platform_name": platform_name,
        "channel": channel,
        "group": kw.get("group") or "",
        "total": total,
        "currency": kw.get("currency") or "CNY",
        "days": kw.get("days") or "",
        "days_min": dmin,
        "days_max": dmax,
        "track": kw.get("track"),
        "fees": kw.get("fees") or [],
        "remark": kw.get("remark") or "",
    }


def clean_rows(rows):
    """统一过滤脏数据：总价缺失 / 非数字 / NaN / Infinity / ≤0 的行丢弃（上游偶发返回 0 价或占位数据）。"""
    out = []
    for r in rows or []:
        t = r.get("total") if isinstance(r, dict) else None
        if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) or t <= 0:
            continue
        out.append(r)
    return out


API_DOC_PUBLIC = "public"   # 有公开文档
API_DOC_APPLY = "apply"     # 需向服务商申请文档
ENV_OPTS = [{"value": "prod", "label": "正式环境"}, {"value": "sandbox", "label": "测试环境"}]
ENV_FIELD = {"key": "env", "label": "环境", "secret": False, "optional": True, "type": "select",
             "options": ENV_OPTS, "default": "prod", "hidden": True}   # 界面用环境单选，不显示这个下拉；导入/导出仍用 env=…


class Adapter:
    """每个平台支持两种授权模式：
    login = 登录账号授权（用网页/后台账号密码，走登录后的接口或网页）
    api   = 开放平台 API 授权（官方 API 凭证，appKey/secret/token 等）
    子类实现 login_quote/login_test 与 api_quote/api_test；账号页保存 auth_mode 切换。"""
    id = ""
    name = ""
    system = ""          # 系统类型说明
    mode = ""            # 对接方式说明（完整描述，界面里作为提示/平台说明）
    mode_short_login = ""   # 账号页「对接方式」列的简称（≤12 字）
    mode_short_api = ""
    needs_account = True
    default_mode = "login"     # 桌面版沿用 1.5.0：默认登录模式（开放平台 API 需手动切换）
    fixed_mode = None    # 设为 "login"/"api" = 该平台只有一种模式（无授权切换）
    fixed_label = ""     # 单一模式时界面显示的文案，如「公开网站，无需授权」
    login_impl = True    # 登录模式是否已实现
    login_note = ""
    login_fields = [
        {"key": "username", "label": "账号", "secret": False},
        {"key": "password", "label": "密码", "secret": True},
    ]
    api_impl = True      # 开放平台 API 模式是否已实现（至少连接测试）
    api_quote_ok = True  # API 模式是否具备运价试算
    api_doc_status = API_DOC_APPLY
    api_doc_url = ""
    api_doc_note = ""    # 申请方式 / 鉴权方式摘要
    api_fields = []
    note = ""
    # ---- 测试 / 正式环境（只对开放平台 API 模式有意义；登录模式连的是网站本身，没有环境之分）----
    # envs=None → 只有正式环境（如欣航公开网页）；否则 {"prod": 说明, "sandbox": 说明}
    envs = None
    env_note = ""          # 只有正式环境时的说明（界面上显示）
    env_urls = {}          # {"prod": 默认接口地址, "sandbox": ...}，仅展示用
    sandbox_needs_base = False   # 测试环境没有公开地址（如 OIS），必须自己填「接口域名」
    ENV_SUFFIX = "@sandbox"
    ENV_LABELS = {"prod": "正式环境", "sandbox": "测试环境"}
    TEST_REMARK = "【测试环境数据，非真实报价】"

    def __init__(self, store):
        self.store = store

    # ---- 子类实现 ----
    def login_quote(self, q: Query):
        raise NotImplementedError

    def login_test(self):
        raise NotImplementedError

    def api_quote(self, q: Query):
        raise AdapterError(self.api_doc_note or "需向服务商申请 API 文档", "pending")

    def api_test(self):
        raise AdapterError(self.api_doc_note or "需向服务商申请 API 文档", "pending")

    # ---- 模式分发 ----
    # ---- 环境：同一平台两套凭据 / 接口地址并存，互不覆盖 ----
    def env_keys(self):
        """每个环境各存一份的字段（开放平台 API 的凭据 / 接口地址）；env 本身和 common 字段（如 cityId、寄件邮编）不分环境。"""
        return {f["key"] for f in self.api_fields if f["key"] != "env" and not f.get("common")}

    def env_choices(self):
        return list(self.envs) if self.envs else ["prod"]

    def _raw_env(self, raw):
        return "sandbox" if (self.envs and raw.get("env") == "sandbox") else "prod"

    def _legacy_single_set(self, raw):
        """旧版只有一套凭据 + env=sandbox：平铺的那一套其实是测试环境的凭据。"""
        return bool(self.envs) and raw.get("env") == "sandbox" and not any(k.endswith(self.ENV_SUFFIX) for k in raw)

    legacy_sandbox_prefix = ""   # 旧版沙箱凭据的字段前缀（顺丰曾用 sb_xxx），读取时并入「xxx@sandbox」

    def _norm(self, raw):
        p = self.legacy_sandbox_prefix
        if not (p and self.envs):
            return raw
        raw = dict(raw)
        for k in self.env_keys():
            if raw.get(p + k) and not raw.get(k + self.ENV_SUFFIX):
                raw[k + self.ENV_SUFFIX] = raw[p + k]
        return raw

    def env_view(self, raw, env=None):
        """把原始存储（正式 = 原键，测试 = 键@sandbox）展开成指定环境的平铺视图，适配器代码照旧用 a["user_id"] 读取。"""
        if not self.envs:
            return dict(raw)
        raw = self._norm(raw)
        env = env or self._raw_env(raw)
        legacy = self._legacy_single_set(raw)
        v = dict(raw)
        for k in self.env_keys():
            flat, sb = raw.get(k), raw.get(k + self.ENV_SUFFIX)
            if legacy:
                flat, sb = None, flat
            val = flat if env == "prod" else sb
            v.pop(k + self.ENV_SUFFIX, None)
            if val:
                v[k] = val
            else:
                v.pop(k, None)
        v["env"] = env
        return v

    def env_patch(self, raw, values, env=None, activate=True):
        """平铺的字段值 → 带环境的存储补丁。env=写入哪个环境（默认当前环境）；activate=同时把当前环境切到它。"""
        values = dict(values)
        values.pop("env", None)
        if not self.envs:
            return values
        out = {}
        raw = self._norm(raw)
        if self._legacy_single_set(raw):         # 一次性迁移：旧的平铺凭据 → 测试环境，正式环境清空
            for k in self.env_keys():
                if raw.get(k):
                    out[k + self.ENV_SUFFIX] = raw[k]
                    out[k] = ""
        cur = self._raw_env(raw)
        target = env if env in self.envs else cur
        for k, v in values.items():
            if k in self.env_keys():
                out[k if target == "prod" else k + self.ENV_SUFFIX] = v
            else:
                out[k] = v
        if activate and env in self.envs:
            out["env"] = env
        elif "env" in out:
            pass
        elif self._legacy_single_set(raw):
            out["env"] = "sandbox"
        return out

    def env(self):
        return self._raw_env(self.store.get_account(self.id))

    @property
    def is_test_env(self):
        return self.env() != "prod"

    def _env_configured(self, env):
        a = self.env_view(self.store.get_account(self.id), env)
        ok = all(a.get(f["key"]) for f in self.api_fields if not f.get("optional") and f["key"] != "env")
        if env != "prod" and self.sandbox_needs_base and not a.get("api_base"):
            ok = False
        return ok

    def env_info(self):
        raw = self.store.get_account(self.id)
        cur = self._raw_env(raw)
        items = []
        for e in self.env_choices():
            view = self.env_view(raw, e)
            items.append({"value": e, "label": self.ENV_LABELS[e], "tag": "" if e == "prod" else "【测试】",
                          "configured": self._env_configured(e) if self.api_fields else True,
                          "url": self.env_urls.get(e, ""), "needs_base": bool(e != "prod" and self.sandbox_needs_base),
                          "fields": self._fields_view(self.api_fields, view)})
        return {"env": cur, "env_label": self.ENV_LABELS[cur], "env_tag": "" if cur == "prod" else "【测试】",
                "env_only": not self.envs, "env_note": self.env_note, "envs": items}

    def account(self):
        return self.env_view(self.store.get_account(self.id))

    def _default_mode(self):
        """没有显式选择授权方式时：默认「开放平台 API」。
        例外（只为不把已经能用的平台弄坏）：该平台还没填任何 API 凭据、但已有可用的登录账号、且登录模式在当前环境可用
        （网页版里云途登录模式需要本机浏览器，不算可用）→ 暂时回退到登录模式，状态里会标出「回退」。"""
        if self.default_mode != "api" or not (self.api_impl and self.api_quote_ok):
            return "login" if self.login_impl else "api"
        a = self.account()
        api_ok = bool(self.api_fields) and all(a.get(f["key"]) for f in self.api_fields if not f.get("optional"))
        login_ok = bool(self.login_fields) and all(a.get(f["key"]) for f in self.login_fields if not f.get("optional"))
        login_usable = self.login_impl and not (getattr(self, "desktop_only_login", False) and is_cloud())
        if not api_ok and login_ok and login_usable:
            return "login"
        return "api"

    def auth_mode(self):
        if self.fixed_mode:
            return self.fixed_mode
        m = self.account().get("auth_mode")
        return m if m in ("login", "api") else self._default_mode()

    @property
    def mode_fallback(self):
        """未显式选择、因没有 API 凭据而临时使用登录模式。"""
        return (not self.fixed_mode) and self.account().get("auth_mode") not in ("login", "api") and self.auth_mode() == "login" \
            and self.api_impl and self.api_quote_ok

    def cur_fields(self, mode=None):
        return self.api_fields if (mode or self.auth_mode()) == "api" else self.login_fields

    @property
    def fields(self):  # 兼容旧代码：当前模式的字段
        return self.cur_fields()

    @property
    def desktop_blocked(self):
        """云端网页版里，需要本机浏览器的登录模式不可用（只有桌面版能用）。"""
        return bool(getattr(self, "desktop_only_login", False)) and is_cloud() and self.auth_mode() != "api"

    @property
    def implemented(self):
        """当前模式下能否查询运价。"""
        if self.desktop_blocked:
            return False
        if self.auth_mode() == "api":
            return bool(self.api_impl and self.api_quote_ok)
        return bool(self.login_impl)

    @property
    def testable(self):
        if self.desktop_blocked:
            return False
        return bool(self.api_impl) if self.auth_mode() == "api" else bool(self.login_impl)

    def _desktop_guard(self):
        if getattr(self, "desktop_only_login", False) and is_cloud() and self.auth_mode() != "api":
            raise AdapterError(f"{self.name}「登录账号」模式需要本机浏览器（含验证码），仅桌面版可用；网页版请改用「开放平台 API」授权", "manual")

    def quote(self, q: Query):
        self._desktop_guard()
        # 登录一次原则：上次登录/鉴权失败后，凭据没改就不再自动重试（避免账号被风控锁定）；用户点「测试连接」才会再试一次
        if self.store.login_locked(self.id):
            raise AdapterError("上次登录/鉴权失败，为避免账号被锁定已暂停自动重试。请核对账号信息并保存，或点「测试连接」手动重试一次。", "login_failed")
        try:
            rows = self.api_quote(q) if self.auth_mode() == "api" else self.login_quote(q)
        except AdapterError as e:
            if e.kind == "login_failed":
                self.store.lock_login(self.id)
            raise
        rows = clean_rows(rows)
        if self.auth_mode() == "api" and self.is_test_env:      # 测试环境的报价一律打标：不计入全场最低
            for r in rows:
                r["env"] = "sandbox"
                if self.TEST_REMARK not in (r.get("remark") or ""):
                    r["remark"] = (self.TEST_REMARK + " " + (r.get("remark") or "")).strip()
        return rows

    def test_login(self):
        self._desktop_guard()
        try:
            r = self.api_test() if self.auth_mode() == "api" else self.login_test()
        except AdapterError as e:
            if e.kind == "login_failed":
                self.store.lock_login(self.id)
            raise
        self.store.unlock_login(self.id)
        return r

    def has_account(self):
        a = self.account()
        if not all(a.get(f["key"]) for f in self.cur_fields() if not f.get("optional")):
            return False
        if self.auth_mode() == "api" and self.sandbox_needs_base and self.is_test_env and not a.get("api_base"):
            return False
        return True

    def all_field_keys(self):
        return {f["key"] for f in self.login_fields} | {f["key"] for f in self.api_fields}

    def secret_keys(self):
        return {f["key"] for f in self.login_fields + self.api_fields if f.get("secret")}

    @property
    def mode_short(self):
        s = self.mode_short_api if self.auth_mode() == "api" else self.mode_short_login
        return s or (self.mode[:12] if self.mode else "")

    def _fields_view(self, fields, a):
        return [
            {"key": f["key"], "label": f["label"], "secret": f.get("secret", False),
             "optional": f.get("optional", False), "type": f.get("type", "text"),
             "options": f.get("options"), "hint": f.get("hint", ""), "hidden": bool(f.get("hidden")),
             "value": ("" if f.get("secret") else a.get(f["key"], f.get("default", ""))),
             "saved": bool(a.get(f["key"]))}
            for f in fields
        ]

    def status(self):
        rt = self.store.runtime.get(self.id, {})
        a = self.account()
        mode = self.auth_mode()
        enabled = a.get("enabled", True)
        if not self.implemented:
            state = "pending"
            if self.desktop_blocked:
                label = "仅桌面版"
            elif mode == "api" and self.api_impl and not self.api_quote_ok:
                label = "仅连接测试"
            elif mode == "api" and not self.api_impl:
                label = "需申请文档"
            else:
                label = "待验证"
        elif self.needs_account and not self.has_account():
            state, label = "no_account", "待配置账号"
        elif not enabled:
            state, label = "disabled", "已停用"
        elif rt.get("last_error"):
            state, label = "error", "最近一次失败"
        elif rt.get("last_ok"):
            state, label = "ready", "可用"
        else:
            state, label = "idle", "已配置，尚未查询"
        if mode == "api" and not self.implemented and self.api_impl and self.has_account() and rt.get("last_ok"):
            label = "已连接（无运价接口）"
        mode_label = self.fixed_label if self.fixed_mode else ("开放平台API" if mode == "api" else "登录账号")
        if self.mode_fallback:
            mode_label += "（暂用，未配置开放平台API）"
        return {
            "id": self.id, "name": self.name, "system": self.system, "mode": self.mode, "mode_short": self.mode_short,
            "auth_mode": mode, "auth_mode_label": mode_label, "mode_fallback": self.mode_fallback,
            **self.env_info(),
            "fixed_mode": bool(self.fixed_mode), "no_auth": bool(self.fixed_mode) and not self.needs_account,
            "state": state, "label": label, "enabled": enabled, "implemented": self.implemented,
            "testable": self.testable, "has_account": self.has_account() if self.needs_account else True,
            "note": self.note, "last_ok": rt.get("last_ok"), "last_error": rt.get("last_error"),
            "modes": {
                "login": {"impl": self.login_impl, "note": self.login_note,
                          "desktop_only": bool(getattr(self, "desktop_only_login", False)) and is_cloud(),
                          "fields": self._fields_view(self.login_fields, a)},
                "api": {"impl": self.api_impl, "quote_ok": self.api_quote_ok,
                        "doc_status": self.api_doc_status,
                        "doc_status_label": "有公开文档" if self.api_doc_status == API_DOC_PUBLIC else "需向服务商申请文档",
                        "doc_url": self.api_doc_url, "doc_note": self.api_doc_note,
                        "fields": self._fields_view(self.api_fields, a)},
            },
            "fields": self._fields_view(self.cur_fields(mode), a),
        }
