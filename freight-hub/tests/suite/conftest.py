"""FreightHub 全量测试套件的公共夹具。

约定：
- 所有测试使用独立的临时数据目录（FREIGHT_HUB_DATA），绝不触碰真实 data/config.json。
- 单元/接口测试一律打桩 HTTP 层（FakeNet），并且拦截 requests 的底层发送——任何漏打桩的真实外网请求都会直接报错。
- 凭据全部是明显的假数据（FAKE-…），任何测试输出里都不会出现真实密钥。
- 带 live 标记的冒烟测试默认不运行，需要 FH_LIVE=1，并且只读、每个平台最多一次登录、失败即停。
"""
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_TMP = tempfile.mkdtemp(prefix="fh_suite_")
os.environ["FREIGHT_HUB_DATA"] = _TMP
os.environ.pop("LOCALAPPDATA", None)
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

from app import main as m  # noqa: E402
from app.adapters.base import AdapterError  # noqa: E402

DATA_DIR = Path(_TMP)

# ---- 假凭据（测试专用，明显不是真值）----
FAKE = {
    "app_key": "FAKE-APPKEY-0001",
    "app_secret": "FAKE-APPSECRET-SENTINEL-9f8e7d6c",
    "aes_key": "A" * 43,                      # 43 位 Base64 → 32 字节全 0x00
    "password": "FAKE-PASSWORD-SENTINEL-p@ss",
    "username": "FAKE-USER",
    "token": "FAKE-TOKEN-SENTINEL-abc123",
}
SENTINELS = [FAKE["app_secret"], FAKE["password"], FAKE["token"], "FAKE-AESKEY-SENTINEL", "FAKE-CHECKWORD-SENTINEL"]


class Resp:
    """requests.Response 的最小替身。"""

    def __init__(self, data=None, status=200, text=None, content=None, headers=None):
        self._d = data
        self.status_code = status
        self.headers = headers or {}
        if text is not None:
            self.text = text
        elif isinstance(data, (dict, list)) or data is None:
            self.text = json.dumps(data, ensure_ascii=False)
        else:
            self.text = str(data)
        self.content = content if content is not None else self.text.encode("utf-8")
        self.encoding = "utf-8"
        self.ok = status < 400

    def json(self):
        if isinstance(self._d, (dict, list)):
            return self._d
        raise ValueError("No JSON object could be decoded")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeNet:
    """按 URL 片段路由的 HTTP 打桩；记录每次请求。route 的响应可以是：Resp / dict(当作 200 JSON) / callable(call)->Resp /
    Exception（抛出）/ list（按调用顺序依次返回，最后一个重复）。"""

    def __init__(self):
        self.calls = []
        self.routes = []
        self.lock = threading.Lock()

    def route(self, frag, resp, method=None):
        self.routes.append({"frag": frag, "resp": resp, "method": method, "n": 0})
        return self

    def reset_routes(self):
        self.routes.clear()

    def _dispatch(self, method, url, kw):
        call = {"method": method.upper(), "url": url, "kw": kw}
        with self.lock:
            self.calls.append(call)
        for rt in self.routes:
            if rt["frag"] in url and (rt["method"] is None or rt["method"].upper() == call["method"]):
                with self.lock:
                    i = rt["n"]
                    rt["n"] += 1
                r = rt["resp"]
                if isinstance(r, list):
                    r = r[min(i, len(r) - 1)]
                if callable(r) and not isinstance(r, (Resp, Exception)):
                    r = r(call)
                if isinstance(r, Exception) or (isinstance(r, type) and issubclass(r, Exception)):
                    raise r
                if isinstance(r, (dict, list)):
                    r = Resp(r)
                return r
        return Resp({"error": "no route", "url": url}, 404)

    # 便捷查询
    def to(self, frag):
        return [c for c in self.calls if frag in c["url"]]

    def count(self, frag):
        return len(self.to(frag))

    def urls(self):
        return [c["url"] for c in self.calls]


@pytest.fixture(scope="session", autouse=True)
def _block_real_network():
    """兜底：任何没有被 FakeNet 接住的真实 HTTP 请求（非本机）直接失败。"""
    if os.environ.get("FH_LIVE") == "1":
        yield
        return
    from requests.adapters import HTTPAdapter
    orig = HTTPAdapter.send

    def guarded(self, request, *a, **kw):
        host = re.sub(r"^https?://", "", request.url).split("/")[0].split(":")[0]
        if host in ("127.0.0.1", "localhost"):
            return orig(self, request, *a, **kw)
        raise requests.ConnectionError(f"测试期间禁止真实外网请求：{request.url[:80]}")

    HTTPAdapter.send = guarded
    yield
    HTTPAdapter.send = orig


@pytest.fixture(autouse=True)
def _reset_state():
    """每个测试前：清空配置、会话、历史、运行状态。"""
    st = m.store
    st._write({"accounts": {}})
    for f in (st.dir / "sessions").glob("*"):
        f.unlink()
    st.runtime.clear()
    (st.dir / "login_lock.json").unlink(missing_ok=True)
    st.delete_history(None)
    from app import importer
    importer._batches.clear()
    for a in m.registry.values():
        a.__dict__.pop("_fh_lock", None)
    yield


@pytest.fixture
def net(monkeypatch):
    n = FakeNet()

    def req(method, url, **kw):
        return n._dispatch(method, url, kw)

    monkeypatch.setattr(requests, "request", req)
    monkeypatch.setattr(requests, "post", lambda url, **kw: n._dispatch("POST", url, kw))
    monkeypatch.setattr(requests, "get", lambda url, **kw: n._dispatch("GET", url, kw))
    monkeypatch.setattr(requests.Session, "request", lambda self, method, url, **kw: n._dispatch(method, url, kw))
    return n


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    return TestClient(m.app)


@pytest.fixture
def store():
    return m.store


@pytest.fixture
def reg():
    return m.registry


def set_acct(pid, **kw):
    kw.setdefault("enabled", True)
    return m.store.set_account(pid, kw)


@pytest.fixture
def setacct():
    return set_acct


def ois_ok_routes(net, quote_items=None, products=None, auth=None):
    """OIS 三个接口的成功响应。"""
    net.route("/ois/order/getAuth", auth or {"body": {"ack": "true", "token": "FAKE-TOKEN-SENTINEL-abc123", "clno": "C1", "coid": "CO1"}})
    net.route("/ois/order/getProductList", {"result_code": 0, "body": products if products is not None else [{"code": "P1", "hubType": "专线"}]})
    net.route("/tms/expose/queryQuoteList", {"result_code": 0, "body": quote_items if quote_items is not None else [OIS_ITEM]})
    return net


OIS_ITEM = {"code": "P1", "name": "美国专线小包", "standardFeeSum": 123.45, "standardCharge": 100, "fuelCharge": 10.5,
            "otherStandardFeeSum": 12.95, "standardCur": "CNY", "aging": "5-8", "zoneName": "一区", "formulaView": "首重+续重",
            "remark": " 备注 ", "otherStandardFeeList": [{"chargestandard": 3, "ardedName": "偏远费"}], "divsize": 5000}


def json_body_of(call):
    d = call["kw"].get("json")
    if d is not None:
        return d
    data = call["kw"].get("data")
    if isinstance(data, (bytes, str)):
        return json.loads(data)
    return data
