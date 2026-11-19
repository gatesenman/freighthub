"""Chrome 扩展测试夹具：用桌面版仓库里的「桩服务」（真实 FastAPI 应用 + 打桩的平台 HTTP，没有任何真实外网请求）
当后端，用 Playwright 的持久化上下文加载未打包扩展。"""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests
from playwright.sync_api import sync_playwright

EXT = Path(__file__).resolve().parent.parent
FH_ROOT = Path(os.environ.get("FH_DESKTOP_ROOT", "/workspace/freight-hub"))
SHOTS = EXT / "screenshots"
SHOTS.mkdir(exist_ok=True)
EXT_ID = (Path("/workspace/freight-hub-extension-key/id.txt").read_text().strip()
          if Path("/workspace/freight-hub-extension-key/id.txt").exists() else None)
ADMIN_PW = "Admin-Pass-123"      # 桩服务里的测试管理员密码（假数据，不是真实账号）


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Stub:
    def __init__(self, mode, tmp, env=None):
        self.port, self.data = free_port(), str(tmp)
        self.proc = subprocess.Popen([sys.executable, "-m", "tests.suite._stub_server", mode, str(self.port), self.data], cwd=str(FH_ROOT),
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env={**os.environ, "PYTHONPATH": str(FH_ROOT), **(env or {})})
        self.url = f"http://127.0.0.1:{self.port}"
        for _ in range(150):
            try:
                requests.get(self.url + "/api/ping", timeout=1)
                return
            except Exception:
                if self.proc.poll() is not None:
                    raise RuntimeError("桩服务启动失败：" + self.proc.stdout.read().decode(errors="replace")[-800:])
                time.sleep(0.2)
        raise RuntimeError("桩服务启动超时")

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except Exception:
            self.proc.kill()


@pytest.fixture(scope="session")
def desk(tmp_path_factory):
    s = Stub("desktop", tmp_path_factory.mktemp("ext_desk"), {"FH_STUB_RICH": "1"})
    yield s
    s.stop()


@pytest.fixture(scope="session")
def web(tmp_path_factory):
    s = Stub("server", tmp_path_factory.mktemp("ext_web"), {"FH_STUB_RICH": "1"})
    yield s
    s.stop()


@pytest.fixture(scope="session")
def web_allowed(tmp_path_factory):
    """服务端已把扩展 ID 加进 FREIGHT_HUB_ALLOWED_ORIGINS 的情形（补丁说明里的推荐配置）。"""
    s = Stub("server", tmp_path_factory.mktemp("ext_web2"), {"FH_STUB_RICH": "1", "FREIGHT_HUB_ALLOWED_ORIGINS": EXT_ID})
    yield s
    s.stop()


@pytest.fixture(scope="session")
def pw():
    with sync_playwright() as p:
        yield p


@pytest.fixture
def browser_ctx(pw, tmp_path):
    ctx = pw.chromium.launch_persistent_context(
        str(tmp_path / "profile"), headless=True, channel="chromium", viewport={"width": 420, "height": 760},
        args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}", "--headless=new"])
    yield ctx
    ctx.close()


@pytest.fixture
def ext(browser_ctx):
    sw = browser_ctx.service_workers[0] if browser_ctx.service_workers else browser_ctx.wait_for_event("serviceworker", timeout=20000)
    eid = sw.url.split("/")[2]
    for _ in range(50):      # 等 onInstalled 跑完（建右键菜单、写 Origin 规则）
        if sw.evaluate("async () => (await chrome.declarativeNetRequest.getDynamicRules()).length") >= 1:
            break
        time.sleep(0.1)
    return type("E", (), {"ctx": browser_ctx, "sw": sw, "id": eid, "url": lambda self, p: f"chrome-extension://{eid}/{p}"})()


def set_backend(ext, backend, url):
    """直接写扩展存储（等同于在选项页里保存），再让后台刷新 Origin 规则。"""
    key = "desktopUrl" if backend == "desktop" else "webUrl"
    ext.sw.evaluate("([b,k,u]) => chrome.storage.local.set({backend:b,[k]:u})", [backend, key, url])
    ext.sw.evaluate("() => self.__fh.applyOriginRule()")


def open_panel(ext, vw=None):
    pg = ext.ctx.new_page()
    if vw:
        pg.set_viewport_size(vw)
    pg.goto(ext.url("sidepanel.html"))
    return pg


@pytest.fixture
def ext_page(ext):
    pg = ext.ctx.new_page()
    pg.goto(ext.url("options.html"))     # 只为拿到加载了 FH 脚本的扩展页面；options 不依赖 parse.js，所以再注入
    pg.add_script_tag(url=ext.url("js/parse.js"))
    return pg


def wait_ready(pg, platforms=False, timeout=15):
    """等侧边栏完成初始化（页面 CSP 禁止 eval，所以不用 wait_for_function(字符串)）。"""
    end = time.time() + timeout
    while time.time() < end:
        if pg.evaluate("(p) => !!(window.__fh && __fh.S.meta && (!p || __fh.S.platforms.length > 0))", platforms):
            return
        time.sleep(0.1)
    raise AssertionError("侧边栏初始化超时")
