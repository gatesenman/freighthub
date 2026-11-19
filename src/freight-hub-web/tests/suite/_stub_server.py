"""UI / 网页模式端到端测试用的「桩服务」：真实的 FreightHub FastAPI 应用 + 打桩的平台 HTTP。

用法：python -m tests.suite._stub_server <desktop|server> <port> <data_dir>
- 平台 HTTP 全部打桩（只有 雨桐 OIS 成功、环洋 鉴权失败、其余无账号 / 欣航超时），任何真实外网请求都会报错。
- 种入的账号凭据是明显的假数据（FAKE-…）。
"""
import json
import os
import sys
import time

mode, port, data = sys.argv[1], int(sys.argv[2]), sys.argv[3]
os.environ["FREIGHT_HUB_DATA"] = data
os.environ["FREIGHT_HUB_PORT"] = str(port)
os.environ.pop("LOCALAPPDATA", None)
if mode == "server":
    os.environ["FREIGHT_HUB_MODE"] = "server"
    os.environ.setdefault("FREIGHT_HUB_ADMIN_PASSWORD", "Admin-Pass-123")
else:
    os.environ.pop("FREIGHT_HUB_MODE", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import requests  # noqa: E402


class R:
    def __init__(self, data, status=200):
        self._d, self.status_code, self.headers, self.encoding = data, status, {}, "utf-8"
        self.text = json.dumps(data, ensure_ascii=False)
        self.content = self.text.encode()
        self.ok = status < 400

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


ITEMS = [
    {"code": "P1", "name": "美国专线小包", "standardFeeSum": 123.45, "standardCharge": 100, "fuelCharge": 10.5, "otherStandardFeeSum": 12.95,
     "standardCur": "CNY", "aging": "5-8", "zoneName": "一区", "remark": "测试备注"},
    {"code": "P2", "name": "美国经济专线", "standardFeeSum": 99.9, "standardCharge": 90, "fuelCharge": 9.9, "otherStandardFeeSum": 0,
     "standardCur": "CNY", "aging": "8-12", "zoneName": "一区", "remark": ""},
    {"code": "P3", "name": "美国快递", "standardFeeSum": 215.0, "standardCharge": 200, "fuelCharge": 15, "otherStandardFeeSum": 0,
     "standardCur": "CNY", "aging": "3-5", "zoneName": "二区", "remark": ""},
]


def dispatch(method, url, **kw):
    if "ekttech" in url:                                   # 环洋：鉴权失败（用来测错误态）
        return R({"result_code": 1, "body": {"ack": "false", "message": "appKey invalid"}})
    if "intelink" in url:
        if "getAuth" in url:
            return R({"body": {"ack": "true", "token": "FAKE-TOKEN", "clno": "C1", "coid": "CO1"}})
        if "getProductList" in url:
            return R({"result_code": 0, "body": [{"code": "P1", "hubType": "专线"}]})
        if "queryQuoteList" in url:
            return R({"result_code": 0, "body": ITEMS})
    if "127.0.0.1" in url or "localhost" in url:
        return None
    raise requests.ConnectionError("stub: 未打桩的网络请求 " + url[:60])


def _wrap(method):
    def f(*a, **kw):
        url = a[0] if a else kw.pop("url")
        r = dispatch(method, url, **kw)
        return r
    return f


requests.request = lambda method, url, **kw: dispatch(method, url, **kw)
requests.post = lambda url, **kw: dispatch("POST", url, **kw)
requests.get = lambda url, **kw: dispatch("GET", url, **kw)
requests.Session.request = lambda self, method, url, **kw: dispatch(method, url, **kw)

from app import main as m  # noqa: E402

m.store.set_account("yutong", {"enabled": True, "auth_mode": "api", "app_key": "FAKE-APPKEY", "app_secret": "FAKE-APPSECRET-SENTINEL"})
m.store.set_account("huanyang", {"enabled": True, "auth_mode": "api", "app_key": "FAKE-APPKEY", "app_secret": "FAKE-APPSECRET-SENTINEL"})

import uvicorn  # noqa: E402

uvicorn.run(m.app, host="127.0.0.1", port=port, log_level="warning")
