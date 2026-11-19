"""快速询价浮窗（/quick 页面）与工具栏输入框、试用版水印的 Playwright UI 测试（打桩平台，Linux 可跑）。
说明：真实的全局热键 / 托盘 / WebView2 窗口定位只能在 Windows 上验证，这里验证页面本身与和后端的交互；
桌面外壳（pywebview）用 window.pywebview 的假对象代替。"""
import json
import time

import pytest

from tests.suite.test_ui_e2e import SHOTS, Stub, open_app, sync_playwright, expect  # noqa: F401  (复用桩服务与浏览器)

pytestmark = pytest.mark.ui

POP = {"width": 460, "height": 560}
SHELL_FAKE = """
window.__calls = [];
window.pywebview = {api: {
  close_popup(){ window.__calls.push(['close']); return Promise.resolve(); },
  open_main(id){ window.__calls.push(['open_main', id]); return Promise.resolve(); },
  quick_popup(t){ window.__calls.push(['quick_popup', t]); return Promise.resolve(); }}};
"""


@pytest.fixture(scope="module")
def desk(tmp_path_factory):
    s = Stub("desktop", tmp_path_factory.mktemp("fh_q_desktop"))
    yield s
    s.stop()


@pytest.fixture(scope="module")
def rich(tmp_path_factory):
    s = Stub("desktop", tmp_path_factory.mktemp("fh_q_rich"), env={"FH_STUB_RICH": "1"})
    yield s
    s.stop()


@pytest.fixture(scope="module")
def trial(tmp_path_factory):
    s = Stub("desktop", tmp_path_factory.mktemp("fh_q_trial"), env={"FH_STUB_RICH": "1", "FREIGHT_HUB_TRIAL": "1"})
    yield s
    s.stop()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def ctx(browser):
    c = browser.new_context(viewport=POP)
    yield c
    c.close()


def popup(ctx, url, query="", shell=True):
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    if shell:
        pg.add_init_script(SHELL_FAKE)
    pg.goto(url + "/quick" + query)
    pg.wait_for_function("window.__quickReady === true", timeout=10000)
    return pg


def fill_and_go(pg, country="美国", weight="2", dims=None):
    pg.fill("#inCountry", country)
    pg.fill("#inWeight", weight)
    if dims:
        pg.fill("#inL", str(dims[0])); pg.fill("#inW", str(dims[1])); pg.fill("#inH", str(dims[2]))
    pg.press("#inWeight", "Enter")


def settle(pg):
    pg.wait_for_function("!document.querySelector('#qlist .it.loading') && document.querySelectorAll('#qlist .it').length > 0", timeout=10000)


def test_popup_initial_state_and_no_errors(desk, ctx):
    pg = popup(ctx, desk.url)
    assert "快速询价" in pg.title()
    assert "按 Enter 查询" in pg.inner_text("#qbest")
    assert "将同时查询" in pg.inner_text("#qlist")
    assert pg.evaluate("document.activeElement.id") == "inCountry"          # 没有记忆时焦点在国家框
    assert pg.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert pg.errors == []


def test_enter_queries_every_platform_and_highlights_overall_lowest(desk, ctx):
    pg = popup(ctx, desk.url)
    fill_and_go(pg)
    settle(pg)
    items = {i["id"]: i for i in pg.eval_on_selector_all("#qlist .it", "e => e.map(x => ({id:x.dataset.id, cls:x.className, text:x.innerText.replace(/\\s+/g,' ')}))")}
    assert {"yutong", "huanyang"} <= set(items)
    assert "ok" in items["yutong"]["cls"] and "min" in items["yutong"]["cls"]
    assert "¥99.90" in items["yutong"]["text"] and "美国经济专线" in items["yutong"]["text"] and "8-12" in items["yutong"]["text"] and "全场最低" in items["yutong"]["text"]
    assert "min" not in items["huanyang"]["cls"].split() and "err" in items["huanyang"]["cls"].split()       # 部分失败不影响其它平台
    assert items["huanyang"]["text"].strip().endswith("-") and ("登录" in items["huanyang"]["text"] or "失败" in items["huanyang"]["text"] or "appKey" in items["huanyang"]["text"])
    best = pg.inner_text("#qbest")
    assert "全部最低" in best and "¥99.90" in best and "雨桐" in best
    assert pg.is_enabled("#bGo")
    pg.screenshot(path=str(SHOTS / "quick-popup-partial-failure-460x560.png"))
    assert pg.errors == []


def test_each_platform_has_its_own_loading_state(desk, ctx):
    pg = popup(ctx, desk.url)

    pg.evaluate("""(() => { const f = window.fetch; window.fetch = (u, o) => (String(u).includes('/api/quote/quick') && !String(u).includes('commit') && String((o||{}).body).includes('"yutong"'))
        ? new Promise(r => setTimeout(r, 1500)).then(() => f(u, o)) : f(u, o); })()""")
    fill_and_go(pg)
    # 环洋先出结果（失败），雨桐还在加载
    pg.wait_for_function("document.querySelector('#qlist .it[data-id=huanyang]') && document.querySelector('#qlist .it[data-id=huanyang]').classList.contains('err')", timeout=8000)
    assert "loading" in pg.get_attribute("#qlist .it[data-id=yutong]", "class")
    assert pg.is_visible("#qlist .it[data-id=yutong] .spin")
    assert pg.is_disabled("#bGo")
    pg.screenshot(path=str(SHOTS / "quick-popup-loading-460x560.png"))
    settle(pg)
    assert "min" in pg.get_attribute("#qlist .it[data-id=yutong]", "class") and pg.is_enabled("#bGo")


def test_all_platforms_ok_overall_lowest_is_the_cheapest(rich, ctx):
    pg = popup(ctx, rich.url)
    fill_and_go(pg, dims=(30, 20, 10))
    settle(pg)
    assert "环洋" in pg.inner_text("#qbest") and "¥88.60" in pg.inner_text("#qbest")
    assert pg.locator("#qlist .it.min").count() == 1 and pg.get_attribute("#qlist .it.min", "data-id") == "huanyang"
    assert pg.locator("#qlist .it").first.get_attribute("data-id") == "huanyang"       # 按价格排序，最低的在最上面
    pg.screenshot(path=str(SHOTS / "quick-popup-all-ok-460x560.png"))


@pytest.mark.parametrize("country,weight,expect", [("", "2", "请填写目的国"), ("美国", "", "重量"), ("美国", "0", "重量"), ("火星", "1", "火星")])
def test_validation_messages(desk, ctx, country, weight, expect):
    pg = popup(ctx, desk.url)
    fill_and_go(pg, country, weight)
    pg.wait_for_function(f"document.querySelector('#qbest').textContent.includes({json.dumps(expect)})", timeout=8000)
    assert "fail" in pg.get_attribute("#qbest", "class")
    assert pg.is_enabled("#bGo")


def test_text_param_runs_immediately_and_fills_fields(desk, ctx):
    pg = popup(ctx, desk.url, "?text=" + "美国 2kg 30x20x10")
    settle(pg)
    assert pg.input_value("#inCountry").startswith("美国") and pg.input_value("#inWeight") == "2"
    assert (pg.input_value("#inL"), pg.input_value("#inW"), pg.input_value("#inH")) == ("30", "20", "10")
    assert "¥99.90" in pg.inner_text("#qbest")


def test_unparseable_text_param_shows_reason(desk, ctx):
    pg = popup(ctx, desk.url, "?text=" + "blah")
    pg.wait_for_function("document.querySelector('#qbest').classList.contains('fail')", timeout=8000)
    assert pg.inner_text("#qbest").strip()


def test_last_input_is_remembered_after_reopen(desk, ctx):
    pg = popup(ctx, desk.url)
    fill_and_go(pg, "德国", "3.5", (10, 11, 12))
    settle(pg)
    pg.wait_for_timeout(300)
    pg2 = popup(ctx, desk.url)
    assert pg2.input_value("#inCountry").startswith("德国") and pg2.input_value("#inWeight") == "3.5" and pg2.input_value("#inL") == "10"
    assert pg2.evaluate("document.activeElement.id") == "inWeight"              # 有记忆时焦点直接在重量上，改一下就能查
    # 桌面外壳每次唤出时调用 __quickShow：复位结果、保留输入
    pg2.evaluate("window.__quickShow({})")
    assert "按 Enter 查询" in pg2.inner_text("#qbest") and pg2.input_value("#inWeight") == "3.5"


def test_esc_and_close_button_call_shell_close(desk, ctx):
    pg = popup(ctx, desk.url)
    pg.keyboard.press("Escape")
    assert pg.evaluate("window.__calls") == [["close"]]
    pg.click("#qx")
    assert pg.evaluate("window.__calls") == [["close"], ["close"]]


def test_esc_without_shell_marks_closed(desk, ctx):
    pg = popup(ctx, desk.url, shell=False)
    pg.keyboard.press("Escape")
    assert pg.evaluate("window.__closed") is True


def test_autoclose_on_blur_is_optional(desk, ctx):
    pg = popup(ctx, desk.url)
    pg.evaluate("document.hasFocus = () => false")
    assert pg.is_checked("#cbAuto")
    pg.evaluate("window.dispatchEvent(new Event('blur'))")
    pg.wait_for_function("window.__calls.length >= 1", timeout=3000)
    pg.uncheck("#cbAuto")
    pg.wait_for_timeout(300)
    assert json.loads(__import__("requests").get(desk.url + "/api/shell/settings").text)["popup_autoclose"] is False
    n = len(pg.evaluate("window.__calls"))
    pg.evaluate("window.dispatchEvent(new Event('blur'))")
    pg.wait_for_timeout(600)
    assert len(pg.evaluate("window.__calls")) == n                              # 关掉选项后失去焦点不再关闭
    pg.check("#cbAuto")
    pg.wait_for_timeout(300)


def test_blur_then_refocus_cancels_close(desk, ctx):
    pg = popup(ctx, desk.url)
    pg.evaluate("document.hasFocus = () => false")
    pg.evaluate("window.dispatchEvent(new Event('blur')); window.dispatchEvent(new Event('focus'))")
    pg.wait_for_timeout(500)
    assert pg.evaluate("window.__calls") == []


def test_click_row_commits_history_and_asks_shell_to_open_main(desk, ctx):
    pg = popup(ctx, desk.url)
    fill_and_go(pg)
    settle(pg)
    pg.click("#qlist .it.ok >> nth=0")
    pg.wait_for_function("window.__calls.some(c => c[0] === 'open_main')", timeout=5000)
    hid = [c for c in pg.evaluate("window.__calls") if c[0] == "open_main"][0][1]
    assert isinstance(hid, int)
    hist = __import__("requests").get(desk.url + "/api/history/%d" % hid).json()
    assert hist["rows"] and all(r["platform"] == "yutong" for r in hist["rows"])
    assert [r["total"] for r in hist["rows"]].count(99.9) == 1
    # 失败的行不可点击
    pg.click("#qlist .it.err")
    pg.wait_for_timeout(300)
    assert len([c for c in pg.evaluate("window.__calls") if c[0] == "open_main"]) == 1


def test_click_row_in_browser_opens_main_with_full_results(desk, ctx):
    pg = popup(ctx, desk.url, shell=False)
    fill_and_go(pg)
    settle(pg)
    with ctx.expect_page() as np_:
        pg.click("#qlist .it.ok >> nth=0")
    main = np_.value
    main.wait_for_selector("#boot", state="detached", timeout=10000)
    main.wait_for_selector(".panel:visible .gridscroll tbody tr", timeout=10000)
    t = main.inner_text(".panel:visible .gridscroll")
    assert "美国经济专线" in t and "美国专线小包" in t and "美国快递" in t                  # 主窗口里是全部渠道，不只是最低价


# ---------------- 主窗口：工具栏输入框 / 菜单 / 设置 ----------------
def test_toolbar_quick_box_enter_opens_popup_with_query(desk, browser):
    c = browser.new_context(viewport={"width": 1280, "height": 800})
    pg = c.new_page()
    open_app(pg, desk.url)
    assert pg.is_visible("#qqIn")
    pg.fill("#qqIn", "美国 2kg 30x20x10")
    with c.expect_page() as np_:
        pg.press("#qqIn", "Enter")
    pop = np_.value
    pop.wait_for_function("window.__quickReady === true")
    assert "/quick?text=" in pop.url
    settle(pop)
    assert "¥99.90" in pop.inner_text("#qbest") and pop.input_value("#inL") == "30"
    c.close()


def test_toolbar_quick_box_uses_desktop_shell_when_present(desk, browser):
    c = browser.new_context(viewport={"width": 1280, "height": 800})
    pg = c.new_page()
    pg.add_init_script(SHELL_FAKE)
    open_app(pg, desk.url)
    pg.fill("#qqIn", "日本 1kg")
    pg.click("#qqGo")
    assert pg.evaluate("window.__calls") == [["quick_popup", "日本 1kg"]]
    c.close()


def test_menu_has_quick_item_with_hotkey_and_settings_dialog(desk, browser):
    c = browser.new_context(viewport={"width": 1280, "height": 800})
    pg = c.new_page()
    open_app(pg, desk.url)
    pg.click('#menubar .mb-item[data-m="查询"]')
    assert "快速询价浮窗" in pg.inner_text("#ctxmenu") and "Ctrl+Alt+Q" in pg.inner_text("#ctxmenu")
    pg.keyboard.press("Escape")
    pg.click('#menubar .mb-item[data-m="工具"]')
    pg.click("#ctxmenu .mi:has-text('快捷键与浮窗设置')")
    pg.wait_for_selector("#skKey")
    assert pg.input_value("#skKey") == "Ctrl+Alt+Q"
    pg.focus("#skKey")
    pg.keyboard.press("Control+Shift+KeyK")
    assert pg.input_value("#skKey") == "Ctrl+Shift+K"
    pg.keyboard.press("Control+Alt+Slash")                                      # 不支持的键：给出提示、保持原值
    assert "不能用作快捷键" in pg.inner_text("#skSt") and pg.input_value("#skKey") == "Ctrl+Shift+K"
    pg.click("#skOk")
    pg.wait_for_function("document.querySelector('#skSt').textContent.trim().length > 0")
    pg.screenshot(path=str(SHOTS / "quick-settings-dialog-1280x800.png"))
    saved = __import__("requests").get(desk.url + "/api/shell/settings").json()
    assert saved["hotkey"] == "Ctrl+Shift+K"
    pg.click("#skCancel")
    __import__("requests").put(desk.url + "/api/shell/settings", json={"hotkey": "Ctrl+Alt+Q"})
    c.close()


def test_env_radio_switch_keeps_both_credential_sets(desk, browser):
    """每个开放平台有 正式 / 测试 环境单选；切换不丢另一套凭据，未保存的草稿也保留。"""
    c = browser.new_context(viewport={"width": 1280, "height": 800})
    pg = c.new_page()
    open_app(pg, desk.url)
    pg.click('#menubar .mb-item[data-m="平台"]')
    pg.click("#ctxmenu .mi:has-text('平台账号管理')")
    pg.wait_for_selector("text=燕文", timeout=8000)
    pg.click(".panel:visible .gridscroll tbody tr:has-text('燕文')")
    pg.wait_for_selector(".panel:visible .aEnv", timeout=8000)
    labels = pg.eval_on_selector_all(".panel:visible .aEnv", "xs => xs.map(x => x.parentElement.innerText.trim())")
    assert any("正式环境" in x for x in labels) and any("测试环境" in x for x in labels)
    pg.check('.panel:visible .aEnv[value="sandbox"]')
    pg.fill('.panel:visible .acc-form [data-k="user_id"]', "100000")
    pg.fill('.panel:visible .acc-form [data-k="api_token"]', "TEST-TOKEN-1")
    pg.check('.panel:visible .aEnv[value="prod"]')
    assert pg.input_value('.panel:visible .acc-form [data-k="user_id"]') == ""
    pg.fill('.panel:visible .acc-form [data-k="user_id"]', "PROD-ID")
    pg.check('.panel:visible .aEnv[value="sandbox"]')
    assert pg.input_value('.panel:visible .acc-form [data-k="user_id"]') == "100000"
    pg.screenshot(path=str(SHOTS / "env-radio-account-form-1280x800.png"))
    c.close()


# ---------------- 试用版水印 ----------------
def test_normal_edition_has_no_watermark(desk, ctx):
    pg = popup(ctx, desk.url)
    assert pg.locator("#trialMark").count() == 0 and pg.locator("#trialBadge").count() == 0
    pg2 = ctx.new_page()
    open_app(pg2, desk.url)
    assert pg2.locator("#trialMark").count() == 0 and "试用" not in pg2.title()


def test_trial_edition_main_window_watermark_badge_and_passthrough(trial, browser):
    c = browser.new_context(viewport={"width": 1280, "height": 800})
    pg = c.new_page()
    open_app(pg, trial.url)
    assert pg.locator("#trialMark").count() == 1 and pg.locator("#trialBadge").count() == 1
    assert pg.inner_text("#trialBadge") == "未授权版本 / 试用" and pg.is_visible("#trialBadge")
    st = pg.evaluate("(() => { const s = getComputedStyle(document.getElementById('trialMark')); return [s.position, s.pointerEvents, s.backgroundRepeat, s.backgroundImage.includes('svg')]; })()")
    assert st == ["fixed", "none", "repeat", True]
    box = pg.eval_on_selector("#trialMark", "e => { const r = e.getBoundingClientRect(); return [r.width, r.height]; }")
    assert box == [1280, 800]                                                  # 整页铺满
    b = pg.eval_on_selector("#trialBadge", "e => { const r = e.getBoundingClientRect(); return [r.top, r.height]; }")
    assert b[0] < 40                                                           # 在标题栏区域
    assert "试用" in pg.title()
    # 水印不拦截点击
    pg.click('#menubar .mb-item[data-m="文件"]')
    assert pg.is_visible("#ctxmenu")
    pg.keyboard.press("Escape")
    pg.screenshot(path=str(SHOTS / "trial-main-1280x800.png"))
    c.close()


def test_trial_watermark_on_popup_and_survives_results(trial, ctx):
    pg = popup(ctx, trial.url)
    assert pg.locator("#trialMark").count() == 1 and pg.is_visible("#trialBadge")
    fill_and_go(pg)
    settle(pg)
    assert pg.locator("#trialMark").count() == 1
    pg.screenshot(path=str(SHOTS / "trial-quick-popup-460x560.png"))
    pg.click("#qlist .it.ok >> nth=0")                                         # 点击穿透水印
    pg.wait_for_function("window.__calls.some(c => c[0] === 'open_main')")


def test_trial_flag_reported_by_meta(trial, desk):
    import requests
    assert requests.get(trial.url + "/api/meta").json()["trial"] is True
    assert requests.get(desk.url + "/api/meta").json()["trial"] is False
