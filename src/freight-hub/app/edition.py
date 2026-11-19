"""版本形态：正式版 / 试用版（带「未授权版本 / 试用」水印）。
试用版由构建标志 TRIAL=1 在安装目录写入 app/edition.json，或设环境变量 FREIGHT_HUB_TRIAL=1。"""
import json
import os
from pathlib import Path

_FILE = Path(__file__).with_name("edition.json")


def is_trial() -> bool:
    v = os.environ.get("FREIGHT_HUB_TRIAL")
    if v is not None:
        return v.strip().lower() in ("1", "true", "yes", "on")
    try:
        return bool(json.loads(_FILE.read_text(encoding="utf-8")).get("trial"))
    except Exception:
        return False
