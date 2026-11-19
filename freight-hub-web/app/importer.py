"""平台账号批量导入 / 导出。

一行一个平台配置。表头列：平台 | 授权方式 | 账号 | 密码 | appKey | appSecret | AES秘钥 | 客户编码 | 对接URL | 其他参数 | 备注 | 启用
各平台字段按适配器需要映射（见 SPEC）；模板第二个 sheet 是字段说明。
同时兼容最初的表格：平台名 / 账号 / 密码 / 网址（可以没有表头）。
密钥不会回显：预览只显示「已填写」，真实值只保存在服务端内存中的批次里，确认后才写入 data/config.json。
"""
import base64
import csv
import io
import re
import time
import uuid

from .xlsx import read_xlsx, write_xlsx

COLUMNS = ["平台", "授权方式", "账号", "密码", "appKey", "appSecret", "AES秘钥", "客户编码", "对接URL", "其他参数", "备注", "启用"]
COLKEY = ["platform", "mode", "user", "pwd", "appkey", "appsecret", "aes", "cust", "url", "extra", "note", "enabled"]
WIDTHS = [12, 12, 20, 18, 26, 36, 36, 16, 34, 34, 20, 8]

ALIASES = {
    "platform": ["平台", "平台名", "平台名称", "名称", "系统", "货代", "供应商", "platform", "name"],
    "mode": ["授权方式", "授权模式", "模式", "方式", "mode", "auth"],
    "user": ["账号", "账户", "用户名", "用户", "登录账号", "登录名", "username", "user", "account"],
    "pwd": ["密码", "口令", "password", "pwd", "pass"],
    "appkey": ["appkey", "app_key", "appid", "app_id", "客户号", "user_id", "userid", "accesskey"],
    "appsecret": ["appsecret", "app_secret", "secret", "密钥", "秘钥", "apitoken", "api_token", "token", "apikey", "api_key"],
    "aes": ["aes秘钥", "aes密钥", "aeskey", "aes_key", "aes"],
    "cust": ["客户编码", "客户代码", "customercode", "customer_code"],
    "url": ["对接url", "对接环境url", "对接地址", "接口地址", "接口域名", "url", "网址", "网站", "地址", "链接", "系统地址", "登录地址", "官网"],
    "extra": ["其他参数", "其他", "扩展", "参数", "extra"],
    "note": ["备注", "说明", "remark", "note", "memo"],
    "enabled": ["启用", "是否启用", "enabled"],
}
_HDR = {}
for _k, _names in ALIASES.items():
    for _n in _names:
        _HDR[_n.lower()] = _k

PLATFORM_ALIASES = {
    "yuntu": ["云途", "yunexpress", "yuntu", "yunexp"],
    "zhonghang": ["中航", "zhonghang", "39.108.221.228"],
    "xinhang": ["欣航", "xinhang", "xhgj"],
    "huanyang": ["环洋", "huanyang", "hyexp", "hyeline", "ekttech"],
    "yanwen": ["燕文", "yanwen", "yw56"],
    "shunfeng": ["顺丰", "sf-express", "sf.global", "sfglobal", "shunfeng"],
    "yutong": ["雨桐", "yutong", "ytgj56"],
}

# 各平台 × 授权方式：配置键 ← 模板列（用 | 分隔的是别名，取第一个非空）
SPEC = {
    "yuntu": {"login": {"username": "user", "password": "pwd"},
              "api": {"app_id": "appkey|user", "app_secret": "appsecret|pwd"}},
    "zhonghang": {"login": {"username": "user", "password": "pwd"},
                  "api": {"app_key": "appkey|user", "app_secret": "appsecret|pwd", "api_base": "url"}},
    "huanyang": {"login": {"username": "user", "password": "pwd"},
                 "api": {"app_key": "appkey|user", "app_secret": "appsecret|pwd", "api_base": "url"}},
    "yutong": {"login": {"username": "user", "password": "pwd"},
               "api": {"app_key": "appkey|user", "app_secret": "appsecret|pwd", "api_base": "url"}},
    "yanwen": {"login": {"username": "user", "password": "pwd"},
               "api": {"user_id": "appkey|user", "api_token": "appsecret|pwd"}},
    "shunfeng": {"login": {"username": "user", "password": "pwd"},
                 "api": {"app_key": "appkey|user", "app_secret": "appsecret|pwd", "aes_key": "aes", "customer_code": "cust"}},
}
COL_LABEL = dict(zip(COLKEY, COLUMNS))
API_HELP = {
    "yuntu": ("appKey 列 = appId；appSecret 列 = appSecret（apiKey）", "source_key=…; env=prod|sandbox; access_token=…（沙箱才需要）", "云途开放平台后台获取 appId/appSecret"),
    "zhonghang": ("appKey、appSecret", "对接URL 一般留空（默认 openapi.intelink.net.cn）；products=路线编码1,路线编码2", "V5 后台「API 授权」"),
    "huanyang": ("appKey、appSecret、对接URL", "对接URL 填后台给的「对接环境URL」（如 https://ekttech.net/pos-web）；products=…", "TMS 后台「API 授权」（切勿点“重新生成密钥”）"),
    "yutong": ("appKey、appSecret", "对接URL 一般留空；products=…", "系统后台「API 授权」"),
    "yanwen": ("appKey 列 = 客户号 user_id；appSecret 列 = apitoken", "city_id=01; env=prod|sandbox", "燕文开放平台"),
    "shunfeng": ("appKey、appSecret、AES秘钥、客户编码", "api_kind=sfglobal|fengqiao; env=prod|sandbox; products=INT0007,…; sender_post=…; pay_month_card=…; partner_id=…; check_word=…（丰桥）",
                 "顺丰国际开放平台 openapi.sf.global；丰桥作备选"),
}
LOGIN_HELP = {
    "yuntu": "账号、密码（登录需要本机 Edge/Chrome，出现验证码时要手动完成）",
    "zhonghang": "账号、密码；company=公司编号（可选，写在“其他参数”）",
    "huanyang": "账号、密码；company=公司编号（可选）",
    "yutong": "账号、密码",
    "yanwen": "账号、密码",
    "shunfeng": "账号、密码",
}
# 填错时给的示例（假数据）
EXAMPLES = [
    ["云途", "API", "", "", "示例AppId", "示例appSecret", "", "", "", "env=prod", "示例行，不要直接导入", "是"],
    ["中航", "登录", "示例账号", "示例密码", "", "", "", "", "", "", "", "是"],
    ["环洋", "API", "", "", "示例appKey", "示例appSecret", "", "", "https://ekttech.net/pos-web", "", "", "是"],
    ["顺丰国际", "API", "", "", "示例APPKey", "示例APPSecret", "示例AES秘钥(43位)", "示例客户编码", "", "env=prod", "", "是"],
    ["燕文", "API", "", "", "示例客户号", "示例apitoken", "", "", "", "city_id=01", "", "是"],
    ["欣航", "", "", "", "", "", "", "", "", "", "公开网站免授权，导入时会自动跳过", ""],
]

_masked = re.compile(r"\*{3,}|•{3,}|●{3,}")


def _norm(s):
    s = str(s or "").strip().lower()
    s = re.split(r"[（(]", s)[0].strip()
    return s.replace(" ", "").replace("\u3000", "")


def mask_secret(v):
    v = str(v or "")
    if not v:
        return ""
    return v[:2] + "****" + v[-2:] if len(v) >= 10 else "****"


def is_masked(v):
    return bool(_masked.search(str(v or "")))


# ------------------------------------------------------------------ 解析文件
def decode_csv(data: bytes):
    for enc in ("utf-8-sig", "gb18030"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode("utf-8", "replace")
    sample = text[:2000]
    delim = "\t" if sample.count("\t") > sample.count(",") else ","
    return [[c.strip() for c in r] for r in csv.reader(io.StringIO(text), delimiter=delim)]


def load_table(filename: str, data: bytes):
    """返回 [[cell...], ...]；xlsx 取名为「账号配置」的 sheet，没有就取第一个有内容的 sheet。"""
    fn = (filename or "").lower()
    if fn.endswith(".xlsx") or data[:2] == b"PK":
        sheets = read_xlsx(data)
        if not sheets:
            raise ValueError("Excel 文件里没有工作表")
        pick = next((s for s in sheets if s[0] in ("账号配置", "账号", "Sheet1")), None) or next((s for s in sheets if any(any(c for c in r) for r in s[1])), sheets[0])
        return pick[1]
    if fn.endswith(".xls"):
        raise ValueError("不支持旧版 .xls，请在 Excel 里另存为 .xlsx 或 CSV")
    return decode_csv(data)


def resolve_platform(text, registry):
    t = _norm(text)
    if not t:
        return None
    for pid, a in registry.items():
        if t == _norm(a.name) or t == pid:
            return pid
    for pid, names in PLATFORM_ALIASES.items():
        if pid in registry and any(n.lower() in t for n in names):
            return pid
    for pid, a in registry.items():
        if _norm(a.name) and (_norm(a.name) in t or t in _norm(a.name)):
            return pid
    return None


def parse_rows(table):
    """表格 → [{row, platform_text, cells{colkey:value}}]。自动识别表头；没有表头时按 平台/账号/密码/网址 的顺序。"""
    hdr_idx, colmap = None, {}
    for i, r in enumerate(table[:12]):
        m = {}
        for j, c in enumerate(r):
            k = _HDR.get(_norm(c))
            if k and k not in m.values():
                m[j] = k
        if "platform" in m.values() and len(m) >= 2:
            hdr_idx, colmap = i, m
            break
    items = []
    if hdr_idx is None:
        colmap = {0: "platform", 1: "user", 2: "pwd", 3: "url"}
        start = 0
    else:
        start = hdr_idx + 1
    for i in range(start, len(table)):
        r = table[i]
        cells = {k: str(r[j]).strip() for j, k in colmap.items() if j < len(r) and r[j] is not None and str(r[j]).strip() != ""}
        if not cells:
            continue
        items.append({"row": i + 1, "cells": cells, "headerless": hdr_idx is None})
    return items


def parse_extra(text):
    out = {}
    for part in re.split(r"[;；\n\r]+", text or ""):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^([^=:：＝]+)[=:：＝](.*)$", part)
        if m:
            out[m.group(1).strip()] = m.group(2).strip()
    return out


def _bool(v):
    return str(v).strip().lower() not in ("否", "n", "no", "false", "0", "停用", "禁用")


def _pick(cells, spec):
    for k in spec.split("|"):
        if cells.get(k):
            return cells[k], k
    return "", None


def decide_mode(cells, pid, adapter):
    raw = _norm(cells.get("mode", ""))
    if raw:
        if raw in ("api", "openapi", "开放平台", "开放平台api", "接口", "api授权", "开放api") or "api" in raw or "开放" in raw or "接口" in raw:
            return "api"
        if raw in ("登录", "login", "账号", "账号密码", "登录账号", "登录授权", "密码") or "登录" in raw or "账号" in raw or "login" in raw:
            return "login"
        return None
    if cells.get("appkey") or cells.get("appsecret") or cells.get("aes") or cells.get("cust"):
        return "api"
    return "login"


# ------------------------------------------------------------------ 预览 / 应用
_batches = {}


def _field_meta(adapter):
    return {f["key"]: f for f in adapter.login_fields + adapter.api_fields}


def _build_item(it, registry, store):
    cells = it["cells"]
    res = {"row": it["row"], "platform_text": cells.get("platform", ""), "pid": None, "name": "", "mode": None,
           "action": "error", "message": "", "fields": [], "patch": {}, "changed": [], "enabled": None, "remark": cells.get("note", "")}
    pid = resolve_platform(cells.get("platform", ""), registry)
    if not pid:
        res["message"] = f"无法识别平台「{cells.get('platform', '')}」"
        return res
    a = registry[pid]
    res.update(pid=pid, name=a.name)
    if a.fixed_mode and not a.needs_account:
        res.update(action="skip", message="公开网站，免授权，已跳过")
        return res
    mode = decide_mode(cells, pid, a)
    if not mode:
        res["message"] = f"授权方式「{cells.get('mode')}」无法识别，请填 登录 或 API"
        return res
    res["mode"] = mode
    spec = SPEC.get(pid, {}).get(mode)
    if spec is None:
        res["message"] = "该平台没有这种授权方式"
        return res
    meta = _field_meta(a)
    patch, used = {}, set()
    for key, colspec in spec.items():
        val, col = _pick(cells, colspec)
        if col:
            used.add(col)
        if val and not is_masked(val):
            patch[key] = val if meta.get(key, {}).get("secret") else val.strip()
    # 对接URL 在登录模式下只是系统网址，不写入
    extra = parse_extra(cells.get("extra", ""))
    label2key = {f["label"]: f["key"] for f in (a.api_fields if mode == "api" else a.login_fields)}
    allowed = {f["key"] for f in (a.api_fields if mode == "api" else a.login_fields)}
    for k, v in extra.items():
        key = k if k in allowed else label2key.get(k) or next((kk for kk in allowed if kk.lower() == k.lower()), None)
        if key is None:
            low = {"环境": "env", "出发仓": "city_id", "公司编号": "company", "公司": "company", "产品": "products"}.get(k)
            key = low if low in allowed else None
        if key is None:
            res["message"] = f"其他参数里的「{k}」不是该平台的字段（可用：{', '.join(sorted(allowed))}）"
            return res
        if is_masked(v):
            continue
        f = meta.get(key, {})
        if f.get("type") == "select" and f.get("options") and v not in [o["value"] for o in f["options"]]:
            opt = next((o["value"] for o in f["options"] if o["label"].startswith(v)), None)
            if not opt and key == "env":      # 环境的常见叫法：测试 / 沙箱 / 沙盒 / 正式 / 生产
                alias = next((val for kw, val in (("沙", "sandbox"), ("测试", "sandbox"), ("test", "sandbox"), ("fat", "sandbox"), ("sit", "sandbox"),
                                                  ("正式", "prod"), ("生产", "prod"), ("线上", "prod")) if v.lower().startswith(kw)), None)
                opt = alias if alias in [o["value"] for o in f["options"]] else None
            if not opt:
                res["message"] = f"参数 {key} 的值「{v}」不在可选项内（{', '.join(o['value'] for o in f['options'])}）"
                return res
            v = opt
        patch[key] = v if f.get("secret") else v.strip()
    ignored = [COL_LABEL[c] for c in ("appkey", "appsecret", "aes", "cust", "url") if cells.get(c) and c not in used and not (c == "url" and mode == "login")]
    # 必填检查（已有配置的字段可以留空 = 保持不变）
    old = a.env_view(store.get_account(pid), patch.get("env"))     # 与该行目标环境的现有凭据比较（其它环境的凭据不受影响）
    need = [f for f in (a.api_fields if mode == "api" else a.login_fields) if not f.get("optional")]
    miss = [f["label"] for f in need if not patch.get(f["key"]) and not old.get(f["key"])]
    if pid == "shunfeng" and mode == "api":
        if not (patch.get("app_key") or old.get("app_key")) and not (patch.get("partner_id") or old.get("partner_id")):
            miss.append("APP Key（或丰桥顾客编码 partner_id）")
        if patch.get("partner_id") or old.get("partner_id"):
            if not patch.get("api_kind") and not old.get("api_kind") and not (patch.get("app_key") or old.get("app_key")):
                patch["api_kind"] = "fengqiao"
    if miss:
        res["message"] = "缺少：" + "、".join(miss)
        return res
    if not patch:
        res["message"] = "没有可写入的内容"
        return res
    # 显示 + 新增/覆盖判断
    changed = []
    for k, v in patch.items():
        f = meta.get(k, {})
        was = old.get(k)
        if was != v:
            changed.append(k)
        res["fields"].append({"key": k, "label": f.get("label", k), "secret": bool(f.get("secret")),
                              "value": "" if f.get("secret") else v, "changed": was != v, "had": bool(was)})
    has_old = any(old.get(f["key"]) for f in (a.api_fields if mode == "api" else a.login_fields) if f["key"] != "env")
    if not has_old:
        res["action"] = "add"
    elif changed:
        res["action"] = "overwrite"
    else:
        res["action"] = "same"
    res["changed"] = changed
    res["patch"] = patch
    if cells.get("enabled") != "" and "enabled" in cells:
        res["enabled"] = _bool(cells["enabled"])
    msg = []
    if ignored:
        msg.append("未使用的列：" + "、".join(ignored))
    if it.get("headerless"):
        msg.append("无表头，按 平台/账号/密码/网址 读取")
    res["message"] = "；".join(msg)
    return res


def preview(filename, data_b64, registry, store):
    data = base64.b64decode(data_b64 or "")
    if not data:
        raise ValueError("文件是空的")
    table = load_table(filename, data)
    items = parse_rows(table)
    if not items:
        raise ValueError("没有读到任何数据行：请确认第一行是表头（平台、授权方式、账号、密码…），下面一行一个平台")
    if len(items) > 1000:
        raise ValueError(f"数据行太多（{len(items)} 行，上限 1000 行），请拆分后导入")
    rows = [_build_item(it, registry, store) for it in items]
    # 同一平台多行：登录+API 两套都存，当前使用 API；同模式重复则后者覆盖前者
    seen = {}
    for r in rows:
        if r["action"] in ("add", "overwrite", "same"):
            key = (r["pid"], r["mode"])
            if key in seen:
                seen[key]["action"] = "dup"
                seen[key]["message"] = f"与第 {r['row']} 行重复，以后者为准"
            seen[key] = r
    by_pid = {}
    for r in rows:
        if r["action"] in ("add", "overwrite", "same"):
            by_pid.setdefault(r["pid"], []).append(r)
    for pid, rs in by_pid.items():
        if len({x["mode"] for x in rs}) > 1:
            for x in rs:
                x["message"] = (x["message"] + "；" if x["message"] else "") + "同一平台同时给了登录和 API：两套都保存，当前使用 API"
    bid = uuid.uuid4().hex[:12]
    store.batch_put(bid, rows)
    out = []
    for i, r in enumerate(rows):
        out.append({"i": i, "row": r["row"], "platform_text": r["platform_text"], "pid": r["pid"], "name": r["name"], "mode": r["mode"],
                    "action": r["action"], "message": r["message"], "fields": r["fields"],
                    "mode_label": {"api": "开放平台API", "login": "登录账号"}.get(r["mode"], "")})
    cnt = {}
    for r in rows:
        cnt[r["action"]] = cnt.get(r["action"], 0) + 1
    return {"batch": bid, "rows": out, "summary": cnt}


def apply(batch_id, selected, registry, store):
    rows = store.batch_pop(batch_id)
    if rows is None:
        raise KeyError("导入批次已过期，请重新选择文件")
    idx = set(selected) if selected is not None else {i for i, r in enumerate(rows) if r["action"] in ("add", "overwrite")}
    # 同平台两种模式：当前使用 API
    pick_mode = {}
    for i, r in enumerate(rows):
        if i in idx and r["action"] in ("add", "overwrite", "same"):
            if r["mode"] == "api" or r["pid"] not in pick_mode:
                pick_mode[r["pid"]] = r["mode"]
    results = []
    done_pid = set()
    for i, r in enumerate(rows):
        if i not in idx or r["action"] not in ("add", "overwrite", "same"):
            continue
        a = registry[r["pid"]]
        patch = dict(r["patch"])
        patch["auth_mode"] = pick_mode[r["pid"]]
        if r["enabled"] is not None:
            patch["enabled"] = r["enabled"]
        elif store.get_account(r["pid"]).get("enabled") is None:
            patch["enabled"] = True
        if r["remark"]:
            patch["remark"] = r["remark"]
        if r["changed"] or store.get_account(r["pid"]).get("auth_mode") != patch["auth_mode"]:
            store.clear_session(r["pid"])
            store.clear_session(r["pid"] + "_api")
            store.runtime.pop(r["pid"], None)
        flat = {k: v for k, v in patch.items() if k in a.all_field_keys()}
        rest = {k: v for k, v in patch.items() if k not in flat}
        env_t = flat.get("env")      # 这一行写入哪个环境（行里写了 env=… 才会同时切换当前环境）
        store.set_account(r["pid"], {**rest, **a.env_patch(store.get_account(r["pid"]), flat, env=env_t, activate=bool(env_t))})
        results.append({"i": i, "pid": r["pid"], "name": a.name, "mode": patch["auth_mode"], "action": r["action"],
                        "active": pick_mode[r["pid"]] == r["mode"], "testable": False})
    # 每个平台只测一次（当前使用的模式），避免多次登录触发锁号
    for x in results:
        if x["active"] and x["pid"] not in done_pid:
            x["testable"] = bool(registry[x["pid"]].testable)
            done_pid.add(x["pid"])
    return results


# ------------------------------------------------------------------ 模板 / 导出
def _inverse(pid, mode):
    """配置键 → 模板列"""
    return {k: v.split("|")[0] for k, v in SPEC.get(pid, {}).get(mode, {}).items()}


def export_rows(registry, store, secrets="none"):
    rows = []
    for pid, a in registry.items():
        if a.fixed_mode and not a.needs_account:
            continue
        raw_acc = store.get_account(pid)
        meta = _field_meta(a)
        variants = [("login", a.login_fields, None)] + [("api", a.api_fields, e) for e in a.env_choices()]
        for mode, fields, env_e in variants:
            acc = a.env_view(raw_acc, env_e) if env_e else raw_acc      # 每个环境各导出一行（非正式环境带 env=sandbox）
            keys = [f["key"] for f in fields]
            if not any(acc.get(k) for k in keys if k != "env"):
                continue
            inv = _inverse(pid, mode)
            cell = {c: "" for c in COLKEY}
            extra = []
            for k in keys:
                v = acc.get(k)
                if not v or (k == "env" and v == "prod"):
                    continue
                secret = meta[k].get("secret")
                shown = (mask_secret(v) if secrets == "mask" else "") if secret else v
                col = inv.get(k)
                if col and not cell[col]:
                    cell[col] = shown
                else:
                    if secret and not shown:
                        continue
                    extra.append(f"{k}={shown}")
            cell["platform"] = a.name
            cell["mode"] = "API" if mode == "api" else "登录"
            cell["extra"] = "; ".join(extra)
            cell["note"] = acc.get("remark", "")
            cell["enabled"] = "是" if acc.get("enabled", True) else "否"
            rows.append([cell[k] for k in COLKEY])
    return rows


def _guide_rows(registry):
    g = [["说明", "内容"],
         ["填写方式", "「账号配置」sheet：一行一个平台配置。同一平台可以写两行（登录一行、API 一行），当前使用 API。"],
         ["授权方式", "填「登录」或「API」；留空时：填了 appKey/appSecret 视为 API，否则视为登录。"],
         ["密码/密钥留空", "覆盖已有账号时，密码、密钥留空 = 保持原值不变；含 **** 的打码值也不会覆盖原值。"],
         ["其他参数", "写成 键=值，多个用分号隔开，如 env=sandbox; products=A,B。键名见下表。"],
         ["兼容旧表格", "只有 平台名 / 账号 / 密码 / 网址 四列（甚至没有表头）也能导入，按登录方式处理；网址仅供核对。"],
         ["欣航", "公开网站，免授权，导入时自动跳过。"],
         ["安全", "表格里含明文密码，用完请删除；导出当前配置时可选择不导出或打码密钥。"],
         [], ["平台", "授权方式", "需要填写的列", "其他参数（键=值）", "获取方式 / 说明"]]
    for pid, a in registry.items():
        if a.fixed_mode and not a.needs_account:
            g.append([a.name, "—", "无需填写", "", "公开网站免授权，导入自动跳过"])
            continue
        g.append([a.name, "登录", LOGIN_HELP.get(pid, "账号、密码"), "", a.login_note or "登录网页/后台所用账号"])
        if pid in API_HELP:
            cols, extra, how = API_HELP[pid]
            g.append([a.name, "API", cols, extra, how])
    return g


def build_template(registry, fmt="xlsx"):
    if fmt == "csv":
        buf = io.StringIO()
        csv.writer(buf).writerow(COLUMNS)
        return ("\ufeff" + buf.getvalue()).encode("utf-8"), "text/csv; charset=utf-8", "账号导入模板.csv"
    names = [a.name for a in registry.values() if a.needs_account]
    sheets = [
        {"name": "账号配置", "rows": [COLUMNS], "widths": WIDTHS, "header": True,
         "validations": [("A2:A300", names), ("B2:B300", ["登录", "API"]), ("L2:L300", ["是", "否"])]},
        {"name": "字段说明", "rows": _guide_rows(registry), "widths": [14, 12, 46, 62, 46], "header": True, "wrap": True},
        {"name": "示例（假数据）", "rows": [COLUMNS] + EXAMPLES, "widths": WIDTHS, "header": True},
    ]
    return write_xlsx(sheets), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "账号导入模板.xlsx"


def build_export(registry, store, fmt="xlsx", secrets="none"):
    rows = export_rows(registry, store, secrets)
    tag = "密钥已打码" if secrets == "mask" else "不含密钥"
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(COLUMNS)
        w.writerows(rows)
        return ("\ufeff" + buf.getvalue()).encode("utf-8"), "text/csv; charset=utf-8", f"账号配置导出_{tag}.csv"
    sheets = [{"name": "账号配置", "rows": [COLUMNS] + rows, "widths": WIDTHS, "header": True},
              {"name": "字段说明", "rows": _guide_rows(registry), "widths": [14, 12, 46, 62, 46], "header": True, "wrap": True}]
    return write_xlsx(sheets), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", f"账号配置导出_{tag}.xlsx"
