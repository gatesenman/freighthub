"""快速询价浮窗的纯逻辑（不依赖 Windows / Web 框架，Linux 上可直接单元测试）：
  - 文本解析：「美国 2kg」「US 1.5 30x20x10」→ 国家 / 重量 / 尺寸
  - 聚合：每个平台的最低价 + 全部平台最低价
  - 浮窗定位：鼠标位置 → 浮窗左上角（多显示器 / 负坐标 / 高 DPI / 贴边翻转 / 夹紧到工作区）"""
import math
import re

from .countries import COUNTRIES

_BY_CODE = {c: n for c, n in COUNTRIES}
_BY_NAME = {n: c for c, n in COUNTRIES}
_ALIAS = {"英国": "GB", "UK": "GB", "美利坚": "US", "USA": "US", "韩国": "KR", "南韩": "KR", "阿联酋": "AE", "俄国": "RU", "德": "DE", "法": "FR", "日": "JP"}
_NUM = r"(\d+(?:\.\d+)?)"
_DIM = re.compile(rf"{_NUM}\s*[x×*X]\s*{_NUM}\s*[x×*X]\s*{_NUM}(?:\s*cm)?", re.I)
_WEIGHT_UNIT = re.compile(rf"{_NUM}\s*(kg|公斤|千克|g|克|斤)(?![A-Za-z])", re.I)


def resolve_country(text):
    """'US' / 'us' / '美国' / '美国 US' → ('US', '美国')；认不出 → None。"""
    t = (text or "").strip()
    if not t:
        return None
    up = t.upper()
    if up in _BY_CODE:
        return up, _BY_CODE[up]
    for tok in re.split(r"[\s,，/]+", t):
        u = tok.upper()
        if u in _BY_CODE:
            return u, _BY_CODE[u]
        if tok in _BY_NAME:
            return _BY_NAME[tok], tok
        if tok in _ALIAS or u in _ALIAS:
            c = _ALIAS.get(tok) or _ALIAS[u]
            return c, _BY_CODE.get(c, c)
    # 「美国 US」之外的模糊：名称包含
    for n, c in _BY_NAME.items():
        if len(t) >= 2 and (t in n or n in t):
            return c, n
    return None


def parse_quick_text(text):
    """解析一行快速输入。返回 dict(country, country_name, weight, length, width, height)，失败抛 ValueError（中文原因）。"""
    s = (text or "").strip()
    if not s:
        raise ValueError("请输入「国家 重量」，例如：美国 2kg 或 US 1.5 30x20x10")
    length = width = height = 0.0
    m = _DIM.search(s)
    if m:
        length, width, height = (float(x) for x in m.groups())
        s = (s[:m.start()] + " " + s[m.end():]).strip()
    weight = None
    if re.search(r"(?<![\d.])-\s*\d", s):
        raise ValueError("重量必须大于 0（不能是负数）")
    m = _WEIGHT_UNIT.search(s)
    if m:
        v, u = float(m.group(1)), m.group(2).lower()
        weight = v / 1000 if u in ("g", "克") else v / 2 if u == "斤" else v
        s = (s[:m.start()] + " " + s[m.end():]).strip()
    else:
        m = re.search(rf"(?<![A-Za-z]){_NUM}(?![A-Za-z])", s)
        if m:
            weight = float(m.group(1))
            s = (s[:m.start()] + " " + s[m.end():]).strip()
    hit = resolve_country(s)
    if not hit:
        raise ValueError(f"没认出目的国「{s or '空'}」：请写国家中文名或两位代码（如 美国 / US）")
    if weight is None or not math.isfinite(weight) or weight <= 0:
        raise ValueError("请给出大于 0 的重量，例如 2kg")
    if weight > 100000 or max(length, width, height) > 10000:
        raise ValueError("重量或尺寸超出范围")
    return {"country": hit[0], "country_name": hit[1], "weight": round(weight, 3), "length": length, "width": width, "height": height}


# ------------------------------------------------------------------ 聚合
def _cny(row):
    return (row.get("currency") or "CNY").upper() == "CNY"


def best_row(rows):
    """一个平台里的最低价：人民币优先（不同币种不混比）；没有人民币时取最低的外币并带币种。"""
    rows = [r for r in rows or [] if isinstance(r.get("total"), (int, float)) and r["total"] > 0]
    if not rows:
        return None
    r = min(rows, key=lambda x: (bool(x.get("excluded")), not _cny(x), x["total"]))   # 测试环境等「不计入最低」的行排在最后
    return {"excluded": r.get("excluded") or "", "channel": r.get("channel", ""), "total": r["total"], "currency": (r.get("currency") or "CNY").upper(),
            "days": r.get("days") or "", "days_min": r.get("days_min"), "days_max": r.get("days_max"), "remark": r.get("remark") or ""}


def summarize(platform_infos, rows):
    """platform_infos：run_platform 的 info 列表；rows：所有行。
    返回 (platforms, overall)：platforms 每项带 best / count；overall 为全部平台里最低价（人民币优先）。"""
    by = {}
    for r in rows:
        by.setdefault(r.get("platform"), []).append(r)
    plats = []
    for i in platform_infos:
        item = dict(i)
        item["best"] = best_row(by.get(i["id"]))
        plats.append(item)
    cands = [(p["best"], p) for p in plats if p["best"]]
    overall = None
    if cands:
        b, p = min(cands, key=lambda c: (bool(c[0].get("excluded")), c[0]["currency"] != "CNY", c[0]["total"]))
        overall = dict(b, platform=p["id"], platform_name=p["name"])
    return plats, overall


# ------------------------------------------------------------------ 浮窗定位
def _norm(rect):
    l, t, r, b = rect
    return (min(l, r), min(t, b), max(l, r), max(t, b))


def pick_monitor(point, monitors):
    """鼠标所在显示器；落在缝隙里就取最近的。monitors：[(left, top, right, bottom)]（物理像素，可为负）。"""
    if not monitors:
        raise ValueError("没有显示器信息")
    x, y = point
    best, bd = None, None
    for m in monitors:
        l, t, r, b = _norm(m)
        if l <= x < r and t <= y < b:
            return (l, t, r, b)
        dx = max(l - x, 0, x - (r - 1))
        dy = max(t - y, 0, y - (b - 1))
        d = dx * dx + dy * dy
        if bd is None or d < bd:
            best, bd = (l, t, r, b), d
    return best


def place_popup(cursor, size, work_area, offset=14, margin=8):
    """浮窗左上角（物理像素）。默认出现在鼠标右下方；右 / 下放不下就翻到鼠标左 / 上方；最后夹紧在工作区内。
    size=(w,h) 为物理像素（调用方已乘 DPI 缩放）；offset / margin 同为物理像素。窗口比工作区还大时贴左上角。"""
    cx, cy = cursor
    w, h = size
    l, t, r, b = _norm(work_area)
    x, y = cx + offset, cy + offset
    if x + w > r - margin:
        x = cx - offset - w
    if y + h > b - margin:
        y = cy - offset - h
    x = min(x, r - margin - w)
    y = min(y, b - margin - h)
    x = max(x, l + margin)
    y = max(y, t + margin)
    return int(x), int(y)


def popup_geometry(cursor, size_dip, monitors, scale_of=None, offset_dip=14, margin_dip=8):
    """综合：选显示器 → 按该显示器 DPI 缩放尺寸 → 定位。scale_of(monitor_rect)->缩放系数（1.0 / 1.25 / 1.5 / 2.0…）。
    返回 {x, y, w, h, scale, monitor}（物理像素）。"""
    mon = pick_monitor(cursor, monitors)
    sc = float(scale_of(mon)) if scale_of else 1.0
    if not (0.5 <= sc <= 6):
        sc = 1.0
    w, h = int(round(size_dip[0] * sc)), int(round(size_dip[1] * sc))
    w = min(w, mon[2] - mon[0] - 2 * int(margin_dip * sc))
    h = min(h, mon[3] - mon[1] - 2 * int(margin_dip * sc))
    x, y = place_popup(cursor, (w, h), mon, int(round(offset_dip * sc)), int(round(margin_dip * sc)))
    return {"x": x, "y": y, "w": w, "h": h, "scale": sc, "monitor": list(mon)}


# ------------------------------------------------------------------ 全局快捷键
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x1, 0x2, 0x4, 0x8
_MODS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN, "windows": MOD_WIN, "super": MOD_WIN}
_NAMED_VK = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "home": 0x24, "end": 0x23, "insert": 0x2D, "delete": 0x2E,
             "pageup": 0x21, "pagedown": 0x22, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "`": 0xC0, "-": 0xBD, "=": 0xBB,
             "[": 0xDB, "]": 0xDD, ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF, "\\": 0xDC}
DEFAULT_HOTKEY = "Ctrl+Alt+Q"


def parse_hotkey(text):
    """'Ctrl+Alt+Q' → (modifiers, virtual-key)。必须至少一个修饰键（避免占用普通字母键）；不合法抛 ValueError（中文原因）。"""
    parts = [p.strip() for p in re.split(r"\s*\+\s*", (text or "").strip()) if p.strip()]
    if len(parts) < 2:
        raise ValueError("快捷键至少要「修饰键 + 一个按键」，例如 Ctrl+Alt+Q")
    mods, key = 0, parts[-1]
    for p in parts[:-1]:
        m = _MODS.get(p.lower())
        if not m:
            raise ValueError(f"不认识的修饰键「{p}」，可用：Ctrl / Alt / Shift / Win")
        mods |= m
    if not mods & (MOD_CONTROL | MOD_ALT | MOD_WIN):
        raise ValueError("快捷键需要包含 Ctrl、Alt 或 Win（只用 Shift 会影响正常打字）")
    k = key.lower()
    if re.fullmatch(r"[a-z]", k):
        vk = ord(k.upper())
    elif re.fullmatch(r"[0-9]", k):
        vk = ord(k)
    elif re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", k):
        vk = 0x70 + int(k[1:]) - 1
    elif k in _NAMED_VK:
        vk = _NAMED_VK[k]
    else:
        raise ValueError(f"不认识的按键「{key}」，可用：A-Z、0-9、F1-F24、空格等")
    return mods, vk


def format_hotkey(text):
    """规范化显示：'ctrl + alt + q' → 'Ctrl+Alt+Q'。"""
    mods, vk = parse_hotkey(text)
    names = [n for m, n in ((MOD_CONTROL, "Ctrl"), (MOD_ALT, "Alt"), (MOD_SHIFT, "Shift"), (MOD_WIN, "Win")) if mods & m]
    key = next((k.title() if len(k) > 1 else k for k, v in _NAMED_VK.items() if v == vk), None)
    if key is None:
        key = chr(vk) if (0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A) else f"F{vk - 0x6F}"
    return "+".join(names + [key])
