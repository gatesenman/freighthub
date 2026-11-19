"""把账号表（cred.json: {平台名:{u,p}}）导入 data/config.json。仅用于本机初始化，不会打印密码。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.store import Store  # noqa

MAP = {"云途": "yuntu", "中航": "zhonghang", "欣航": None, "环洋": None, "燕文": None, "顺丰": "shunfeng", "雨桐": "yutong"}
src = json.load(open(sys.argv[1], encoding="utf-8"))
st = Store()
for name, pid in MAP.items():
    if pid and name in src:
        st.set_account(pid, {"username": src[name]["u"], "password": src[name]["p"], "enabled": True})
        print("imported", pid)
