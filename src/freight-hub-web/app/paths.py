"""用户数据目录。

- 便携版（zip 解压运行）：程序目录下的 data/
- 安装版（安装程序会在程序目录放一个 installed.flag）：%LOCALAPPDATA%\\FreightHub\\data
  这样升级/卸载不会丢账号和历史，且即使装在只读目录（如 Program Files）也能写。
- 环境变量 FREIGHT_HUB_DATA 优先。
"""
import os
import sys
from pathlib import Path


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def data_path() -> Path:
    env = os.environ.get("FREIGHT_HUB_DATA")
    if env:
        return Path(env)
    root = app_root()
    if (root / "installed.flag").exists():
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
        return Path(base) / "FreightHub" / "data"
    return root / "data"
