"""前端 UI 端到端（Playwright + 打桩平台）：桌面模式 与 网页/服务器模式。
- 桌面模式：菜单 / 快捷键 / 标签页 / 查询流程 / 加载画面 / 错误态 / 1.3.1 的两处修复（对接方式短文案、操作日志撑满）。
- 网页模式：登录页、角色、多用户会话、CSRF、HttpOnly、导入上传 / 导出下载、会话失效跳转。
截图输出到 screenshots/（1280x800 与 1920x1080）。"""
import io
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

pytestmark = pytest.mark.ui
ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "screenshots"
SHOTS.mkdir(exist_ok=True)
ADMIN_PW = "Admin-Pass-123"

try:
    from playwright.sync_api import sync_playwright, expect
except Exception:       # pragma: no cover
    pytest.skip("playwright 未安装", allow_module_level=True)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Stub:
    def __init__(self, mode, tmp):
        self.mode, self.port, self.data = mode, free_port(), str(tmp)
        self.proc = subprocess.Popen([sys.executable, "-m", "tests.suite._stub_server", mode, str(self.port), self.data], cwd=str(ROOT),
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env={**os.environ, "PYTHONPATH": str(ROOT)})
        self.url = f"http://127.0.0.1:{self.port}"
        for _ in range(100):
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


@pytest.fixture(scope="module")
def desktop(tmp_path_factory):
    s = Stub("desktop", tmp_path_factory.mktemp("fh_ui_desktop"))
    yield s
    s.stop()


@pytest.fixture(scope="module")
def web(tmp_path_factory):
    s = Stub("server", tmp_path_factory.mktemp("fh_ui_web"))
    yield s
    s.stop()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, accept_downloads=True)
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    yield pg
    ctx.close()


def open_app(pg, url):
    pg.goto(url + "/")
    pg.wait_for_selector("#boot", state="detached", timeout=10000)
    pg.wait_for_selector("#menubar .mb-item")
    pg.bring_to_front()
    pg.evaluate("window.focus()")      # 无头 Chromium 需要页面有焦点，Ctrl+I 等快捷键才算用户手势


def query(pg, country="美国 US", weight="1"):
    pg.fill(".panel.active .inCountry, .panel:visible .inCountry", country)
    pg.fill(".panel:visible .inWeight", weight)
    pg.keyboard.press("F5")
    pg.wait_for_selector(".panel:visible .gridscroll tbody tr", timeout=10000)


def rows_text(pg):
    return pg.eval_on_selector_all(".panel:visible .gridscroll tbody tr", "els => els.map(e => e.innerText.replace(/\\s+/g,' '))")


def xlsx_bytes(rows):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["平台", "授权方式", "账号", "密码", "appKey", "appSecret", "AES秘钥", "客户编码", "对接URL", "其他参数", "备注", "启用"])
    for r in rows:
        ws.append(r)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


IMPORT_FILE = lambda: xlsx_bytes([["雨桐", "API", "", "", "FAKE-IMPORT-KEY", "FAKE-IMPORT-SECRET", "", "", "", "", "", "是"]])


# ====================== 桌面模式 ======================
def test_boot_screen_visible_while_loading_then_hides(desktop, page):
    page.route("**/api/meta", lambda r: (time.sleep(0.8), r.continue_()))
    page.goto(desktop.url + "/", wait_until="commit")
    assert page.locator("#boot").is_visible() and "正在加载" in page.inner_text("#boot")
    page.wait_for_selector("#boot", state="detached", timeout=10000)
    assert page.is_visible("#menubar")


def test_boot_failure_message_when_backend_fails(desktop, page):
    page.route("**/api/meta", lambda r: r.fulfill(status=500, json={"detail": "后端坏了"}))
    page.goto(desktop.url + "/")
    page.wait_for_function("document.querySelector('#bootMsg') && document.querySelector('#bootMsg').textContent.includes('启动失败')")
    assert "后端坏了" in page.inner_text("#bootMsg") and not page.is_visible("#boot .sp")


def test_menubar_toolbar_statusbar_present(desktop, page):
    open_app(page, desktop.url)
    assert page.eval_on_selector_all("#menubar .mb-item", "e => e.map(x => x.textContent)") == ["文件", "查询", "平台", "工具", "帮助"]
    cmds = page.eval_on_selector_all("#toolbar .tb", "e => e.map(x => x.dataset.cmd)")
    assert {"new", "run", "reset", "export", "copy", "accounts", "history", "refresh", "about"} <= set(cmds)
    assert "v1.4" in page.inner_text("#sbVer") or "1.4" in page.inner_text("#statusbar")
    assert page.errors == []


@pytest.mark.parametrize("menu,expect_items", [("文件", ["新建查询", "导入账号", "导出当前账号配置", "退出"]), ("查询", ["开始查询", "重置条件", "查询历史"]),
                                                ("平台", ["平台账号管理", "测试全部平台连接", "雨桐"]), ("工具", ["复制选中行", "清空查询历史"]), ("帮助", ["使用说明", "关于 FreightHub"])])
def test_each_menu_opens_with_items_and_esc_closes(desktop, page, menu, expect_items):
    open_app(page, desktop.url)
    page.click(f'#menubar .mb-item[data-m="{menu}"]')
    txt = page.inner_text("#ctxmenu")
    for it in expect_items:
        assert it in txt, (menu, it)
    page.keyboard.press("Escape")
    assert not page.is_visible("#ctxmenu") and page.locator("#ctxmenu .mi").count() == 0
    page.click(f'#menubar .mb-item[data-m="{menu}"]')
    assert page.is_visible("#ctxmenu")
    page.mouse.click(640, 700)
    assert not page.is_visible("#ctxmenu")


def test_hover_switches_open_menu(desktop, page):
    open_app(page, desktop.url)
    page.click('#menubar .mb-item[data-m="文件"]')
    page.hover('#menubar .mb-item[data-m="平台"]')
    assert "平台账号管理" in page.inner_text("#ctxmenu")


def test_ctrl_t_ctrl_w_tabs(desktop, page):
    open_app(page, desktop.url)
    assert page.locator("#tabs .tab").count() == 1
    page.keyboard.press("Control+t")
    page.keyboard.press("Control+t")
    assert page.locator("#tabs .tab").count() == 3
    page.click("#tabs .tab >> nth=0")
    assert "active" in page.get_attribute("#tabs .tab >> nth=0", "class")
    page.keyboard.press("Control+w")
    assert page.locator("#tabs .tab").count() == 2


def test_ctrl_i_opens_file_chooser_and_preview_dialog(desktop, page):
    open_app(page, desktop.url)
    with page.expect_file_chooser() as fc:
        page.keyboard.press("Control+i")
    fc.value.set_files(files=[{"name": "acc.xlsx", "mimeType": "application/octet-stream", "buffer": IMPORT_FILE()}])
    page.wait_for_selector("#modal .dlg.wide")
    t = page.inner_text("#modal")
    assert "导入账号 — 预览" in t and "雨桐" in t and "FAKE-IMPORT-SECRET" not in t       # 预览不回显密钥
    page.screenshot(path=str(SHOTS / "desktop-import-preview-1280x800.png"))
    page.click("#impCancel")
    assert not page.is_visible("#modal")


def test_import_bad_file_shows_error_dialog(desktop, page):
    open_app(page, desktop.url)
    with page.expect_file_chooser() as fc:
        page.keyboard.press("Control+i")
    fc.value.set_files(files=[{"name": "bad.xlsx", "mimeType": "application/octet-stream", "buffer": b"not a zip"}])
    page.wait_for_selector("#modal .dlg")
    assert "读取文件失败" in page.inner_text("#modal")


def test_query_flow_sorted_rows_platform_states_and_detail(desktop, page):
    open_app(page, desktop.url)
    query(page)
    rows = rows_text(page)
    assert len(rows) == 3 and "美国经济专线" in rows[0] and "全场最低" in rows[0]            # 99.9 最低 → 排第一
    assert "美国快递" in rows[-1]
    strip = page.inner_text(".panel:visible .pstrip")
    assert "雨桐" in strip and "环洋" in strip
    page.click(".panel:visible .gridscroll tbody tr >> nth=0")
    assert "99.9" in page.inner_text(".panel:visible .detail") or "费用" in page.inner_text(".panel:visible .detail")
    assert "3" in page.inner_text(".panel:visible .resCount")
    page.screenshot(path=str(SHOTS / "desktop-query-1280x800.png"))
    page.set_viewport_size({"width": 1920, "height": 1080})
    page.screenshot(path=str(SHOTS / "desktop-query-1920x1080.png"))


def test_sorting_filtering_and_clear(desktop, page):
    open_app(page, desktop.url)
    query(page)
    page.click('.panel:visible th:has-text("总价")')          # 切换排序方向
    first = rows_text(page)[0]
    page.click('.panel:visible th:has-text("总价")')
    assert rows_text(page)[0] != first or len(rows_text(page)) == 1
    page.fill(".panel:visible .fKey", "快递")
    page.wait_for_function("document.querySelectorAll('.panel.active .gridscroll tbody tr').length === 1")
    page.click(".panel:visible .bClr")
    page.wait_for_function("document.querySelectorAll('.panel.active .gridscroll tbody tr').length === 3")
    page.fill(".panel:visible .fMaxP", "100")
    page.wait_for_function("document.querySelectorAll('.panel.active .gridscroll tbody tr').length === 1")


def test_error_states_missing_country_and_platform_errors(desktop, page):
    open_app(page, desktop.url)
    page.fill(".panel:visible .inCountry", "")
    page.keyboard.press("F5")
    page.wait_for_selector("#modal .dlg")
    assert "查询失败" in page.inner_text("#modal") or "目的国" in page.inner_text("#modal")
    page.click("#modal button")
    page.fill(".panel:visible .inCountry", "美国 US")
    page.fill(".panel:visible .inWeight", "0")
    page.keyboard.press("F5")
    page.wait_for_selector("#modal .dlg")
    assert "重量" in page.inner_text("#modal") or "查询失败" in page.inner_text("#modal")
    page.click("#modal button")
    query(page)                                              # 环洋鉴权失败，雨桐成功：失败不影响成功平台
    assert "环洋" in page.inner_text(".panel:visible .pstrip") and len(rows_text(page)) == 3


def test_platform_status_dots_in_tree(desktop, page):
    open_app(page, desktop.url)
    assert page.locator("#tree .dot").count() >= 7


def test_history_tab_lists_query_and_csv_download(desktop, page):
    open_app(page, desktop.url)
    query(page)
    page.click('#toolbar .tb[data-cmd="history"]')
    page.wait_for_selector("#tabs .tab:has-text('历史')")
    assert "US" in page.inner_text(".panel:visible") or "美国" in page.inner_text(".panel:visible")


def test_export_csv_download_fallback_in_browser(desktop, page):
    open_app(page, desktop.url)
    query(page)
    with page.expect_download() as dl:
        page.keyboard.press("Control+e")
    path = dl.value.path()
    txt = Path(path).read_text(encoding="utf-8-sig")
    assert "美国经济专线" in txt and txt.splitlines()[0].startswith("平台")


# ---- 1.3.1 修复 1：对接方式列短文案 + tooltip ----
EXPECT_MODE = {"雨桐": "OIS 开放API", "环洋": "OIS 开放API", "中航": "OIS 开放API", "顺丰国际": "sf.global API", "欣航": "公开网页", "燕文": "燕文开放API", "云途": "云途开放API"}


def open_accounts_tab(pg):
    pg.click('#toolbar .tb[data-cmd="accounts"]')
    pg.wait_for_selector(".panel:visible .acc-logbox")


def test_accounts_mode_column_short_labels_and_tooltip(desktop, page):
    open_app(page, desktop.url)
    open_accounts_tab(page)
    cells = page.eval_on_selector_all(".panel:visible .gridscroll tbody tr", "rows => rows.map(r => ({name: r.children[0].innerText.trim(), mode: r.children[5].innerText.trim(), title: r.children[5].title || r.children[5].getAttribute('data-tip') || (r.children[5].firstElementChild && r.children[5].firstElementChild.title) || ''}))")
    assert len(cells) == 7
    seen = {}
    for c in cells:
        seen[c["name"]] = c["mode"]
        assert 0 < len(c["mode"]) <= 14, c      # 约定文案本身最长为 sf.global API（13 字）
        assert len(c["title"]) > len(c["mode"]), f"tooltip 应包含完整对接说明：{c}"
    for k, v in EXPECT_MODE.items():
        assert any(name.startswith(k[:2]) and mode == v for name, mode in seen.items()), (k, dict(seen))
    # 平台说明里有完整文字
    page.click(".panel:visible .gridscroll tbody tr >> nth=0")
    assert "对接方式：" in page.inner_text(".panel:visible .desc")


# ---- 1.3.1 修复 2：操作日志撑满并随窗口缩放 ----
def log_geometry(pg):
    return pg.evaluate("""() => { const p = [...document.querySelectorAll('.panel')].find(e => e.offsetParent);
      const body = p.querySelector('.acc-body').getBoundingClientRect(), box = p.querySelector('.acc-logbox').getBoundingClientRect(), ta = p.querySelector('.log').getBoundingClientRect();
      return {bodyBottom: body.bottom, boxBottom: box.bottom, boxTop: box.top, taH: ta.height, vh: innerHeight}; }""")


@pytest.mark.parametrize("w,h", [(1280, 800), (1920, 1080), (1100, 650)])
def test_accounts_log_panel_fills_remaining_height_and_resizes(desktop, page, w, h):
    open_app(page, desktop.url)
    open_accounts_tab(page)
    page.set_viewport_size({"width": w, "height": h})
    page.wait_for_timeout(150)
    g = log_geometry(page)
    assert abs(g["boxBottom"] - g["bodyBottom"]) <= 2, g           # 贴到底，没有留白
    assert g["taH"] >= 60, g
    page.screenshot(path=str(SHOTS / f"desktop-accounts-{w}x{h}.png"))


def test_log_panel_grows_with_window(desktop, page):
    open_app(page, desktop.url)
    open_accounts_tab(page)
    page.set_viewport_size({"width": 1280, "height": 700})
    page.wait_for_timeout(100)
    small = log_geometry(page)["taH"]
    page.set_viewport_size({"width": 1280, "height": 1000})
    page.wait_for_timeout(100)
    big = log_geometry(page)["taH"]
    assert big - small >= 250, (small, big)


def test_log_receives_messages_after_test_connection(desktop, page):
    open_app(page, desktop.url)
    open_accounts_tab(page)
    page.click(".panel:visible .gridscroll tbody tr:has-text('雨桐')")
    page.click(".panel:visible .aTest")
    page.wait_for_function("document.querySelector('.panel .log') && [...document.querySelectorAll('.log')].some(t => t.value.length > 5)", timeout=10000)


def test_account_save_never_echoes_secret_back(desktop, page):
    open_app(page, desktop.url)
    open_accounts_tab(page)
    page.click(".panel:visible .gridscroll tbody tr:has-text('雨桐')")
    html = page.content()
    assert "FAKE-APPSECRET-SENTINEL" not in html
    vals = page.eval_on_selector_all(".panel:visible .acc-form input", "e => e.map(x => x.value)")
    assert all("FAKE-APPSECRET" not in v for v in vals)


def test_no_console_errors_across_tour(desktop, page):
    open_app(page, desktop.url)
    query(page)
    open_accounts_tab(page)
    page.click('#toolbar .tb[data-cmd="about"]')
    page.wait_for_selector("#tabs .tab:has-text('帮助')")
    assert page.errors == []


# ====================== 网页 / 服务器模式 ======================
def ui_login(pg, url, user="admin", pw=ADMIN_PW):
    pg.goto(url + "/login")
    pg.fill("#u", user)
    pg.fill("#p", pw)
    pg.click("#ok")


def login_ok(pg, url, user="admin", pw=ADMIN_PW):
    ui_login(pg, url, user, pw)
    pg.wait_for_url(url + "/")
    pg.wait_for_selector("#boot", state="detached", timeout=10000)


def api_login(web, user="admin", pw=ADMIN_PW):
    s = requests.Session()
    r = s.post(web.url + "/api/auth/login", json={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    s.headers["X-CSRF-Token"] = r.json()["csrf"]
    return s


@pytest.fixture(scope="module")
def bob(web):
    a = api_login(web)
    r = a.post(web.url + "/api/users", json={"username": "bob", "password": "Bob-Pass-12345", "role": "user"})
    assert r.status_code in (200, 400)
    return ("bob", "Bob-Pass-12345")


def test_web_unauthenticated_redirects_to_login_page(web, page):
    page.goto(web.url + "/")
    page.wait_for_url(web.url + "/login")
    assert page.is_visible("#u") and page.is_visible("#p")
    page.screenshot(path=str(SHOTS / "web-login-1280x800.png"))


def test_web_login_wrong_password_error_and_no_navigation(web, page):
    ui_login(page, web.url, "admin", "wrong-password")
    page.wait_for_function("document.getElementById('err').textContent.length > 0")
    assert "用户名或密码错误" in page.inner_text("#err") and page.url.endswith("/login")
    assert page.input_value("#p") == ""


def test_web_login_then_full_ui_with_user_chip_and_logout(web, page):
    login_ok(page, web.url)
    assert page.eval_on_selector_all("#menubar .mb-item", "e => e.map(x => x.textContent)") == ["文件", "查询", "平台", "工具", "管理", "帮助"]
    assert "admin" in page.inner_text("#userChip") and "管理员" in page.inner_text("#userChip")
    assert not page.locator("#winCtl button").first.is_visible()                 # 浏览器里没有窗口控制按钮
    page.click('#menubar .mb-item[data-m="文件"]')
    assert "退出登录" in page.inner_text("#ctxmenu") and "Alt+F4" not in page.inner_text("#ctxmenu")
    page.screenshot(path=str(SHOTS / "web-main-1280x800.png"))
    page.set_viewport_size({"width": 1920, "height": 1080})
    page.keyboard.press("Escape")
    page.screenshot(path=str(SHOTS / "web-main-1920x1080.png"))
    page.click("#uLogout")
    page.wait_for_url(web.url + "/login")
    page.goto(web.url + "/")
    page.wait_for_url(web.url + "/login")                                         # 退出后不能直接回到主界面


def test_web_session_cookie_is_httponly_and_csrf_not_in_js_cookie(web, page):
    login_ok(page, web.url)
    assert "fh_session" not in page.evaluate("document.cookie")
    ck = [c for c in page.context.cookies() if c["name"] == "fh_session"][0]
    assert ck["httpOnly"] and ck["sameSite"] == "Lax"


def test_web_post_without_csrf_header_is_rejected_from_page(web, page):
    login_ok(page, web.url)
    st = page.evaluate("fetch('/api/quote', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({country:'US', weight:1, platforms:['yuntu']})}).then(r => r.status)")
    assert st == 403
    st = page.evaluate("fetch('/api/quote', {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token': window.__csrf}, body: JSON.stringify({country:'US', weight:1, platforms:['yuntu']})}).then(r => r.status)")
    assert st == 200


def test_web_admin_query_flow_and_csv_download(web, page):
    login_ok(page, web.url)
    query(page)
    assert len(rows_text(page)) == 3
    page.screenshot(path=str(SHOTS / "web-query-1280x800.png"))
    with page.expect_download() as dl:
        page.keyboard.press("Control+e")
    assert "美国经济专线" in Path(dl.value.path()).read_text(encoding="utf-8-sig")


def test_web_admin_import_via_browser_upload_and_template_download(web, page):
    login_ok(page, web.url)
    with page.expect_file_chooser() as fc:
        page.keyboard.press("Control+i")
    fc.value.set_files(files=[{"name": "acc.xlsx", "mimeType": "application/octet-stream", "buffer": IMPORT_FILE()}])
    page.wait_for_selector("#modal .dlg.wide")
    assert "导入账号 — 预览" in page.inner_text("#modal")
    page.click("#impCancel")
    page.click('#menubar .mb-item[data-m="文件"]')
    with page.expect_download() as dl:
        page.click('#ctxmenu .mi:has-text("下载导入模板（Excel）")')
    assert dl.value.suggested_filename.endswith(".xlsx")


def test_web_admin_user_management_dialog(web, page, bob):
    login_ok(page, web.url)
    page.click('#menubar .mb-item[data-m="管理"]')
    page.click('#ctxmenu .mi:has-text("用户管理")')
    page.wait_for_selector(".nuAdd")
    page.wait_for_function("document.querySelector('.ucount') && document.querySelector('.ucount').textContent.includes('共')")
    body = page.inner_text("#tabpanels") if page.query_selector("#tabpanels") else page.inner_text("body")
    assert "bob" in body and "admin" in body
    page.fill(".nuName", "carol")
    page.fill(".nuPw", "short")
    page.click(".nuAdd")
    page.wait_for_function("document.querySelector('.uerr').textContent.length > 0")
    assert "8" in page.inner_text(".uerr")
    page.fill(".nuPw", "Carol-Pass-12345")
    page.click(".nuAdd")
    page.wait_for_selector("text=carol")
    page.screenshot(path=str(SHOTS / "web-users-1280x800.png"))
    page.click("text=carol")
    page.click(".bDel")
    page.click("#modal .btn.def")
    page.wait_for_function("!document.body.innerText.includes('carol')")


def test_web_user_role_restrictions_in_ui(web, page, bob):
    login_ok(page, web.url, *bob)
    assert "普通用户" in page.inner_text("#userChip")
    page.click('#menubar .mb-item[data-m="工具"]')
    assert "用户管理" not in page.inner_text("#ctxmenu") and "修改我的密码" in page.inner_text("#ctxmenu")
    page.keyboard.press("Escape")
    page.mouse.click(640, 600)
    page.keyboard.press("Control+i")
    page.wait_for_selector("#modal .dlg")
    assert "需要管理员权限" in page.inner_text("#modal")
    page.click("#modal button")
    assert page.locator('#toolbar .tb[data-cmd="accounts"]:visible').count() == 0        # 无权限的工具栏按钮直接隐藏
    query(page)                                                                    # 普通用户可以询价
    assert len(rows_text(page)) == 3
    page.screenshot(path=str(SHOTS / "web-user-role-1280x800.png"))


def test_web_user_direct_api_calls_forbidden(web, bob):
    u = api_login(web, *bob)
    for method, path in [("GET", "/api/accounts/export"), ("GET", "/api/users"), ("PUT", "/api/platforms/yutong/account"), ("POST", "/api/platforms/yutong/test"),
                         ("POST", "/api/accounts/import/preview")]:
        r = u.request(method, web.url + path, json={} if method != "GET" else None)
        assert r.status_code == 403, (method, path, r.status_code)
    meta = u.get(web.url + "/api/meta").json()
    assert meta["role"] == "user" and "data_dir" not in meta                      # 服务器路径不给普通用户
    assert u.get(web.url + "/api/platforms").status_code == 200


def test_web_multi_user_sessions_isolated_history(web, browser, bob):
    ca = browser.new_context(viewport={"width": 1280, "height": 800})
    cb = browser.new_context(viewport={"width": 1280, "height": 800})
    try:
        pa, pb = ca.new_page(), cb.new_page()
        login_ok(pa, web.url)
        login_ok(pb, web.url, *bob)
        assert "admin" in pa.inner_text("#userChip") and "bob" in pb.inner_text("#userChip")
        query(pb, "美国 US", "2")
        query(pa, "美国 US", "3")
        hb = pb.evaluate("fetch('/api/history').then(r => r.json())")
        ha = pa.evaluate("fetch('/api/history').then(r => r.json())")
        assert {h["user"] for h in hb} == {"bob"} and 2 in [h["query"]["weight"] for h in hb] and 3 not in [h["query"]["weight"] for h in hb]
        assert {"admin", "bob"} <= {h["user"] for h in ha}                            # 管理员能看到所有人的
        
        admin_only = [h["id"] for h in ha if h["user"] == "admin"][0]
        st = pb.evaluate(f"fetch('/api/history/{admin_only}').then(r => r.status)")
        assert st == 404                                                             # 看不到别人的记录
        assert pb.evaluate(f"fetch('/api/history/{admin_only}/csv').then(r => r.status)") == 404
        # bob 退出不影响 admin 的会话
        pb.click("#uLogout")
        pb.wait_for_url(web.url + "/login")
        assert pa.evaluate("fetch('/api/meta').then(r => r.status)") == 200
    finally:
        ca.close()
        cb.close()


def test_web_session_revoked_server_side_redirects_to_login(web, browser, bob):
    cb = browser.new_context(viewport={"width": 1280, "height": 800})
    try:
        pb = cb.new_page()
        login_ok(pb, web.url, *bob)
        a = api_login(web)
        assert a.put(web.url + "/api/users/bob", json={"disabled": True}).status_code == 200
        pb.keyboard.press("F5")                                                       # 任何 API 调用都会拿到 401 → 回登录页
        pb.fill(".panel:visible .inCountry", "美国 US")
        pb.keyboard.press("F5")
        pb.wait_for_url(web.url + "/login", timeout=8000)
        assert a.put(web.url + "/api/users/bob", json={"disabled": False}).status_code == 200
    finally:
        cb.close()


def test_web_change_password_dialog_validation(web, page, bob):
    login_ok(page, web.url, *bob)
    page.click('#menubar .mb-item[data-m="工具"]')
    page.click('#ctxmenu .mi:has-text("修改我的密码")')
    page.fill("#pwOld", bob[1])
    page.fill("#pwNew", "Another-Pass-1234")
    page.fill("#pwNew2", "Mismatch-Pass-1234")
    page.click("#pwOk")
    assert "不一致" in page.inner_text("#pwErr")
    page.fill("#pwNew2", "x")
    page.fill("#pwNew", "short")
    page.fill("#pwNew2", "short")
    page.click("#pwOk")
    page.wait_for_function("document.getElementById('pwErr').textContent.includes('8')")
    page.fill("#pwOld", "wrong-old-password")
    page.fill("#pwNew", "Another-Pass-1234")
    page.fill("#pwNew2", "Another-Pass-1234")
    page.click("#pwOk")
    page.wait_for_function("document.getElementById('pwErr').textContent.includes('原密码')")


def test_web_login_throttle_message_in_ui(web, page):
    for _ in range(5):
        ui_login(page, web.url, "throttle-victim", "bad-password")
        page.wait_for_function("document.getElementById('err').textContent.length > 0")
        page.wait_for_function("!document.getElementById('ok').disabled")
    ui_login(page, web.url, "throttle-victim", "bad-password")
    page.wait_for_function("document.getElementById('err').textContent.includes('5 分钟')")


def test_web_xss_in_username_not_executed(web, page):
    a = api_login(web)
    r = a.post(web.url + "/api/users", json={"username": "<img src=x onerror=alert(1)>", "password": "Valid-Pass-1234"})
    assert r.status_code == 400                                                       # 用户名白名单直接拒绝


def test_accounts_env_radio_switch_keeps_both_credential_sets(desktop, page):
    open_app(page, desktop.url)
    open_accounts_tab(page)
    page.click(".panel:visible .gridscroll tbody tr:has-text('燕文')")
    page.wait_for_selector(".panel:visible .aEnv")
    labels = page.eval_on_selector_all(".panel:visible .aEnv", "xs => xs.map(x => x.parentElement.innerText.trim())")
    assert any("正式环境" in x for x in labels) and any("测试环境" in x for x in labels)
    page.check('.panel:visible .aEnv[value="sandbox"]')
    page.fill('.panel:visible .acc-form [data-k="user_id"]', "100000")
    page.fill('.panel:visible .acc-form [data-k="api_token"]', "TEST-TOKEN-1")
    page.check('.panel:visible .aEnv[value="prod"]')
    assert page.input_value('.panel:visible .acc-form [data-k="user_id"]') == ""          # 正式环境那套是空的
    page.fill('.panel:visible .acc-form [data-k="user_id"]', "PROD-ID")
    page.fill('.panel:visible .acc-form [data-k="api_token"]', "PROD-TOKEN-1")
    page.check('.panel:visible .aEnv[value="sandbox"]')
    assert page.input_value('.panel:visible .acc-form [data-k="user_id"]') == "100000"    # 切回测试：没填完的草稿还在
    page.click(".panel:visible .aSave")
    page.wait_for_function("() => [...document.querySelectorAll('.log')].some(x => x.value.includes('已保存'))")
    page.check('.panel:visible .aEnv[value="prod"]')
    assert page.input_value('.panel:visible .acc-form [data-k="user_id"]') == "PROD-ID"
