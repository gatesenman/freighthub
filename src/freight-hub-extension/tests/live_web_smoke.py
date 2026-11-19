"""真实部署站点的冒烟（手动运行，不在默认测试里）：扩展 → https 站点：登录一次 + 查询一次（只读）。
账号从 FH_DEMO_FILE（含「用户名：」「密码：」两行）读取，不打印、不落盘。"""
import os
import re
import sys
import tempfile
import time

from playwright.sync_api import sync_playwright

EXT = "/workspace/freight-hub-extension"
raw = open(os.environ["FH_DEMO_FILE"], encoding="utf-8").read()
user = re.search(r"用户名[:：]\s*(\S+)", raw).group(1)
pw_ = re.search(r"密码[:：]\s*(\S+)", raw).group(1)
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(tempfile.mkdtemp(), headless=True, channel="chromium", viewport={"width": 420, "height": 760},
                                               args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}", "--headless=new"])
    sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker")
    eid = sw.url.split("/")[2]
    time.sleep(1)
    pg = ctx.new_page()
    pg.goto(f"chrome-extension://{eid}/sidepanel.html")
    pg.wait_for_selector("#vLogin:not([hidden])", timeout=15000)
    print("login form shown; conn:", pg.inner_text("#connTxt"))
    pg.fill("#liUser", user); pg.fill("#liPass", pw_)
    pg.click("#liGo")
    try:
        pg.wait_for_selector("#vMain:not([hidden])", timeout=20000)
    except Exception:
        print("LOGIN FAILED:", pg.inner_text("#liMsg"))
        sys.exit(1)
    print("logged in; conn:", pg.inner_text("#connTxt"))
    print("cookies for site:", [(c["name"], c["httpOnly"], c["secure"], c["sameSite"]) for c in ctx.cookies("https://freight.gaosichun.dev")])
    pg.fill("#qText", "US 1kg"); pg.press("#qText", "Enter")
    pg.wait_for_function("() => document.getElementById('qbest').className.indexOf('wait') < 0", timeout=90000)
    print("qbest:", pg.inner_text("#qbest").replace("\n", " | "))
    print("per-platform:", [t.replace("\n", " ")[:160] for t in pg.locator("#qlist .it").all_inner_texts()]); print("more:", pg.inner_text("#qmore")[:300])
    print("cnt all:", pg.inner_text("#cntAll"))
    pg.screenshot(path=f"{EXT}/screenshots/panel-live-web-420x760.png")
    pg.click('#tabs button[data-tab="hist"]'); time.sleep(1.5)
    print("history note:", pg.inner_text("#histNote"))
    ctx.close()
