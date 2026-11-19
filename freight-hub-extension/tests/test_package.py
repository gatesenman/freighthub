"""打包产物测试：解压 dist zip → 加载解压后的扩展 → 查一次；并确认包内没有测试 / 密钥 / 源码以外的东西。"""
import time
import zipfile
from pathlib import Path

import pytest
from playwright.sync_api import expect

from conftest import EXT, EXT_ID

ZIP = EXT / "dist" / "FreightHub-Chrome-1.0.1.zip"
pytestmark = pytest.mark.ext


def test_zip_contents():
    z = zipfile.ZipFile(ZIP)
    names = z.namelist()
    assert all(n.startswith("FreightHub-Chrome-1.0.1/") for n in names)
    for need in ("manifest.json", "background.js", "sidepanel.html", "options.html", "安装说明.md", "js/panel.js", "icons/icon128.png"):
        assert "FreightHub-Chrome-1.0.1/" + need in names, need
    assert not [n for n in names if any(x in n for x in ("tests/", ".pem", "private", "screenshots", "docs/", ".git", "__pycache__", "pytest"))]
    txt = "\n".join(z.read(n).decode("utf-8", "ignore") for n in names if n.endswith((".js", ".html", ".json", ".md")))
    assert "Admin-Pass-123" not in txt and "BEGIN PRIVATE KEY" not in txt


def test_packaged_extension_loads_and_queries(pw, tmp_path, desk):
    out = tmp_path / "x"
    zipfile.ZipFile(ZIP).extractall(out)
    d = out / "FreightHub-Chrome-1.0.1"
    ctx = pw.chromium.launch_persistent_context(str(tmp_path / "p"), headless=True, channel="chromium", viewport={"width": 420, "height": 760},
                                                args=[f"--disable-extensions-except={d}", f"--load-extension={d}", "--headless=new"])
    try:
        sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=20000)
        assert sw.url.split("/")[2] == EXT_ID            # 固定公钥 → 固定 ID
        for _ in range(50):
            if sw.evaluate("async () => (await chrome.declarativeNetRequest.getDynamicRules()).length"):
                break
            time.sleep(0.1)
        sw.evaluate("([u]) => chrome.storage.local.set({backend: 'desktop', desktopUrl: u})", [desk.url])
        pg = ctx.new_page()
        pg.goto(f"chrome-extension://{EXT_ID}/sidepanel.html")
        pg.fill("#qText", "美国 1kg"); pg.press("#qText", "Enter")
        expect(pg.locator("#qbest")).to_contain_text("¥88.60")
        assert pg.locator("#trialBadge").count() == 1 and pg.locator("#trialMark").count() == 1
        m = sw.evaluate("() => chrome.runtime.getManifest()")
        assert m["version"] == "1.0.1" and m["manifest_version"] == 3
    finally:
        ctx.close()
