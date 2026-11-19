// 设置与后端访问（侧边栏 / 选项页 / 后台共用）。不保存任何账号密码：登录态只靠站点下发的会话 Cookie（HttpOnly，扩展读不到）。
(function (g) {
  "use strict";
  const DEFAULTS = {
    backend: "web",                                         // web = 网页版；desktop = 本机桌面版
    webUrl: "https://freight.gaosichun.dev",
    desktopUrl: "http://127.0.0.1:8765",
  };
  const KEYS = Object.keys(DEFAULTS);

  function normUrl(u) {
    u = String(u || "").trim();
    if (!u || /\s/.test(u)) return "";
    if (!/^[a-z]+:\/\//i.test(u)) u = (/^(127\.|localhost|\[::1\])/i.test(u) ? "http://" : "https://") + u;
    let x;
    try { x = new URL(u); } catch (e) { return ""; }
    if (!/^https?:$/.test(x.protocol) || !/^[a-z0-9.\-\[\]:]+$/i.test(x.hostname)) return "";
    return x.origin + x.pathname.replace(/\/+$/, "");
  }
  async function getSettings() {
    const d = await chrome.storage.local.get(KEYS);
    const s = Object.assign({}, DEFAULTS, d);
    s.webUrl = normUrl(s.webUrl) || DEFAULTS.webUrl;
    s.desktopUrl = normUrl(s.desktopUrl) || DEFAULTS.desktopUrl;
    if (s.backend !== "desktop") s.backend = "web";
    return s;
  }
  async function saveSettings(p) {
    const o = {};
    KEYS.forEach(k => { if (k in p) o[k] = p[k]; });
    await chrome.storage.local.set(o);
    return getSettings();
  }
  const baseOf = s => (s.backend === "desktop" ? s.desktopUrl : s.webUrl);

  class ApiError extends Error {
    constructor(msg, status, kind) { super(msg); this.status = status || 0; this.kind = kind || ""; }
  }
  // 一个后端会话：带 Cookie 的 fetch + CSRF 令牌。
  class Client {
    constructor(settings) { this.s = settings; this.base = baseOf(settings); this.csrf = ""; }
    async call(path, opt) {
      opt = opt || {};
      const method = (opt.method || "GET").toUpperCase();
      const headers = { Accept: "application/json" };
      let body;
      if (opt.body !== undefined) { body = JSON.stringify(opt.body); headers["Content-Type"] = "application/json"; }
      if (method !== "GET" && this.csrf) headers["X-CSRF-Token"] = this.csrf;
      let r;
      try {
        r = await fetch(this.base + path, { method, headers, body, credentials: "include", cache: "no-store", redirect: "manual", signal: opt.signal });
      } catch (e) {
        if (e && e.name === "AbortError") throw e;
        throw new ApiError(this.s.backend === "desktop"
          ? "连不上本机桌面版（" + this.base + "）。请先启动 FreightHub 桌面版，或在选项里改用网页版。"
          : "连不上网页版（" + this.base + "）。请检查网络和选项里的网址。", 0, "network");
      }
      if (r.type === "opaqueredirect" || (r.status >= 300 && r.status < 400)) throw new ApiError("需要登录", 401, "auth");
      let d = null;
      try { d = await r.json(); } catch (e) { /* 非 JSON */ }
      if (!r.ok) {
        let m = d && (typeof d.detail === "string" ? d.detail : d.message) || ("请求失败（HTTP " + r.status + "）");
        let kind = r.status === 401 ? "auth" : "";
        if (r.status === 403 && /Origin|来源/.test(m)) {
          kind = "origin";
          m = "网页版拒绝了扩展的来源（Origin）。请到选项页点「检测连接」，或让管理员把扩展 ID " + chrome.runtime.id + " 加进服务端环境变量 FREIGHT_HUB_ALLOWED_ORIGINS。";
        }
        throw new ApiError(m, r.status, kind);
      }
      return d;
    }
    get(p, o) { return this.call(p, o); }
    post(p, body, o) { return this.call(p, Object.assign({ method: "POST", body }, o || {})); }
  }

  g.FH = Object.assign(g.FH || {}, { DEFAULTS, normUrl, getSettings, saveSettings, baseOf, ApiError, Client });
})(typeof self !== "undefined" ? self : window);
