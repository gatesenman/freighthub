// 后台（MV3 service worker）：右键菜单、快捷键、点击图标打开侧边栏、Origin 兼容规则。
importScripts("js/settings.js");

const MENU_ID = "fh-quote-selection";
const RULE_ID = 1;

// ---------- 侧边栏 ----------
chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({ id: MENU_ID, title: "用 FreightHub 询价「%s」", contexts: ["selection"] });
  });
  applyOriginRule();
});
chrome.runtime.onStartup.addListener(() => { chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {}); applyOriginRule(); });

async function openPanel(target) {
  try {
    if (target && target.tabId != null) await chrome.sidePanel.open({ tabId: target.tabId });
    else if (target && target.windowId != null) await chrome.sidePanel.open({ windowId: target.windowId });
    else { const w = await chrome.windows.getCurrent(); await chrome.sidePanel.open({ windowId: w.id }); }
    return true;
  } catch (e) {
    return false;       // 没有用户手势 / 窗口不存在
  }
}

// 右键菜单：选中的文字（如「US 1kg」）交给侧边栏查询。必须在用户手势的同一调用栈里 open，所以先 open 再写存储。
async function onMenuClick(info, tab) {
  if (!info || info.menuItemId !== MENU_ID) return;
  const text = String(info.selectionText || "").trim().slice(0, 200);
  if (!text) return;
  const opened = openPanel({ tabId: tab && tab.id, windowId: tab && tab.windowId });
  await chrome.storage.session.set({ pendingQuery: { text, ts: Date.now() } });
  return opened;
}
chrome.contextMenus.onClicked.addListener(onMenuClick);

// 快捷键（默认 Alt+Shift+F，可在 chrome://extensions/shortcuts 修改）：打开侧边栏
async function onCommand(cmd, tab) {
  if (cmd !== "open-panel") return;
  return openPanel({ tabId: tab && tab.id, windowId: tab && tab.windowId });
}
chrome.commands.onCommand.addListener(onCommand);

// ---------- Origin 兼容 ----------
// 网页版对 POST/PUT/DELETE 校验 Origin 必须等于站点自己的域名（防 CSRF）。扩展发出的请求 Origin 是 chrome-extension://<id>，
// 会被 403。这里只对「本扩展发往所选后端」的写请求把 Origin 改写成后端自己的 origin（等同于同站请求）；
// 如果服务端已把扩展 ID 加进 FREIGHT_HUB_ALLOWED_ORIGINS，这条规则也无害。
async function applyOriginRule() {
  try {
    const s = await FH.getSettings();
    const u = new URL(FH.baseOf(s));
    await chrome.declarativeNetRequest.updateDynamicRules({
      removeRuleIds: [RULE_ID],
      addRules: [{
        id: RULE_ID, priority: 1,
        action: { type: "modifyHeaders", requestHeaders: [{ header: "origin", operation: "set", value: u.origin }] },
        condition: { requestDomains: [u.hostname], initiatorDomains: [chrome.runtime.id], resourceTypes: ["xmlhttprequest"], requestMethods: ["post", "put", "delete", "patch"] },
      }],
    });
  } catch (e) { console.warn("FreightHub: Origin 规则设置失败", e); }
}
chrome.storage.onChanged.addListener((ch, area) => { if (area === "local" && (ch.backend || ch.webUrl || ch.desktopUrl)) applyOriginRule(); });
chrome.runtime.onMessage.addListener((msg, _s, send) => {
  if (msg && msg.type === "apply-origin-rule") { applyOriginRule().then(() => send({ ok: true })); return true; }
  if (msg && msg.type === "open-panel") { openPanel(msg.target).then(ok => send({ ok })); return true; }
});

// 供自动化测试直接调用（真实环境里由 Chrome 事件触发）
self.__fh = { onMenuClick, onCommand, applyOriginRule, MENU_ID };
