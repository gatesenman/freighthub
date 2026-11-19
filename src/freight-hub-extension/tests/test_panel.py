"""侧边栏 UI 测试：本机桌面版后端 + 网页版（登录 / CSRF / Origin）后端。"""
import json
import re

import pytest
from playwright.sync_api import expect

from conftest import wait_ready, ADMIN_PW, EXT_ID, SHOTS, open_panel, set_backend

pytestmark = pytest.mark.ext


def ask(pg, text):
    pg.fill("#qText", text)
    pg.press("#qText", "Enter")


# ---------------------------------------------------------------- 本机桌面版后端
def test_desktop_quick_query_overall_and_per_platform(ext, desk):
    set_backend(ext, "desktop", desk.url)
    pg = open_panel(ext)
    expect(pg.locator("#connTxt")).to_contain_text("本机桌面版")
    expect(pg.locator("#qbest")).to_contain_text("输入目的国和重量")
    ask(pg, "美国 1kg")
    expect(pg.locator("#qbest")).to_contain_text("全部最低")
    expect(pg.locator("#qbest")).to_contain_text("¥88.60")
    expect(pg.locator("#qbest")).to_contain_text("环洋")
    # 输入框被解析回填
    assert pg.input_value("#inCountry") == "美国" and pg.input_value("#inWeight") == "1"
    items = pg.locator("#qlist .it")
    assert items.count() >= 3                      # 两个成功平台 + 失败 / 超时平台
    ok = pg.locator("#qlist .it.ok")
    assert ok.count() == 2
    assert pg.locator("#qlist .it.min").count() == 1
    expect(pg.locator("#qlist .it.min")).to_contain_text("全场最低")
    expect(pg.locator("#qlist .it.min .pr")).to_have_text("¥88.60")
    # 每个平台的最低价：雨桐 P2 = 99.90
    expect(pg.locator("#qlist .it.ok").nth(1)).to_contain_text("¥99.90")
    # 失败平台给出原因而不是空白
    assert pg.locator("#qlist .it.err").count() >= 1
    pg.screenshot(path=str(SHOTS / "panel-quick-desktop-420x760.png"))
    # 全部结果
    pg.click('#tabs button[data-tab="all"]')
    expect(pg.locator("#cntAll")).to_have_text("5")
    assert pg.locator("#grid tbody tr.r").count() == 5
    assert pg.locator("#grid tbody tr.r.low").count() == 1
    pg.click("#grid tbody tr.r >> nth=0")
    expect(pg.locator("tr.det")).to_be_visible()
    expect(pg.locator("tr.det")).to_contain_text("时效")
    pg.screenshot(path=str(SHOTS / "panel-all-results-420x760.png"))


def test_results_sort_filter_and_platform_click(ext, desk):
    set_backend(ext, "desktop", desk.url)
    pg = open_panel(ext)
    ask(pg, "US 1kg")
    expect(pg.locator("#qbest")).to_contain_text("¥88.60")
    pg.click("#qlist .it.ok >> nth=1")               # 点平台行 → 只看该平台
    expect(pg.locator('#pAll')).to_be_visible()
    rows = pg.locator("#grid tbody tr.r")
    assert rows.count() == 3
    pg.select_option("#fPlat", "")
    pg.fill("#fKey", "快递")
    assert pg.locator("#grid tbody tr.r").count() == 1
    pg.fill("#fKey", "")
    pg.click('#grid th[data-k="total"]')              # 当前升序 → 降序
    prices = [float(x.replace(",", "").replace("¥", "")) for x in pg.locator("#grid tbody tr.r td.num:nth-child(3)").all_inner_texts()]
    assert prices == sorted(prices, reverse=True) and len(prices) == 5
    pg.click('#grid th[data-k="total"]')
    prices = [float(x.replace(",", "").replace("¥", "")) for x in pg.locator("#grid tbody tr.r td.num:nth-child(3)").all_inner_texts()]
    assert prices == sorted(prices)


def test_fields_query_and_validation(ext, desk):
    set_backend(ext, "desktop", desk.url)
    pg = open_panel(ext)
    pg.click("#bGo")
    expect(pg.locator("#qbest")).to_contain_text("没认出目的国")
    pg.fill("#inCountry", "美国")
    pg.click("#bGo")
    expect(pg.locator("#qbest")).to_contain_text("大于 0 的重量")
    pg.fill("#inWeight", "2")
    pg.fill("#inL", "30"); pg.fill("#inW", "20"); pg.fill("#inH", "10")
    pg.click("#bGo")
    expect(pg.locator("#qbest")).to_contain_text("30×20×10cm")
    ask(pg, "火星 1kg")
    expect(pg.locator("#qbest")).to_contain_text("没认出目的国")
    pg.fill("#qText", "US -3")
    pg.press("#qText", "Enter")
    expect(pg.locator("#qbest")).to_contain_text("负数")


def test_loading_state_shown_then_result(ext, desk):
    set_backend(ext, "desktop", desk.url)
    pg = open_panel(ext)
    wait_ready(pg, platforms=True)
    pg.evaluate("""() => { const f = window.fetch; window.fetch = (u, o) => String(u).endsWith('/api/quote') ? new Promise(r => setTimeout(() => r(f(u, o)), 700)) : f(u, o); }""")
    ask(pg, "US 1kg")
    expect(pg.locator("#qbest")).to_contain_text("正在查询")
    expect(pg.locator("#qlist .it.loading").first).to_be_visible()
    pg.screenshot(path=str(SHOTS / "panel-loading-420x760.png"))
    expect(pg.locator("#qbest")).to_contain_text("全部最低")
    assert pg.locator("#qlist .it.loading").count() == 0


def test_history_tab_and_reload(ext, desk):
    set_backend(ext, "desktop", desk.url)
    pg = open_panel(ext)
    ask(pg, "US 1kg")
    expect(pg.locator("#qbest")).to_contain_text("全部最低")
    ask(pg, "美国 2kg")
    expect(pg.locator("#qbest")).to_contain_text("2kg")
    pg.click('#tabs button[data-tab="hist"]')
    expect(pg.locator("#histNote")).to_contain_text("最近")
    assert pg.locator("#histList .h").count() >= 2
    expect(pg.locator("#histList .h").first).to_contain_text("2kg")
    pg.screenshot(path=str(SHOTS / "panel-history-420x760.png"))
    pg.click("#histList .h >> nth=1")                 # 载入较早那条（1kg）
    expect(pg.locator("#qbest")).to_contain_text("1kg")
    assert pg.input_value("#inWeight") == "1"
    expect(pg.locator("#pQuick")).to_be_visible()


def test_desktop_backend_down_shows_friendly_error(ext):
    set_backend(ext, "desktop", "http://127.0.0.1:9")
    pg = open_panel(ext)
    expect(pg.locator("#fatal")).to_contain_text("连不上本机桌面版")
    pg.screenshot(path=str(SHOTS / "panel-backend-down-420x760.png"))
    expect(pg.locator("#connDot")).to_have_class(re.compile("bad"))


def test_last_input_remembered(ext, desk):
    set_backend(ext, "desktop", desk.url)
    pg = open_panel(ext)
    ask(pg, "US 1kg")
    expect(pg.locator("#qbest")).to_contain_text("全部最低")
    pg.close()
    pg2 = open_panel(ext)
    expect(pg2.locator("#qText")).to_have_value(re.compile(r"1kg"))


# ---------------------------------------------------------------- 试用水印（v1.0.1 常开）
def test_trial_watermark_always_on(ext, desk):
    set_backend(ext, "desktop", desk.url)
    for page in ("sidepanel.html", "options.html"):
        pg = ext.ctx.new_page()
        pg.goto(ext.url(page))
        mark = pg.locator("#trialMark")
        assert mark.count() == 1
        st = pg.evaluate("() => { const e = document.getElementById('trialMark'); const s = getComputedStyle(e); return [s.pointerEvents, s.position, s.zIndex, s.backgroundImage.slice(0, 40)]; }")
        assert st[0] == "none" and st[1] == "fixed" and int(st[2]) >= 2147483000 and st[3].startswith("url(")
        assert "未授权版本 / 试用" in pg.locator("#trialBadge").inner_text()
        # 水印不拦截点击：中心点命中的不是水印
        assert pg.evaluate("() => document.elementFromPoint(innerWidth/2, innerHeight/2).id !== 'trialMark'")
    # 没有任何关闭水印的设置项
    pg = ext.ctx.new_page(); pg.goto(ext.url("options.html"))
    assert "水印" not in pg.locator("fieldset").all_inner_texts().__str__().replace("带水印", "")
    # 登录页、错误页、查询后同样存在
    pg = open_panel(ext)
    ask(pg, "US 1kg")
    expect(pg.locator("#qbest")).to_contain_text("全部最低")
    assert pg.locator("#trialMark").count() == 1 and pg.locator("#hdBadge #trialBadge").count() == 1
    pg.screenshot(path=str(SHOTS / "panel-trial-watermark-420x760.png"))


# ---------------------------------------------------------------- 网页版后端：登录 / CSRF / Origin
def test_web_login_flow_query_and_logout(ext, web):
    set_backend(ext, "web", web.url)
    pg = open_panel(ext)
    expect(pg.locator("#vLogin")).to_be_visible()
    expect(pg.locator("#vMain")).to_be_hidden()
    expect(pg.locator("#connTxt")).to_contain_text("未登录")
    pg.screenshot(path=str(SHOTS / "panel-login-420x760.png"))
    pg.fill("#liUser", "admin"); pg.fill("#liPass", "wrong-password")
    pg.click("#liGo")
    expect(pg.locator("#liMsg")).to_contain_text("用户名或密码错误")
    pg.fill("#liPass", ADMIN_PW)
    pg.click("#liGo")
    expect(pg.locator("#vMain")).to_be_visible()
    expect(pg.locator("#connTxt")).to_contain_text("admin")
    assert pg.input_value("#liPass") == ""          # 密码输入框用完即清
    ask(pg, "US 1kg")
    expect(pg.locator("#qbest")).to_contain_text("¥88.60")
    pg.screenshot(path=str(SHOTS / "panel-quick-web-420x760.png"))
    # 会话沿用：新开一个侧边栏页面不需要再登录
    pg2 = open_panel(ext)
    expect(pg2.locator("#vMain")).to_be_visible()
    # 密码 / 令牌不落盘
    dump = json.dumps(ext.sw.evaluate("async () => ({l: await chrome.storage.local.get(null), s: await chrome.storage.session.get(null)})"))
    assert ADMIN_PW not in dump and "csrf" not in dump.lower()
    # 退出登录
    pg2.on("dialog", lambda d: d.accept())
    pg2.click("#btnUser")
    expect(pg2.locator("#vLogin")).to_be_visible()
    expect(pg2.locator("#vMain")).to_be_hidden()


def test_web_post_without_origin_rule_is_403_and_message_names_extension_id(ext, web):
    set_backend(ext, "web", web.url)
    pg = open_panel(ext)
    expect(pg.locator("#vLogin")).to_be_visible()
    rules = ext.sw.evaluate("async () => (await chrome.declarativeNetRequest.getDynamicRules()).map(r => ({id: r.id, v: r.action.requestHeaders[0].value, d: r.condition.requestDomains}))")
    assert rules and rules[0]["v"] == web.url and rules[0]["d"] == ["127.0.0.1"]
    ext.sw.evaluate("() => chrome.declarativeNetRequest.updateDynamicRules({removeRuleIds: [1]})")
    r = pg.evaluate("""async () => { const c = new FH.Client(await FH.getSettings());
        try { await c.post('/api/auth/login', {username: 'admin', password: 'x'}); return {}; } catch (e) { return {status: e.status, kind: e.kind, msg: e.message}; } }""")
    assert r["status"] == 403 and r["kind"] == "origin" and EXT_ID in r["msg"] and "FREIGHT_HUB_ALLOWED_ORIGINS" in r["msg"]
    # 重新应用规则后恢复正常（登录失败是 401，而不是 403）
    ext.sw.evaluate("() => self.__fh.applyOriginRule()")
    r = pg.evaluate("""async () => { const c = new FH.Client(await FH.getSettings());
        try { await c.post('/api/auth/login', {username: 'admin', password: 'x'}); return {}; } catch (e) { return {status: e.status, kind: e.kind}; } }""")
    assert r["status"] == 401


def test_web_server_allows_extension_id_without_rewrite(ext, web_allowed):
    """补丁说明里的推荐做法：服务端 FREIGHT_HUB_ALLOWED_ORIGINS=<扩展 ID>，即使没有 Origin 改写规则也能登录。"""
    set_backend(ext, "web", web_allowed.url)
    pg = open_panel(ext)
    expect(pg.locator("#vLogin")).to_be_visible()
    ext.sw.evaluate("() => chrome.declarativeNetRequest.updateDynamicRules({removeRuleIds: [1]})")
    r = pg.evaluate("""async () => { const c = new FH.Client(await FH.getSettings());
        try { const d = await c.post('/api/auth/login', {username: 'admin', password: '%s'}); return {ok: !!d.csrf}; } catch (e) { return {status: e.status, msg: e.message}; } }""" % ADMIN_PW)
    assert r == {"ok": True}, r


def test_web_csrf_required_for_writes(ext, web):
    set_backend(ext, "web", web.url)
    pg = open_panel(ext)
    pg.fill("#liUser", "admin"); pg.fill("#liPass", ADMIN_PW); pg.click("#liGo")
    expect(pg.locator("#vMain")).to_be_visible()
    r = pg.evaluate("""async () => { const c = new FH.Client(await FH.getSettings());   // 新客户端没有 CSRF 令牌
        try { await c.post('/api/quote', {country: 'US', weight: 1}); return {}; } catch (e) { return {status: e.status, msg: e.message}; } }""")
    assert r["status"] == 403 and "CSRF" in r["msg"]


# ---------------------------------------------------------------- 选项页
def test_options_switch_backend_and_panel_follows(ext, desk, web):
    set_backend(ext, "web", web.url)
    panel = open_panel(ext)
    expect(panel.locator("#vLogin")).to_be_visible()
    op = ext.ctx.new_page()
    op.goto(ext.url("options.html"))
    expect(op.locator("#rbWeb")).to_be_checked()
    expect(op.locator("#eid")).to_have_text(EXT_ID)
    op.check("#rbDesk")
    op.fill("#desktopUrl", desk.url + "/")
    op.click("#bSave")
    expect(op.locator("#msg")).to_contain_text("已保存")
    s = ext.sw.evaluate("() => chrome.storage.local.get(['backend', 'desktopUrl'])")
    assert s == {"backend": "desktop", "desktopUrl": desk.url}
    expect(panel.locator("#vMain")).to_be_visible()
    expect(panel.locator("#connTxt")).to_contain_text("本机桌面版")
    op.set_viewport_size({"width": 1280, "height": 800})
    op.screenshot(path=str(SHOTS / "options-1280x800.png"))
    op.click("#bTest")
    expect(op.locator("#testOut")).to_contain_text("连通：正常")
    op.fill("#desktopUrl", "not a url !!")
    op.click("#bSave")
    expect(op.locator("#msg")).to_contain_text("不正确")


def test_options_defaults(ext):
    op = ext.ctx.new_page()
    op.goto(ext.url("options.html"))
    expect(op.locator("#rbWeb")).to_be_checked()
    assert op.input_value("#webUrl") == "https://freight.gaosichun.dev"
    assert op.input_value("#desktopUrl") == "http://127.0.0.1:8765"
