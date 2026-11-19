"""桌面外壳设置（快捷键、浮窗行为、最近一次快速询价输入）。不含任何密钥，存 data/settings.json。"""
import json
import threading

from . import quick
from .paths import data_path

DEFAULTS = {"hotkey": quick.DEFAULT_HOTKEY, "popup_autoclose": True, "close_to_tray": False, "quick_enabled": True,
            "quick_last": {"country": "", "weight": "", "length": "", "width": "", "height": ""}}
_lock = threading.Lock()


def _file():
    return data_path() / "settings.json"


def load():
    try:
        d = json.loads(_file().read_text(encoding="utf-8"))
    except Exception:
        d = {}
    out = json.loads(json.dumps(DEFAULTS))
    for k in DEFAULTS:
        if k in d and type(d[k]) is type(DEFAULTS[k]):
            out[k] = d[k]
    try:
        out["hotkey"] = quick.format_hotkey(out["hotkey"])
    except ValueError:
        out["hotkey"] = quick.DEFAULT_HOTKEY
    last = {k: str(out["quick_last"].get(k, ""))[:40] for k in DEFAULTS["quick_last"]}
    out["quick_last"] = last
    return out


def update(patch):
    """校验后合并保存；不合法抛 ValueError（中文原因）。返回完整设置。"""
    with _lock:
        cur = load()
        for k, v in (patch or {}).items():
            if k not in DEFAULTS:
                continue
            if k == "hotkey":
                cur[k] = quick.format_hotkey(str(v))
            elif k == "quick_last":
                if isinstance(v, dict):
                    cur[k] = {kk: str(v.get(kk, ""))[:40] for kk in DEFAULTS["quick_last"]}
            elif isinstance(v, bool):
                cur[k] = v
            else:
                raise ValueError(f"设置项 {k} 的值不合法")
        p = _file()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
        return cur
