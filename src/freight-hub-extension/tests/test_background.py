"""后台：右键菜单 / 快捷键 / 点击图标开侧边栏 / Origin 规则。"""
import pytest
from playwright.sync_api import expect

from conftest import wait_ready, SHOTS, open_panel, set_backend

pytestmark = pytest.mark.ext


def test_panel_behavior_open_on_action_click(ext):
    b = ext.sw.evaluate("() => chrome.sidePanel.getPanelBehavior()")
    assert b["openPanelOnActionClick"] is True
    o = ext.sw.evaluate("() => chrome.sidePanel.getOptions({})")
    assert o["path"] == "sidepanel.html"


def test_origin_rule_follows_backend(ext, desk):
    r = ext.sw.evaluate("async () => (await chrome.declarativeNetRequest.getDynamicRules())")
    assert r[0]["action"]["requestHeaders"][0]["value"] == "https://freight.gaosichun.dev"
    assert r[0]["condition"]["initiatorDomains"] == [ext.id]
    assert set(r[0]["condition"]["requestMethods"]) == {"post", "put", "delete", "patch"}
    set_backend(ext, "desktop", desk.url)
    r = ext.sw.evaluate("async () => (await chrome.declarativeNetRequest.getDynamicRules())")
    assert r[0]["action"]["requestHeaders"][0]["value"] == desk.url and len(r) == 1


def test_context_menu_selection_runs_query_in_open_panel(ext, desk):
    set_backend(ext, "desktop", desk.url)
    pg = open_panel(ext)
    expect(pg.locator("#connTxt")).to_contain_text("本机桌面版")
    wait_ready(pg)
    # 与 Chrome 点右键菜单时相同的调用（没有真实用户手势，sidePanel.open 会失败但不应抛错）
    ext.sw.evaluate("() => self.__fh.onMenuClick({menuItemId: 'fh-quote-selection', selectionText: 'US 1kg'}, {id: 1, windowId: 1})")
    expect(pg.locator("#qbest")).to_contain_text("¥88.60")
    assert pg.input_value("#qText") == "US 1kg"
    assert pg.input_value("#inCountry") == "美国"
    # 一次性：已被消费
    assert ext.sw.evaluate("async () => (await chrome.storage.session.get('pendingQuery')).pendingQuery") is None


def test_context_menu_selection_before_panel_opens(ext, desk):
    set_backend(ext, "desktop", desk.url)
    ext.sw.evaluate("() => self.__fh.onMenuClick({menuItemId: 'fh-quote-selection', selectionText: '美国 2kg 30x20x10'}, {id: 1, windowId: 1})")
    pg = open_panel(ext)
    expect(pg.locator("#qbest")).to_contain_text("30×20×10cm")
    expect(pg.locator("#qbest")).to_contain_text("2kg")


def test_context_menu_bad_selection_shows_reason(ext, desk):
    set_backend(ext, "desktop", desk.url)
    pg = open_panel(ext)
    wait_ready(pg)
    ext.sw.evaluate("() => self.__fh.onMenuClick({menuItemId: 'fh-quote-selection', selectionText: '今天天气不错'}, {id: 1, windowId: 1})")
    expect(pg.locator("#qbest")).to_contain_text("没认出目的国")


def test_context_menu_ignores_other_menu_and_empty(ext, desk):
    set_backend(ext, "desktop", desk.url)
    ext.sw.evaluate("() => self.__fh.onMenuClick({menuItemId: 'other', selectionText: 'US 1kg'}, {id: 1, windowId: 1})")
    ext.sw.evaluate("() => self.__fh.onMenuClick({menuItemId: 'fh-quote-selection', selectionText: '   '}, {id: 1, windowId: 1})")
    assert ext.sw.evaluate("async () => (await chrome.storage.session.get('pendingQuery')).pendingQuery") is None


def test_context_menu_item_registered(ext):
    # contextMenus 没有「列出」接口：重复创建同一 id 会报错，说明 onInstalled 已创建
    err = ext.sw.evaluate("""() => new Promise(r => { chrome.contextMenus.create({id: 'fh-quote-selection', title: 'x'}, () => r(chrome.runtime.lastError && chrome.runtime.lastError.message)); })""")
    assert err and "duplicate" in err.lower()


def test_command_handler(ext):
    r = ext.sw.evaluate("async () => await self.__fh.onCommand('open-panel', {id: 1, windowId: 1})")
    assert r in (True, False)       # 无手势时 Chrome 拒绝打开，handler 不抛错
    assert ext.sw.evaluate("async () => await self.__fh.onCommand('nope', {id: 1})") is None
    cmds = ext.sw.evaluate("() => chrome.commands.getAll()")
    c = [x for x in cmds if x["name"] == "open-panel"][0]
    assert "试" not in c["description"] and "侧边栏" in c["description"]


def test_context_menu_selection_waits_for_login_on_web(ext, web):
    from conftest import ADMIN_PW
    set_backend(ext, "web", web.url)
    ext.sw.evaluate("() => self.__fh.onMenuClick({menuItemId: 'fh-quote-selection', selectionText: 'US 1kg'}, {id: 1, windowId: 1})")
    pg = open_panel(ext)
    expect(pg.locator("#vLogin")).to_be_visible()
    pg.fill("#liUser", "admin"); pg.fill("#liPass", ADMIN_PW); pg.click("#liGo")
    expect(pg.locator("#qbest")).to_contain_text("¥88.60")
