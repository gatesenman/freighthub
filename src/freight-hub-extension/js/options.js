(function () {
  "use strict";
  const $ = id => document.getElementById(id);
  const say = (t, bad) => { $("msg").textContent = t; $("msg").className = "msg" + (bad ? " bad" : ""); };

  async function load() {
    const s = await FH.getSettings();
    $(s.backend === "desktop" ? "rbDesk" : "rbWeb").checked = true;
    $("webUrl").value = s.webUrl; $("desktopUrl").value = s.desktopUrl;
    $("eid").textContent = chrome.runtime.id;
    const cmds = await chrome.commands.getAll();
    const c = cmds.find(x => x.name === "open-panel");
    $("sc").textContent = c && c.shortcut ? c.shortcut : "未设置";
  }
  function form() {
    const backend = $("rbDesk").checked ? "desktop" : "web";
    const webUrl = FH.normUrl($("webUrl").value), desktopUrl = FH.normUrl($("desktopUrl").value);
    if (!webUrl) throw new Error("网页版网址不正确");
    if (!desktopUrl) throw new Error("桌面版地址不正确");
    return { backend, webUrl, desktopUrl };
  }
  // 申请访问所选后端站点的权限（内置地址已在清单里；自定义地址需要用户点一下授权）
  async function ensurePerm(url) {
    const origin = new URL(url).origin + "/*";
    if (await chrome.permissions.contains({ origins: [origin] })) return true;
    return chrome.permissions.request({ origins: [origin] });
  }
  $("bSave").addEventListener("click", async () => {
    try {
      const f = form();
      const target = f.backend === "desktop" ? f.desktopUrl : f.webUrl;
      if (!(await ensurePerm(target))) throw new Error("没有授予访问 " + target + " 的权限，设置未保存");
      await FH.saveSettings(f);
      await chrome.runtime.sendMessage({ type: "apply-origin-rule" });
      $("webUrl").value = f.webUrl; $("desktopUrl").value = f.desktopUrl;
      say("已保存，侧边栏会自动切换到所选后端");
    } catch (e) { say(e.message, true); }
  });
  $("bTest").addEventListener("click", async () => {
    const out = $("testOut"); out.hidden = false; out.textContent = "检测中…";
    try {
      const f = form();
      const target = f.backend === "desktop" ? f.desktopUrl : f.webUrl;
      if (!(await ensurePerm(target))) throw new Error("没有授予访问 " + target + " 的权限");
      const c = new FH.Client(Object.assign({}, f));
      const lines = ["后端：" + target];
      const p = await c.get("/api/ping"); lines.push("① 连通：" + (p && p.ok ? "正常" : "异常"));
      try {
        const m = await c.get("/api/meta");
        lines.push("② 会话：已登录" + (m.user ? "（" + m.user + "）" : "") + "，版本 " + (m.version || "?"));
        if (m.csrf) {
          c.csrf = m.csrf;
          // 只验证 Origin 校验，不产生任何数据：对 logout 以外的无副作用写接口很少，这里用 /api/auth/logout 会真的登出，所以不测写请求。
          lines.push("③ 写请求 Origin：由内置规则改写为站点自身 Origin（查询时若被拒绝会提示扩展 ID）");
        }
      } catch (e) { lines.push("② 会话：" + (e.kind === "auth" ? "未登录（打开侧边栏登录即可）" : e.message)); }
      out.textContent = lines.join("\n");
    } catch (e) { out.textContent = "失败：" + e.message; }
  });
  $("bShort").addEventListener("click", () => chrome.tabs.create({ url: "chrome://extensions/shortcuts" }));
  FH.mountTrial($("hdBadge"));
  load();
})();
