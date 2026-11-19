// 快速询价浮窗：逐平台并发查询（每个平台一次请求 → 各自的加载 / 成功 / 失败状态），聚合出「全部最低」。
(() => {
  "use strict";
  const $ = s => document.querySelector(s);
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = v => Number(v).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const S = { countries: [], plats: [], settings: { popup_autoclose: true }, gen: 0, busy: false, res: {}, csrf: "" };
  const desk = () => (window.pywebview && window.pywebview.api) || null;

  async function api(url, opt) {
    const o = Object.assign({ headers: {} }, opt || {});
    if (o.body && typeof o.body !== "string") { o.body = JSON.stringify(o.body); o.headers["Content-Type"] = "application/json"; }
    if (S.csrf && (o.method || "GET") !== "GET") o.headers["X-CSRF-Token"] = S.csrf;
    const r = await fetch(url, o);
    let d = null; try { d = await r.json(); } catch (e) {}
    if (!r.ok) { const e = new Error((d && (d.detail || d.message)) ? (typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail)) : "请求失败 " + r.status); e.status = r.status; throw e; }
    return d;
  }

  function closePopup() {
    const a = desk();
    if (a && a.close_popup) a.close_popup(); else { window.__closed = true; try { window.parent !== window && window.parent.postMessage({ fhQuick: "close" }, "*"); window.close(); } catch (e) {} }
  }
  window.__quickClose = closePopup;

  function usable() { return S.plats.filter(p => p.implemented && p.enabled && p.state !== "no_account"); }

  function render() {
    const ids = Object.keys(S.res);
    const list = $("#qlist");
    if (!ids.length) { list.innerHTML = `<div class="empty">${usable().length ? `将同时查询 ${usable().length} 个可用平台` : "还没有可用平台：请先到主窗口「平台账号」配置"}</div>`; return; }
    let overall = null;
    ids.forEach(id => { const r = S.res[id]; if (r.state === "ok" && r.best && (!overall || (r.best.currency === "CNY" ? 0 : 1) < (overall.best.currency === "CNY" ? 0 : 1) || ((r.best.currency === "CNY") === (overall.best.currency === "CNY") && r.best.total < overall.best.total))) overall = r; });
    const pending = ids.filter(id => S.res[id].state === "loading").length;
    const done = ids.length - pending;
    const b = $("#qbest");
    if (overall) {
      const x = overall.best;
      b.className = "best";
      b.innerHTML = `<span>全部最低</span><b class="p">${x.currency === "CNY" ? "¥" + money(x.total) : esc(x.currency) + " " + money(x.total)}</b><span class="m">${esc(overall.name)} · ${esc(x.channel)}${x.days ? " · " + esc(x.days) + " 天" : ""}</span><span class="sp"></span>${pending ? `<span class="m"><i class="spin"></i>${done}/${ids.length}</span>` : ""}`;
    } else if (pending) { b.className = "best wait"; b.innerHTML = `<i class="spin"></i>正在查询 ${ids.length} 个平台…`; }
    else { b.className = "best fail"; b.textContent = "没有平台返回价格，见下方各平台原因"; }
    const order = ids.slice().sort((a, c) => rank(S.res[a]) - rank(S.res[c]) || (S.res[a].best && S.res[c].best ? S.res[a].best.total - S.res[c].best.total : 0));
    list.innerHTML = order.map(id => {
      const r = S.res[id], cls = r.state === "ok" && r.best ? "ok" : (CLS[r.state] || r.state);
      const isMin = overall && overall.id === id;
      let ch = "", pr = "", dy = "";
      if (r.state === "loading") { ch = `<i class="spin"></i>查询中…`; }
      else if (r.state === "ok" && r.best) {
        ch = `${esc(r.best.channel)}${isMin ? '<span class="tg">全场最低</span>' : ""}${r.best.currency !== "CNY" ? `<small>币种 ${esc(r.best.currency)}，未折算人民币</small>` : ""}`;
        pr = r.best.currency === "CNY" ? "¥" + money(r.best.total) : esc(r.best.currency) + " " + money(r.best.total);
        dy = r.best.days ? esc(r.best.days) + " 天" : "-";
      } else if (r.state === "ok") { ch = esc(r.message || "没有返回可用渠道"); pr = "-"; }
      else { ch = esc(short(r.message) || labelOf(r.state)); pr = "-"; }
      return `<div class="it ${cls}${isMin ? " min" : ""}" data-id="${esc(id)}" title="${esc(r.message || "")}"><span class="pb ${esc(id)}">${esc(r.name)}</span><span class="ch">${ch}</span><span class="pr">${pr}</span><span class="dy">${dy}</span></div>`;
    }).join("");
    list.querySelectorAll(".it.ok").forEach(e => e.onclick = openMain);
  }
  const CLS = { ok: "empty-ok", error: "err", login_failed: "err", manual: "err", pending: "pend", skipped: "skip", no_account: "skip", loading: "loading" };
  const rank = r => r.state === "ok" && r.best ? 0 : r.state === "loading" ? 1 : 2;
  const short = m => { m = String(m || "").replace(/\s+/g, " "); return m.length > 46 ? m.slice(0, 45) + "…" : m; };
  const labelOf = s => ({ error: "查询失败", pending: "暂未支持运价试算", no_account: "尚未配置", skipped: "已停用", manual: "需人工处理", login_failed: "登录失败" }[s] || s);

  async function run(text) {
    if (S.busy) S.gen++;                       // 新查询取代旧查询
    const gen = ++S.gen;
    const ids = usable().map(p => p.id);
    if (!ids.length) { render(); return; }
    let body = text ? { text } : { country: $("#inCountry").value, weight: parseFloat($("#inWeight").value) || 0, length: +$("#inL").value || 0, width: +$("#inW").value || 0, height: +$("#inH").value || 0 };
    if (!text && !body.country.trim()) { setMsg("请填写目的国", true); $("#inCountry").focus(); return; }
    if (!text && !(body.weight > 0)) { setMsg("请填写大于 0 的重量", true); $("#inWeight").focus(); return; }
    S.busy = true; S.res = {}; S.tokens = []; S.query = null; $("#bGo").disabled = true;
    ids.forEach(id => { const p = S.plats.find(x => x.id === id); S.res[id] = { id, name: p.name, state: "loading" }; });
    render();
    let left = ids.length;
    await Promise.all(ids.map(async id => {
      try {
        const r = await api("/api/quote/quick", { method: "POST", body: Object.assign({ platforms: [id] }, body) });
        if (gen !== S.gen) return;
        const p = r.platforms[0];
        S.res[id] = { id, name: p.name, state: p.state, message: p.message, best: p.best, count: p.count, ms: p.elapsed_ms };
        if (r.tokens && p.state === "ok" && p.count) S.tokens.push(r.tokens[0]);
        if (!S.query) { S.query = r.query; fillFrom(r.query); }
      } catch (e) {
        if (gen !== S.gen) return;
        if (e.status === 400) { S.res = {}; setMsg(e.message, true); S.gen++; return; }
        S.res[id] = { id, name: (S.plats.find(x => x.id === id) || {}).name || id, state: "error", message: e.message };
      }
      if (gen === S.gen) render();
    }));
    if (gen !== S.gen) return;
    S.busy = false; $("#bGo").disabled = false; render();
    if (S.query) saveLast();
  }
  function setMsg(m, bad) { const b = $("#qbest"); b.className = "best " + (bad ? "fail" : "idle"); b.textContent = m; $("#qlist").innerHTML = ""; S.busy = false; $("#bGo").disabled = false; }
  function fillFrom(q) {
    $("#inCountry").value = `${q.country_name} ${q.country}`; $("#inWeight").value = q.weight;
    $("#inL").value = q.length || ""; $("#inW").value = q.width || ""; $("#inH").value = q.height || "";
  }
  function lastObj() { return { country: $("#inCountry").value, weight: $("#inWeight").value, length: $("#inL").value, width: $("#inW").value, height: $("#inH").value }; }
  function saveLast() {
    const l = lastObj();
    try { localStorage.setItem("fh.quick.last", JSON.stringify(l)); } catch (e) {}
    api("/api/shell/settings", { method: "PUT", body: { quick_last: l } }).catch(() => {});
  }
  function restoreLast() {
    let l = S.settings.quick_last;
    if (!l || !l.country) { try { l = JSON.parse(localStorage.getItem("fh.quick.last") || "null") || l; } catch (e) {} }
    if (l) { $("#inCountry").value = l.country || ""; $("#inWeight").value = l.weight || ""; $("#inL").value = l.length || ""; $("#inW").value = l.width || ""; $("#inH").value = l.height || ""; }
  }

  async function openMain() {
    if (!S.tokens || !S.tokens.length) return;
    try {
      const r = await api("/api/quote/quick/commit", { method: "POST", body: { tokens: S.tokens } });
      window.__lastOpen = r.history_id;
      const a = desk();
      if (a && a.open_main) a.open_main(r.history_id);
      else { const w = window.open("/?open=" + r.history_id, "_blank"); window.__openedMain = !!w; }
    } catch (e) { setMsg(e.message, true); }
  }

  // 由桌面外壳在每次唤出时调用：复位、聚焦；可带 text 直接查询
  window.__quickShow = opts => {
    opts = opts || {};
    S.gen++; S.busy = false; S.res = {}; $("#bGo").disabled = false;
    $("#qbest").className = "best idle"; $("#qbest").textContent = "输入目的国和重量，按 Enter 查询各平台最低价"; render();
    if (!opts.text) restoreLast();
    window.focus();
    const c = $("#inCountry"), w = $("#inWeight");
    (c.value ? w : c).focus(); (c.value ? w : c).select();
    if (opts.text) run(opts.text);
  };

  let blurT = 0;
  window.addEventListener("blur", () => { clearTimeout(blurT); if (S.settings.popup_autoclose) blurT = setTimeout(() => { if (!document.hasFocus()) closePopup(); }, 250); });
  window.addEventListener("focus", () => clearTimeout(blurT));
  document.addEventListener("keydown", e => { if (e.key === "Escape") { e.preventDefault(); closePopup(); } });
  $("#qx").onclick = closePopup;
  $("#qf").onsubmit = e => { e.preventDefault(); run(""); };
  $("#cbAuto").onchange = e => { S.settings.popup_autoclose = e.target.checked; api("/api/shell/settings", { method: "PUT", body: { popup_autoclose: e.target.checked } }).catch(() => {}); };

  (async () => {
    try {
      const meta = await api("/api/meta");
      S.csrf = meta.csrf || ""; S.countries = meta.countries;
      window.fhTrial && window.fhTrial(meta.trial, $("#qhSub"));
      $("#countryList").innerHTML = S.countries.map(c => `<option value="${esc(c.name)} ${esc(c.code)}"></option>`).join("");
      S.plats = await api("/api/platforms");
      try { S.settings = Object.assign(S.settings, await api("/api/shell/settings")); } catch (e) { /* 非管理员（网页模式）读不到设置：用默认值 */ }
      $("#cbAuto").checked = !!S.settings.popup_autoclose;
      $("#qhKey").textContent = S.settings.hotkey || "";
      document.body.classList.add("ready");
      const sp = new URLSearchParams(location.search);
      if (sp.get("text")) window.__quickShow({ text: sp.get("text") }); else window.__quickShow({});
      window.__quickReady = true;
    } catch (e) { $("#qbest").className = "best fail"; $("#qbest").textContent = "启动失败：" + e.message; }
  })();
})();
