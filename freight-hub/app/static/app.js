(() => {
"use strict";
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// 网页/服务器模式：非 GET 请求带 CSRF 令牌，会话过期（401）回登录页
const withCsrf = opt => (opt && opt.method && opt.method !== "GET" && window.__csrf) ? { ...opt, headers: { ...(opt.headers || {}), "X-CSRF-Token": window.__csrf } } : opt;
const api = async (url, opt) => { const r = await fetch(url, withCsrf(opt)); if (r.status === 401 && window.__web) { location.href = "/login"; throw new Error("登录已过期"); } if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch (e) {} throw new Error(m); } return r.json(); };
const jsend = (url, body, method = "POST") => api(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const pad = n => String(n).padStart(2, "0");
const fmtTime = ts => { const d = new Date(ts * 1000); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; };
const money = v => v == null ? "-" : Number(v).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };

// ---------------- 图标 ----------------
const IC = {
  new: '<path d="M4 2h5l3 3v9H4z"/><path d="M8 7v4M6 9h4"/>',
  run: '<circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5L14 14"/>',
  reset: '<path d="M3 8a5 5 0 1 0 1.6-3.7"/><path d="M3 2.5v3h3"/>',
  export: '<path d="M8 2v8M5 7l3 3 3-3"/><path d="M3 12v2h10v-2"/>',
  copy: '<rect x="5" y="5" width="8" height="9" rx="1"/><path d="M3 11V3a1 1 0 0 1 1-1h6"/>',
  user: '<circle cx="8" cy="5.5" r="2.8"/><path d="M2.5 14c.5-3 2.7-4.5 5.5-4.5s5 1.500 5.500 4.500"/>',
  history: '<circle cx="8" cy="8" r="6"/><path d="M8 4.500V8l2.500 1.500"/>',
  info: '<circle cx="8" cy="8" r="6"/><path d="M8 7.200V11M8 5v.2"/>',
  refresh: '<path d="M13 8a5 5 0 1 1-1.600-3.700"/><path d="M13 2.500v3h-3"/>',
  plug: '<path d="M6 2v3M10 2v3M4.500 5h7v2.500a3.500 3.500 0 0 1-7 0zM8 11v3"/>',
  trash: '<path d="M3 4.500h10M6.500 4.500V3h3v1.500M4.500 4.500l.5 9h6l.5-9"/>',
  folder: '<path d="M2 4.500h4l1.500 1.500H14V13H2z"/>',
  box: '<path d="M2.500 5L8 2.500 13.500 5v6L8 13.500 2.500 11zM2.500 5L8 7.500 13.500 5M8 7.500v6"/>',
  clear: '<path d="M2.500 3h11L9.500 8.500V13l-3-1.500V8.500z"/>',
  open: '<path d="M2 4.500h4l1.500 1.500H14V13H2z"/>',
  eye: '<path d="M1.500 8S4 3.500 8 3.500 14.500 8 14.500 8 12 12.500 8 12.500 1.500 8 1.500 8z"/><circle cx="8" cy="8" r="2"/>',
};
const icon = n => `<i class="ic"><svg viewBox="0 0 16 16">${IC[n] || ""}</svg></i>`;

// ---------------- 与桌面壳通信 ----------------
const desk = { api: () => (window.pywebview && window.pywebview.api) || null };
function enableFrame() { document.body.classList.add("hasctl"); }
if (new URLSearchParams(location.search).has("frame")) enableFrame();
window.addEventListener("pywebviewready", enableFrame);
if (window.pywebview && window.pywebview.api) enableFrame();
async function toggleMax() {
  const a = desk.api();
  if (a) { const m = await a.toggle_maximize(); document.body.classList.toggle("maxed", !!m); }
  else document.body.classList.toggle("maxed");
}
$("#wMin").onclick = () => desk.api() && desk.api().minimize();
$("#wMax").onclick = toggleMax;
$("#wClose").onclick = () => desk.api() ? desk.api().close() : window.close();
$("#titlebar").ondblclick = e => { if (!e.target.closest("#winCtl")) toggleMax(); };
// 无边框窗口的缩放把手（由 JS 驱动 pywebview 改窗口大小）
["n", "s", "w", "e", "nw", "ne", "sw", "se"].forEach(d => {
  const g = el("div", "grip " + d); document.body.appendChild(g);
  g.onpointerdown = async ev => {
    const a = desk.api(); if (!a) return;
    ev.preventDefault(); g.setPointerCapture(ev.pointerId);
    const [x0, y0, w0, h0] = await a.get_rect(); const sx = ev.screenX, sy = ev.screenY; let busy = false;
    g.onpointermove = e => {
      if (busy) return; busy = true;
      const dx = e.screenX - sx, dy = e.screenY - sy; let x = x0, y = y0, w = w0, h = h0;
      if (d.includes("e")) w = w0 + dx; if (d.includes("s")) h = h0 + dy;
      if (d.includes("w")) { w = w0 - dx; x = x0 + dx; } if (d.includes("n")) { h = h0 - dy; y = y0 + dy; }
      if (w < 900) { if (d.includes("w")) x -= 900 - w; w = 900; } if (h < 560) { if (d.includes("n")) y -= 560 - h; h = 560; }
      Promise.resolve(a.set_rect(Math.round(x), Math.round(y), Math.round(w), Math.round(h))).finally(() => busy = false);
    };
    g.onpointerup = () => { g.onpointermove = null; g.onpointerup = null; };
  };
});

// ---------------- 状态 ----------------
const S = { platforms: [], countries: [], meta: {}, tabs: [], active: null, hist: [], uid: 0, ctx: null };
const STATE_TXT = { ok: "成功", error: "失败", login_failed: "登录失败", no_account: "未配置账号", manual: "需人工验证", pending: "待验证/需申请文档", skipped: "已停用" };
const plat = id => S.platforms.find(p => p.id === id);
const sbMsg = m => { $("#sbMsg").textContent = m; };

// ---------------- 对话框 ----------------
function msgbox(title, text, buttons = [{ t: "确定", v: true, def: true }]) {
  return new Promise(res => {
    const m = $("#modal");
    m.innerHTML = `<div class="dlg"><div class="dh">${esc(title)}</div><div class="db">${esc(text)}</div><div class="df">${buttons.map((b, i) => `<button class="btn ${b.def ? "def" : ""}" data-i="${i}">${esc(b.t)}</button>`).join("")}</div></div>`;
    m.style.display = "flex";
    $$("button", m).forEach(b => b.onclick = () => { m.style.display = "none"; res(buttons[+b.dataset.i].v); });
    const f = $("button.def", m) || $("button", m); f && f.focus();
  });
}
const confirmBox = (title, text) => msgbox(title, text, [{ t: "确定", v: true, def: true }, { t: "取消", v: false }]);

// ---------------- 弹出菜单 ----------------
function popMenu(items, x, y, layer) {
  const m = layer || $("#ctxmenu");
  m.innerHTML = ""; m.style.display = "block";
  items.forEach(it => {
    if (it.sep) { m.appendChild(el("div", "msep")); return; }
    const d = el("div", "mi" + (it.dis ? " dis" : "") + (it.chk ? " chk" : ""));
    d.innerHTML = (it.dot ? `<span class="mdot dot ${it.dot}" style="margin:0"></span>` : "") + `<span>${esc(it.t)}</span>` + (it.k ? `<span class="k">${esc(it.k)}</span>` : "");
    d.onclick = () => { if (it.dis) return; closeMenus(); it.f && it.f(); };
    m.appendChild(d);
  });
  m.style.left = "0px"; m.style.top = "0px";
  const r = m.getBoundingClientRect();
  m.style.left = Math.max(2, Math.min(x, innerWidth - r.width - 4)) + "px";
  m.style.top = Math.max(2, Math.min(y, innerHeight - r.height - 4)) + "px";
}
function closeMenus() {
  const c = $("#ctxmenu"); c.style.display = "none"; c.innerHTML = "";
  $$(".mb-item.open").forEach(x => x.classList.remove("open")); S.openMenu = null;
}
document.addEventListener("mousedown", e => { if (!e.target.closest(".menu-pop") && !e.target.closest(".mb-item")) closeMenus(); });
document.addEventListener("keydown", e => { if (e.key === "Escape") closeMenus(); });
window.addEventListener("blur", closeMenus);

// ---------------- 剪贴板 / 导出 ----------------
async function copyText(t) {
  try { await navigator.clipboard.writeText(t); }
  catch (e) { const ta = el("textarea"); ta.value = t; ta.style.cssText = "position:fixed;left:-999px"; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove(); }
}
const trackTxt = v => v === true ? "有" : v === false ? "无" : "";
const csvCell = v => { const isStr = typeof v === "string"; v = String(v ?? ""); if (isStr && /^[=+\-@\t\r]/.test(v)) v = "'" + v; return /[",\n\r]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v; };
function rowsToCsv(rows) {
  const head = ["平台", "渠道", "分组", "总价(CNY)", "时效(天)", "轨迹", "备注"];
  return "\ufeff" + [head, ...rows.map(r => [r.platform_name, r.channel, r.group, r.total, r.days, trackTxt(r.track), r.remark])].map(l => l.map(csvCell).join(",")).join("\r\n");
}
async function saveFile(name, text) {
  const a = desk.api();
  if (a && a.save_file) { const p = await a.save_file(name, text); if (p) sbMsg("已保存：" + p); return !!p; }
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  const l = el("a"); l.href = url; l.download = name; document.body.appendChild(l); l.click(); l.remove(); setTimeout(() => URL.revokeObjectURL(url), 2000);
  sbMsg("已导出：" + name); return true;
}

// ---------------- 数据网格 ----------------
function Grid(box, o) {
  const g = { rows: [], view: [], sel: new Set(), sort: o.sort || null, last: null, cols: o.cols.map(c => ({ ...c })) };
  box.classList.add("gridbox");
  box.innerHTML = `<div class="gridscroll" tabindex="0"><table class="grid"><colgroup></colgroup><thead><tr></tr></thead><tbody></tbody></table><div class="gridempty" style="display:none"></div></div>`;
  const sc = $(".gridscroll", box), tbl = $("table", box), cg = $("colgroup", box), hr = $("thead tr", box), tb = $("tbody", box), emp = $(".gridempty", box);
  g.el = sc;
  const setWidth = () => { tbl.style.width = g.cols.reduce((a, c) => a + c.w, 0) + "px"; tbl.style.minWidth = "100%"; };
  function head() {
    cg.innerHTML = g.cols.map(c => `<col style="width:${c.w}px">`).join("");
    hr.innerHTML = g.cols.map((c, i) => `<th data-i="${i}" class="${c.sortable === false ? "" : "sortable"} ${c.align === "r" ? "r" : c.align === "c" ? "c" : ""}" style="position:sticky">${esc(c.t)}${g.sort && g.sort.k === c.k ? `<span class="ar">${g.sort.d > 0 ? "▲" : "▼"}</span>` : ""}<span class="rz"></span></th>`).join("");
    setWidth();
    $$("th", hr).forEach(th => {
      const i = +th.dataset.i;
      th.onclick = e => { if (e.target.classList.contains("rz") || g.cols[i].sortable === false) return; g.sort = g.sort && g.sort.k === g.cols[i].k ? { k: g.cols[i].k, d: -g.sort.d } : { k: g.cols[i].k, d: 1 }; g.refresh(); };
      $(".rz", th).onmousedown = e => {
        e.preventDefault(); e.stopPropagation(); const x0 = e.clientX, w0 = g.cols[i].w;
        const mv = ev => { g.cols[i].w = Math.max(36, w0 + ev.clientX - x0); cg.children[i].style.width = g.cols[i].w + "px"; setWidth(); };
        const up = () => { document.removeEventListener("mousemove", mv); document.removeEventListener("mouseup", up); };
        document.addEventListener("mousemove", mv); document.addEventListener("mouseup", up);
      };
      $(".rz", th).ondblclick = e => { e.stopPropagation(); g.cols[i].w = o.cols[i].w; head(); body(); };
    });
  }
  const val = (c, r) => c.sortVal ? c.sortVal(r) : r[c.k];
  function sortRows() {
    g.view = [...g.rows];
    if (g.sort) {
      const c = g.cols.find(x => x.k === g.sort.k), d = g.sort.d;
      if (c) g.view.sort((a, b) => {
        let A = val(c, a), B = val(c, b); const na = A == null || A === "", nb = B == null || B === "";
        if (na || nb) return na && nb ? 0 : na ? 1 : -1;
        return (typeof A === "string" || typeof B === "string" ? String(A).localeCompare(String(B), "zh") : A - B) * d;
      });
    }
  }
  function body() {
    tb.innerHTML = g.view.map((r, vi) => `<tr data-vi="${vi}" class="${g.sel.has(r) ? "sel" : ""}">${g.cols.map(c => {
      const v = c.render ? c.render(r, vi) : esc(r[c.k]);
      return `<td class="${c.align === "r" ? "r" : c.align === "c" ? "c" : ""} ${c.cls || ""}" title="${c.tip === false ? "" : esc(String(c.title ? c.title(r) : c.text ? c.text(r) : r[c.k] ?? "")).slice(0, 200)}">${v}</td>`;
    }).join("")}</tr>`).join("");
    emp.style.display = g.view.length ? "none" : "block"; emp.textContent = o.empty || "没有数据";
  }
  const fire = () => o.onSelect && o.onSelect(g.selected());
  const paintSel = () => $$("tbody tr", box).forEach(tr => tr.classList.toggle("sel", g.sel.has(g.view[+tr.dataset.vi])));
  g.selected = () => g.view.filter(r => g.sel.has(r));
  g.refresh = () => { sortRows(); head(); body(); };
  g.setRows = rows => { g.rows = rows; const keep = new Set(rows); g.sel = new Set([...g.sel].filter(r => keep.has(r))); g.refresh(); fire(); };
  g.select = (rows, silent) => { g.sel = new Set(rows); paintSel(); if (!silent) fire(); };
  function pick(vi, e) {
    const r = g.view[vi]; if (!r) return;
    if (o.multi && e && e.shiftKey && g.last != null) { const a = Math.min(g.last, vi), b = Math.max(g.last, vi); g.sel = new Set(g.view.slice(a, b + 1)); }
    else if (o.multi && e && (e.ctrlKey || e.metaKey)) { g.sel.has(r) ? g.sel.delete(r) : g.sel.add(r); g.last = vi; }
    else { g.sel = new Set([r]); g.last = vi; }
    paintSel(); fire();
  }
  tb.onmousedown = e => { const tr = e.target.closest("tr"); if (tr && e.button === 0) { pick(+tr.dataset.vi, e); sc.focus(); } };
  tb.ondblclick = e => { const tr = e.target.closest("tr"); if (tr && o.onOpen) o.onOpen(g.view[+tr.dataset.vi]); };
  tb.oncontextmenu = e => {
    const tr = e.target.closest("tr"); if (!tr || !o.menu) return; e.preventDefault();
    const r = g.view[+tr.dataset.vi]; if (!g.sel.has(r)) pick(+tr.dataset.vi);
    popMenu(o.menu(g.selected(), g), e.clientX, e.clientY);
  };
  sc.oncontextmenu = e => { if (!e.target.closest("tbody") && o.blankMenu) { e.preventDefault(); popMenu(o.blankMenu(g), e.clientX, e.clientY); } };
  sc.onkeydown = e => {
    const cur = g.last == null ? -1 : g.last;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault(); const n = Math.max(0, Math.min(g.view.length - 1, cur + (e.key === "ArrowDown" ? 1 : -1))); pick(n, e);
      const tr = tb.children[n]; tr && tr.scrollIntoView({ block: "nearest" });
    } else if (e.key === "Home" || e.key === "End") { e.preventDefault(); const n = e.key === "Home" ? 0 : g.view.length - 1; pick(n, e); tb.children[n] && tb.children[n].scrollIntoView({ block: "nearest" }); }
    else if (e.key === "Enter" && o.onOpen && g.selected()[0]) o.onOpen(g.selected()[0]);
    else if (e.key === "Delete" && o.onDelete) o.onDelete(g.selected());
    else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a" && o.multi) { e.preventDefault(); g.select(g.view); }
    else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "c" && o.onCopy) { e.preventDefault(); o.onCopy(g.selected()); }
  };
  g.resetWidths = () => { g.cols.forEach((c, i) => c.w = o.cols[i].w); head(); };
  head(); body();
  return g;
}

// ---------------- 标签页 ----------------
const TAB_ICON = { query: "run", accounts: "user", history: "history", about: "info" };
function addTab(t, panel) {
  t.panel = panel; $("#tabbody").appendChild(panel); S.tabs.push(t); activate(t.id); return t;
}
function renderTabs() {
  const box = $("#tabs"); box.innerHTML = "";
  S.tabs.forEach(t => {
    const d = el("div", "tab" + (t.id === S.active ? " active" : ""), `${icon(TAB_ICON[t.type])}<span class="tt">${esc(t.title)}</span><span class="x" title="关闭 (Ctrl+W)">✕</span>`);
    d.onmousedown = e => { if (e.button === 1) { closeTab(t.id); e.preventDefault(); } else if (!e.target.classList.contains("x")) activate(t.id); };
    $(".x", d).onclick = e => { e.stopPropagation(); closeTab(t.id); };
    box.appendChild(d);
  });
  const add = el("button", "tabadd", "+"); add.title = "新建查询 (Ctrl+T)"; add.onclick = () => newQuery(); box.appendChild(add);
}
function activate(id) {
  S.active = id; S.tabs.forEach(t => t.panel.classList.toggle("active", t.id === id));
  renderTabs(); updateToolbar(); updateStatus(); markTree();
  const t = cur(); t && t.onShow && t.onShow();
}
const cur = () => S.tabs.find(t => t.id === S.active);
function closeTab(id) {
  const i = S.tabs.findIndex(t => t.id === id); if (i < 0) return;
  S.tabs[i].panel.remove(); S.tabs.splice(i, 1);
  if (!S.tabs.length) { newQuery(); return; }
  if (S.active === id) activate(S.tabs[Math.min(i, S.tabs.length - 1)].id); else renderTabs();
}
function setTitle(t, title) { t.title = title; renderTabs(); }
function single(type, title, build) {
  let t = S.tabs.find(x => x.type === type);
  if (t) { activate(t.id); return t; }
  t = { id: ++S.uid, type, title }; const p = el("div", "panel"); addTab(t, p); build(t, p); return t;
}

// ---------------- 询价标签页 ----------------
const defaultSel = () => S.platforms.filter(p => p.implemented && p.enabled && p.state !== "no_account").map(p => p.id);
function resolveCountry(txt) {
  const t = txt.trim(); if (!t) return null; const up = t.toUpperCase();
  let c = S.countries.find(c => c.code === up) || S.countries.find(c => c.name === t) || S.countries.find(c => `${c.name} ${c.code}` === t || `${c.code} ${c.name}` === t) || S.countries.find(c => c.name.includes(t));
  if (!c && /^[A-Za-z]{2}$/.test(t)) c = { code: up, name: up };
  return c;
}
function newQuery(preset) {
  const t = { id: ++S.uid, type: "query", title: "新建查询", sel: new Set(defaultSel()), result: null, fPlat: new Set(), busy: false, ms: 0 };
  const p = el("div", "panel"); t.panel = p;
  p.innerHTML = `
  <div class="qtop">
    <fieldset class="gb"><legend>查询条件</legend><div class="frow">
      <div class="fld"><label>目的国 / 地区</label><input class="inCountry" list="countryList" style="width:150px" placeholder="如 美国 或 US" autocomplete="off"></div>
      <div class="fld"><label>重量 (kg)</label><input class="inWeight" type="number" min="0" step="0.01" value="1" style="width:70px"></div>
      <div class="fld"><label>邮编（可选）</label><input class="inPost" style="width:80px"></div>
      <div class="fld"><label>尺寸 cm（可选）</label><div style="display:flex"><input class="inL" type="number" min="0" placeholder="长" style="width:46px"><span class="dimx">×</span><input class="inW" type="number" min="0" placeholder="宽" style="width:46px"><span class="dimx">×</span><input class="inH" type="number" min="0" placeholder="高" style="width:46px"></div></div>
    </div></fieldset>
    <fieldset class="gb gb-plat"><legend>查询平台</legend><div class="platlist"></div></fieldset>
    <fieldset class="gb"><legend>操作</legend><div class="frow" style="align-items:center">
      <button class="btn def bRun">${icon("run")}查询 (F5)</button><button class="btn bReset">重置</button></div></fieldset>
  </div>
  <div class="pstrip"></div>
  <div class="filterbar">
    <span>筛选：</span>
    <span class="fld inl"><label>关键词</label><input class="fKey" style="width:120px" placeholder="渠道/备注"></span>
    <span class="fld inl"><label>最高价 ¥</label><input class="fMaxP" type="number" min="0" style="width:64px"></span>
    <span class="fld inl"><label>最长时效 天</label><input class="fMaxD" type="number" min="0" style="width:50px"></span>
    <label class="cb"><input type="checkbox" class="fTrack">仅有轨迹</label>
    <span class="fchips"></span>
    <button class="btn small bClr">清除筛选</button>
    <span class="sp resCount">未查询</span>
  </div>
  <div class="gridhost" style="flex:1;display:flex;flex-direction:column;min-height:140px"></div>
  <fieldset class="gb" style="flex:none"><legend>选中行明细</legend><div class="dtl detail"><span class="muted">在表格中选择一行查看费用明细；右键可复制或导出。</span></div></fieldset>`;
  const q = s => $(s, p);
  t.q = q;
  t.minP = Infinity; t.minD = null;
  const cols = [
    { k: "#", t: "#", w: 44, align: "r", sortable: false, render: (r, i) => i + 1, tip: false },
    { k: "platform_name", t: "平台", w: 64, render: r => `<span class="pb ${r.platform}">${esc(r.platform_name)}</span>${r.env === "sandbox" ? '<span class="envb test" title="测试环境数据，非真实报价">【测试】</span>' : ""}`, text: r => r.platform_name },
    { k: "channel", t: "渠道", w: 300, render: r => `${esc(r.channel)}${!r.excluded && r.total === t.minP ? '<span class="tg">全场最低</span>' : ""}${r.excluded ? `<span class="tg" style="background:#8a8f98;color:#fff" title="${esc(r.excluded)}">不计入最低</span>` : ""}${t.minD != null && r.days_max === t.minD ? '<span class="tg fast">最快</span>' : ""}` },
    { k: "group", t: "分组", w: 110 },
    { k: "total", t: "总价 (¥)", w: 92, align: "r", cls: "price", render: r => money(r.total), text: r => r.total },
    { k: "days", t: "时效 (天)", w: 84, sortVal: r => r.days_min == null ? null : r.days_min * 1000 + (r.days_max || 0), render: r => esc(r.days) || '<span class="muted">-</span>' },
    { k: "track", t: "轨迹", w: 56, align: "c", sortVal: r => r.track === true ? 0 : r.track === false ? 1 : null, render: r => trackTxt(r.track) || '<span class="muted">-</span>', text: r => trackTxt(r.track) },
    { k: "remark", t: "备注", w: 480 },
  ];
  const menu = rows => {
    const one = rows.length === 1 ? rows[0] : null, none = !rows.length;
    return [
      { t: `复制选中行 (${rows.length})`, k: "Ctrl+C", dis: none, f: () => copyRows(t, rows) },
      { t: "复制渠道名称", dis: none, f: () => { copyText(rows.map(r => r.channel).join("\n")); sbMsg("已复制渠道名称"); } },
      { t: "复制价格", dis: none, f: () => { copyText(rows.map(r => r.total).join("\n")); sbMsg("已复制价格"); } },
      { sep: 1 },
      { t: `导出选中行为 CSV…`, dis: none, f: () => exportRows(t, rows, true) },
      { t: "导出全部结果为 CSV…", dis: !t.grid.view.length, k: "Ctrl+E", f: () => exportRows(t, t.grid.view) },
      { sep: 1 },
      { t: one ? `只看「${one.platform_name}」` : "只看选中行所属平台", dis: none, f: () => { t.fPlat = new Set(rows.map(r => r.platform)); applyFilter(t); syncChips(t); } },
      { t: "清除筛选", f: () => clearFilter(t) },
    ];
  };
  t.grid = Grid(q(".gridhost"), { cols, multi: true, sort: { k: "total", d: 1 }, empty: "尚无结果：输入目的国和重量后按 F5 查询", menu,
    onSelect: rows => showDetail(t, rows), onCopy: rows => copyRows(t, rows), blankMenu: () => menu([]) });
  t.platCtl = () => renderPlatBoxes(t);
  q(".bRun").onclick = () => runQuery(t);
  q(".bReset").onclick = () => { ["inCountry", "inPost", "inL", "inW", "inH"].forEach(c => q("." + c).value = ""); q(".inWeight").value = 1; q(".inCountry").focus(); };
  ["fKey", "fMaxP", "fMaxD"].forEach(c => q("." + c).oninput = () => applyFilter(t));
  q(".fTrack").onchange = () => applyFilter(t);
  q(".bClr").onclick = () => clearFilter(t);
  ["inCountry", "inWeight", "inPost", "inL", "inW", "inH"].forEach(c => q("." + c).addEventListener("keydown", e => { if (e.key === "Enter") runQuery(t); }));
  t.onShow = () => { setTimeout(() => { if (!t.result) q(".inCountry").focus(); }, 0); };
  renderPlatBoxes(t);
  addTab(t, p);
  if (preset) fillForm(t, preset);
  return t;
}
function fillForm(t, qy) {
  const q = t.q;
  q(".inCountry").value = `${qy.country_name || qy.country} ${qy.country}`; q(".inWeight").value = qy.weight;
  q(".inPost").value = qy.postcode || ""; q(".inL").value = qy.length || ""; q(".inW").value = qy.width || ""; q(".inH").value = qy.height || "";
}
function renderPlatBoxes(t) {
  const box = t.q(".platlist");
  box.innerHTML = S.platforms.map(p => {
    const dis = !p.implemented || p.state === "no_account" || !p.enabled;
    return `<label class="cb ${dis ? "dis" : ""}" title="${esc(p.label)}"><input type="checkbox" data-id="${p.id}" ${t.sel.has(p.id) && !dis ? "checked" : ""} ${dis ? "disabled" : ""}><span class="dot ${p.state}" style="margin:0"></span>${esc(p.name)}${p.env_tag ? `<span class="envb test">${esc(p.env_tag)}</span>` : ""}${dis ? `<span class="muted">（${esc(p.label)}）</span>` : ""}</label>`;
  }).join("");
  $$("input", box).forEach(i => i.onchange = () => { i.checked ? t.sel.add(i.dataset.id) : t.sel.delete(i.dataset.id); });
}
function showDetail(t, rows) {
  const d = t.q(".detail"), r = rows[rows.length - 1];
  if (!r) { d.innerHTML = '<span class="muted">在表格中选择一行查看费用明细；右键可复制或导出。</span>'; return; }
  const kv = (a, b) => `<div class="kv"><span>${esc(a)}：</span><b>${esc(b)}</b></div>`;
  d.innerHTML = kv("平台", r.platform_name) + kv("渠道", r.channel) + kv("分组", r.group || "-") + kv("总价", `¥${money(r.total)}`) +
    kv("时效", r.days || "-") + kv("轨迹", trackTxt(r.track) || "-") + (r.fees || []).map(f => kv(f.label, f.value)).join("") + (r.remark ? `<div class="kv" style="grid-column:1/-1"><span>备注：</span><b style="font-weight:400">${esc(r.remark)}</b></div>` : "") +
    (rows.length > 1 ? `<div class="kv"><span>已选：</span><b>${rows.length} 行，合计最低 ¥${money(Math.min(...rows.map(x => x.total)))}</b></div>` : "");
}
function copyRows(t, rows) {
  if (!rows.length) return;
  copyText(rows.map(r => [r.platform_name, r.channel, r.group, r.total, r.days, trackTxt(r.track), r.remark].join("\t")).join("\n"));
  sbMsg(`已复制 ${rows.length} 行到剪贴板`);
}
function exportRows(t, rows, sel) {
  if (!rows.length) return sbMsg("没有可导出的数据");
  const q = t.result ? t.result.query : {};
  return saveFile(`报价_${q.country || ""}_${q.weight || ""}kg${sel ? "_选中" : ""}.csv`, rowsToCsv(rows));
}
function applyFilter(t) {
  const r = t.result; if (!r) return;
  const q = t.q, key = q(".fKey").value.trim().toLowerCase(), mp = parseFloat(q(".fMaxP").value), md = parseFloat(q(".fMaxD").value), tr = q(".fTrack").checked;
  const rows = r.rows.filter(x => t.fPlat.has(x.platform) && (!key || (x.channel + x.group + x.remark + x.platform_name).toLowerCase().includes(key))
    && (isNaN(mp) || x.total <= mp) && (isNaN(md) || (x.days_max != null && x.days_max <= md)) && (!tr || x.track === true));
  t.grid.setRows(rows);
  q(".resCount").textContent = `显示 ${rows.length} / 共 ${r.rows.length} 条`;
  updateStatus();
}
function syncChips(t) {
  const r = t.result; if (!r) return;
  const box = t.q(".fchips");
  box.innerHTML = r.platforms.filter(p => p.count).map(p => `<span class="fchip ${t.fPlat.has(p.id) ? "on" : ""}" data-id="${p.id}">${esc(p.name)} ${p.count}</span>`).join("");
  $$(".fchip", box).forEach(c => c.onclick = () => { const id = c.dataset.id; t.fPlat.has(id) ? t.fPlat.delete(id) : t.fPlat.add(id); syncChips(t); applyFilter(t); });
}
function clearFilter(t) {
  const q = t.q; q(".fKey").value = ""; q(".fMaxP").value = ""; q(".fMaxD").value = ""; q(".fTrack").checked = false;
  if (t.result) t.fPlat = new Set(t.result.platforms.filter(p => p.count).map(p => p.id));
  syncChips(t); applyFilter(t);
}
function showResult(t, r) {
  t.result = r; t.fPlat = new Set(r.platforms.filter(p => p.count).map(p => p.id));
  const okRows = r.rows.filter(x => !x.excluded);   // 测试数据/中港段/低于¥10/非人民币的行不参与「全场最低」
  t.minP = okRows.length ? Math.min(...okRows.map(x => x.total)) : Infinity;
  const dd = r.rows.filter(x => x.days_max != null).map(x => x.days_max); t.minD = dd.length ? Math.min(...dd) : null;
  const best = id => { const x = r.rows.filter(y => y.platform === id && !y.excluded); return x.length ? Math.min(...x.map(y => y.total)) : null; };
  t.q(".pstrip").innerHTML = r.platforms.map(p => {
    const b = best(p.id);
    return `<div class="pcell ${p.state}" title="${esc(p.message || "")}"><span class="dot ${p.state}" style="margin:0"></span><b>${esc(p.name)}</b>${p.env_tag ? `<span class="envb test">${esc(p.env_tag)}</span>` : ""}<span class="m">${STATE_TXT[p.state] || p.state}${p.state === "ok" ? ` · ${p.count} 条 · ${(p.elapsed_ms / 1000).toFixed(1)}s` : ""}</span>${b != null ? `<span>最低 <b>¥${money(b)}</b></span>` : ""}${p.message ? `<span class="m">— ${esc(p.message.slice(0, 40))}</span>` : ""}</div>`;
  }).join("");
  const qy = r.query; setTitle(t, `${qy.country_name || qy.country} ${qy.weight}kg`);
  syncChips(t); applyFilter(t);
}
async function runQuery(t) {
  t = t || cur(); if (!t || t.type !== "query" || t.busy) return;
  const q = t.q, c = resolveCountry(q(".inCountry").value), w = parseFloat(q(".inWeight").value);
  if (!c) { await msgbox("提示", "请填写有效的目的国（中文名或二字码，如 美国 / US）。"); return q(".inCountry").focus(); }
  if (!(w > 0)) { await msgbox("提示", "请填写大于 0 的重量。"); return q(".inWeight").focus(); }
  const ids = [...t.sel].filter(id => { const p = plat(id); return p && p.implemented && p.enabled && p.state !== "no_account"; });
  if (!ids.length) return msgbox("提示", "请至少勾选一个可用平台。");
  const body = { country: c.code, weight: w, postcode: q(".inPost").value, length: +q(".inL").value || 0, width: +q(".inW").value || 0, height: +q(".inH").value || 0, platforms: ids };
  t.busy = true; q(".bRun").disabled = true; updateToolbar();
  q(".pstrip").innerHTML = ids.map(id => `<div class="pcell"><span class="dot" style="margin:0"></span><b>${esc(plat(id).name)}</b><span class="m">查询中…</span></div>`).join("");
  sbMsg(`正在并发查询 ${ids.length} 个平台：${c.name} ${w}kg …`); const t0 = performance.now();
  try {
    const r = await jsend("/api/quote", body); t.ms = performance.now() - t0;
    showResult(t, r); sbMsg(`查询完成：${c.name} ${w}kg，共 ${r.rows.length} 条渠道，用时 ${(t.ms / 1000).toFixed(1)} 秒`);
    refreshPlatforms(); refreshHist();
  } catch (e) { q(".pstrip").innerHTML = ""; sbMsg("查询失败"); await msgbox("查询失败", e.message); }
  t.busy = false; q(".bRun").disabled = false; updateToolbar(); updateStatus();
}

// ---------------- 账号管理标签页 ----------------
const isAdmin = () => !window.__web || (S.meta && S.meta.role === "admin");
const needAdmin = () => { if (isAdmin()) return false; msgbox("需要管理员权限", "平台账号、导入 / 导出账号、连接测试和用户管理只有管理员可以操作。\n\n你可以直接使用「查询」。"); return true; };
function openAccounts(selId) {
  if (needAdmin()) return;
  const t = single("accounts", "平台账号", (t, p) => {
    p.innerHTML = `<div class="accbar"><button class="btn small bImp">${icon("plug")}批量导入账号…</button><button class="btn small bTpl">下载模板（Excel）</button><button class="btn small bTplC">下载模板（CSV）</button><button class="btn small bExp">导出当前配置…</button><span class="muted">一行一个平台，登录账号 / 开放平台 API 都支持</span></div>
    <div class="gridhost" style="flex:none;height:190px;display:flex;flex-direction:column"></div>
    <div class="acc-body"><div class="acc-top">
      <fieldset class="gb"><legend>账号设置 — <span class="accName"></span></legend><div class="modesw"><span class="ml">授权模式</span><label class="cb"><input type="radio" name="am${t.id}" value="api" class="aMode">开放平台API授权（默认）</label><label class="cb"><input type="radio" name="am${t.id}" value="login" class="aMode">登录账号授权（备用）</label><span class="docbadge"></span></div><div class="envsw" style="display:none"></div><div class="fixedmode" style="display:none"></div><div class="modenote"></div><div class="acc-form"></div>
        <div class="frow" style="margin-top:4px"><label class="cb"><input type="checkbox" class="aEn">启用该平台</label><span style="flex:1"></span>
        <button class="btn def small aSave">保存</button><button class="btn small aTest">${icon("plug")}测试连接</button></div></fieldset>
      <fieldset class="gb"><legend>平台说明</legend><div class="desc"></div></fieldset></div>
      <fieldset class="gb acc-logbox"><legend>操作日志</legend><textarea class="log" readonly></textarea></fieldset>
    </div>`;
    const q = s => $(s, p); t.q = q;
    const log = (m, bad) => { const ta = q(".log"); ta.value += `[${fmtTime(Date.now() / 1000).slice(11)}] ${m}\n`; ta.scrollTop = ta.scrollHeight; };
    t.log = log;
    t.grid = Grid(q(".gridhost"), { sort: { k: "name", d: 1 }, empty: "无平台", onSelect: rows => form(rows[0]),
      cols: [
        { k: "name", t: "平台", w: 90, render: r => `<span class="pb ${r.id}">${esc(r.name)}</span>`, text: r => r.name },
        { k: "auth_mode_label", t: "授权模式", w: 100 },
        { k: "label", t: "状态", w: 130, render: r => `<span class="dot ${r.state}"></span>${esc(r.label)}`, text: r => r.label },
        { k: "enabled", t: "启用", w: 56, align: "c", render: r => r.enabled ? "是" : "否" },
        { k: "system", t: "系统", w: 220 }, { k: "mode_short", t: "对接方式", w: 120, title: r => r.mode },
        { k: "last_ok", t: "最近成功", w: 150, render: r => r.last_ok ? fmtTime(r.last_ok) : '<span class="muted">-</span>', text: r => r.last_ok ? fmtTime(r.last_ok) : "" },
        { k: "env_label", t: "环境", w: 84, render: r => r.auth_mode === "api" && !r.env_only ? `<span class="envb ${r.env === "prod" ? "prod" : "test"}">${r.env === "prod" ? "正式" : "测试"}</span>` : `<span class="muted">${r.env_only ? "仅正式" : "-"}</span>`, text: r => r.env_label },
      ],
      menu: rows => [{ t: "测试连接", f: () => $(".aTest", p).click() }, { t: "刷新状态", f: refreshPlatforms }] });
    let curP = null, curMode = "login", curEnv = "prod", drafts = {};
    const fieldHtml = f => {
      const ph = f.secret && f.saved ? "已保存（留空表示不修改）" : (f.hint || "");
      if (f.type === "select") return `<div class="row"><label>${esc(f.label)}</label><select data-k="${f.key}">${(f.options || []).map(o => `<option value="${esc(o.value)}" ${o.value === (f.value || "") ? "selected" : ""}>${esc(o.label)}</option>`).join("")}</select></div>`;
      return `<div class="row"><label>${esc(f.label)}${f.optional ? '<span class="opt">（可选）</span>' : ""}</label><input data-k="${f.key}" type="${f.secret ? "password" : "text"}" value="${esc(f.value)}" placeholder="${esc(ph)}" autocomplete="off"></div>`;
    };
    function renderMode() {
      const r = curP; if (!r) return;
      const m = r.modes[curMode];
      $$(".aMode", p).forEach(i => i.checked = i.value === curMode);
      const db = q(".docbadge");
      q(".modesw").style.display = r.fixed_mode ? "none" : "";
      q(".fixedmode").style.display = r.fixed_mode ? "" : "none";
      q(".fixedmode").innerHTML = r.fixed_mode ? `<span class="ml">授权模式</span><span class="doc public">${esc(r.auth_mode_label)}</span>` : "";
      if (r.fixed_mode) {
        db.innerHTML = "";
        q(".modenote").textContent = m.note || "";
      } else if (curMode === "api") {
        db.innerHTML = `<span class="doc ${m.doc_status}">${esc(m.doc_status_label)}</span>${m.doc_url ? ` <a href="${esc(m.doc_url)}" target="_blank" rel="noopener" class="doclink">文档</a>` : ""}${m.impl && !m.quote_ok ? ' <span class="doc apply">仅连接测试，运价试算需申请文档</span>' : ""}`;
        q(".modenote").textContent = m.doc_note || "";
      } else {
        db.innerHTML = m.desktop_only ? '<span class="doc apply">仅桌面版 · 网页版不可用</span>' : m.impl ? '<span class="doc public">已实现</span>' : '<span class="doc apply">暂未实现</span>';
        q(".modenote").textContent = m.desktop_only ? "该登录方式需要本机浏览器（含验证码），仅桌面版可用；网页版请切换到「开放平台API授权」。账号已加密保存，桌面版不受影响。" : (m.note || "");
      }
      // 环境：开放平台 API 模式下才有；两套凭据各自保存，切换只是换一套显示，另一套原样保留
      const es = q(".envsw"), showEnv = !r.fixed_mode && curMode === "api";
      es.style.display = showEnv ? "" : "none";
      let fields = m.fields.filter(f => !f.hidden);
      if (showEnv) {
        const ev = (r.envs || []).find(x => x.value === curEnv) || (r.envs || [])[0];
        if (ev) { curEnv = ev.value; fields = ev.fields.filter(f => !f.hidden); }
        es.innerHTML = `<span class="ml">环境</span>${(r.envs || []).map(x => `<label class="cb"><input type="radio" name="ev${t.id}" value="${x.value}" class="aEnv" ${x.value === curEnv ? "checked" : ""}>${esc(x.label)}${x.configured ? "" : '<span class="muted">（未配置）</span>'}</label>`).join("")}${r.env_only ? `<span class="doc public">${esc(r.env_note || "只有正式环境")}</span>` : ""}<span class="envb ${curEnv === "prod" ? "prod" : "test"}">${curEnv === "prod" ? "当前：正式" : "当前：测试"}</span>${ev && ev.url ? `<span class="muted" style="margin-left:8px">接口：${esc(ev.url)}</span>` : ""}${ev && ev.needs_base ? '<span class="muted" style="margin-left:8px">测试环境没有公开地址，请填服务商给的接口域名与测试密钥</span>' : ""}`;
        $$(".aEnv", es).forEach(i => i.onchange = () => { if (i.checked) { stash(); curEnv = i.value; renderMode(); } });
        fields = fields.map(f => drafts[curEnv + ":" + f.key] != null ? { ...f, value: drafts[curEnv + ":" + f.key] } : f);
      }
      q(".acc-form").innerHTML = fields.length ? fields.map(fieldHtml).join("") : `<div class="muted" style="padding:4px 0">${r.fixed_mode ? "公开网站，无需授权，无需填写账号或密钥。" : "该模式无需填写账号。"}</div>`;
      q(".aTest").disabled = false;
    }
    function form(r) {
      const same = curP && r && curP.id === r.id;
      curP = r; if (!r) return;
      if (!same) { curMode = r.auth_mode; curEnv = r.env || "prod"; drafts = {}; }
      q(".accName").textContent = r.name;
      q(".aEn").checked = !!r.enabled;
      renderMode();
      q(".desc").innerHTML = `<div><b>${esc(r.system)}</b></div><div>对接方式：${esc(r.mode)}</div>${r.note ? `<div style="margin-top:4px;color:#8a4b00">${esc(r.note)}</div>` : ""}${r.last_error ? `<div style="margin-top:4px;color:var(--err)">最近错误：${esc(r.last_error)}</div>` : ""}`;
    }
    $$(".aMode", p).forEach(i => i.onchange = () => { if (i.checked) { curMode = i.value; renderMode(); } });
    t.form = form;
    const stash = () => { if (curMode === "api") $$("[data-k]", q(".acc-form")).forEach(i => { drafts[curEnv + ":" + i.dataset.k] = i.value; }); };
    const vals = () => { const v = {}; $$("[data-k]", q(".acc-form")).forEach(i => v[i.dataset.k] = i.value); return v; };
    const save = async () => {
      const envMode = curMode === "api" && !curP.fixed_mode && curP.envs && !curP.env_only;
      stash();
      if (envMode) {      // 另一个环境里填了但还没保存的内容也一并保存（先存其它环境，最后存当前环境并切换到它）
        for (const e of curP.envs.map(x => x.value).filter(x => x !== curEnv)) {
          const v = {}; let any = false;
          for (const k in drafts) if (k.startsWith(e + ":")) { const val = drafts[k]; if (val !== "") { v[k.slice(e.length + 1)] = val; any = true; } }
          if (any) await jsend(`/api/platforms/${curP.id}/account`, { values: v, env: e, auth_mode: curMode }, "PUT");
        }
      }
      await jsend(`/api/platforms/${curP.id}/account`, { values: vals(), enabled: q(".aEn").checked, auth_mode: curMode, ...(envMode ? { env: curEnv } : {}) }, "PUT");
      drafts = {}; await refreshPlatforms();
    };
    q(".aSave").onclick = async () => { if (!curP) return; try { const id = curP.id; await save(); log(`${plat(id).name}：已保存`); sbMsg(`${plat(id).name} 账号已保存`); } catch (e) { log("保存失败：" + e.message); } };
    q(".aTest").onclick = async ev => {
      if (!curP) return; const b = ev.currentTarget, id = curP.id, nm = curP.name; b.disabled = true; log(`${nm}：测试连接中（${curP.fixed_mode ? curP.auth_mode_label : curMode === "api" ? "开放平台API模式" + (curP.env_only ? "" : curEnv === "prod" ? " · 正式环境" : " · 测试环境") : "登录账号模式"}）…登录模式下云途首次登录约 20 秒`); sbMsg(`正在测试 ${nm} 连接…`);
      try { await save(); const r = await jsend(`/api/platforms/${id}/test`, {}); log(`${nm}：${r.ok ? "成功" : "未通过"} — ${r.message}${r.elapsed_ms ? `（${(r.elapsed_ms / 1000).toFixed(1)}s）` : ""}`); sbMsg(`${nm}：${r.message}`); await refreshPlatforms(); }
      catch (e) { log(`${nm}：失败 — ${e.message}`); }
      b.disabled = false;
    };
    q(".bImp").onclick = importAccounts; q(".bTpl").onclick = () => downloadTemplate("xlsx"); q(".bTplC").onclick = () => downloadTemplate("csv"); q(".bExp").onclick = exportAccounts;
    t.sync = () => { const want = (curP && curP.id) || t.want || "yuntu"; t.grid.setRows(S.platforms); const r = S.platforms.find(x => x.id === want) || S.platforms[0]; t.grid.select([S.platforms.find(x => x === r)], true); form(r); };
    t.sync();
  });
  if (selId) { t.want = selId; const r = plat(selId); if (r) { t.grid.select([r], true); t.form(r); } }
  return t;
}

// ---------------- 历史标签页 ----------------
async function viewHistory(id, requery) {
  const h = await api("/api/history/" + id);
  const t = newQuery(h.query);
  if (requery) { runQuery(t); return; }
  showResult(t, { id: h.id, ts: h.ts, query: h.query, platforms: h.platforms, rows: h.rows });
  sbMsg(`已打开历史记录：${fmtTime(h.ts)}`);
}
function openHistory() {
  const t = single("history", "查询历史", (t, p) => {
    p.innerHTML = `<div class="filterbar"><button class="btn small bView">${icon("eye")}查看</button><button class="btn small bAgain">${icon("refresh")}重新查询</button><button class="btn small bCsv">${icon("export")}导出 CSV</button><button class="btn small bDel">${icon("trash")}删除</button><button class="btn small bAll">清空全部</button><button class="btn small bRef">刷新</button><span class="sp hcount"></span></div><div class="gridhost" style="flex:1;display:flex;flex-direction:column"></div>`;
    const q = s => $(s, p); t.q = q;
    const one = () => t.grid.selected()[0];
    const exportCsv = async h => { if (!h) return; const full = await api("/api/history/" + h.id); const qy = full.query; saveFile(`报价_${qy.country}_${qy.weight}kg_${h.id}.csv`, rowsToCsv(full.rows)); };
    const del = async rows => { if (!rows.length) return; if (!await confirmBox("删除记录", `确定删除选中的 ${rows.length} 条查询记录？`)) return; for (const r of rows) await api("/api/history/" + r.id, { method: "DELETE" }); await refreshHist(); };
    t.grid = Grid(q(".gridhost"), {
      multi: true, sort: { k: "ts", d: -1 }, empty: "还没有查询记录", onOpen: h => viewHistory(h.id), onDelete: del,
      cols: [
        { k: "ts", t: "查询时间", w: 150, render: h => fmtTime(h.ts), text: h => fmtTime(h.ts) },
        { k: "country", t: "目的国", w: 120, render: h => esc(`${h.query.country_name || h.query.country} (${h.query.country})`), sortVal: h => h.query.country, text: h => h.query.country_name },
        { k: "weight", t: "重量 (kg)", w: 80, align: "r", sortVal: h => h.query.weight, render: h => h.query.weight, text: h => h.query.weight },
        { k: "post", t: "邮编", w: 80, sortVal: h => h.query.postcode, render: h => esc(h.query.postcode || "-"), text: h => h.query.postcode },
        { k: "plats", t: "平台结果（渠道数）", w: 300, sortable: false, text: () => "", render: h => h.platforms.filter(p => !["pending", "no_account", "skipped"].includes(p.state)).map(p => `<span class="pb ${p.id}" title="${esc(p.message || "")}">${esc(p.name)} ${p.state === "ok" ? p.count : "✗"}</span>`).join(" ") },
        { k: "min_total", t: "最低价 (¥)", w: 100, align: "r", cls: "price", render: h => money(h.min_total), text: h => h.min_total },
        { k: "count", t: "渠道数", w: 70, align: "r" },
      ],
      menu: rows => [{ t: "查看结果", dis: rows.length !== 1, f: () => viewHistory(rows[0].id) }, { t: "重新查询", dis: rows.length !== 1, f: () => viewHistory(rows[0].id, true) }, { t: "导出 CSV…", dis: rows.length !== 1, f: () => exportCsv(rows[0]) }, { sep: 1 }, { t: "删除", f: () => del(rows) }],
    });
    q(".bView").onclick = () => one() && viewHistory(one().id);
    q(".bAgain").onclick = () => one() && viewHistory(one().id, true);
    q(".bCsv").onclick = () => exportCsv(one());
    q(".bDel").onclick = () => del(t.grid.selected());
    q(".bAll").onclick = clearHistory; q(".bRef").onclick = refreshHist;
    t.sync = () => { t.grid.setRows(S.hist); q(".hcount").textContent = `共 ${S.hist.length} 条记录`; };
    t.onShow = refreshHist; t.sync();
  });
  return t;
}
async function clearHistory() {
  if (!await confirmBox("清空历史", "确定清空全部查询历史？此操作不可恢复。")) return;
  await api("/api/history", { method: "DELETE" }); await refreshHist(); sbMsg("查询历史已清空");
}

// ---------------- 关于标签页 ----------------
function openAbout() {
  single("about", "帮助与关于", (t, p) => {
    p.innerHTML = `<fieldset class="gb about"><legend>关于</legend>
      <h3>货代询价助手 FreightHub v${esc(S.meta.version)}</h3>
      <p>把多个货代平台的报价查询统一到一个桌面软件里：输入目的国与重量，并发查询全部已启用平台，在同一张表里排序、筛选、对比、导出。</p>
      <p>本软件<b>只读查询</b>，不会下单，也不会修改任何平台资料。账号密码只保存在本机：<code>${esc(S.meta.data_dir || "data/config.json")}</code></p></fieldset>
      <fieldset class="gb about"><legend>平台对接说明</legend><table>${S.platforms.map(x => `<tr><td><span class="pb ${x.id}">${esc(x.name)}</span></td><td><span class="dot ${x.state}"></span>${esc(x.label)}</td><td>${esc(x.auth_mode_label)}</td><td>${esc(x.mode)}${x.fixed_mode ? "" : `<br><span class="muted">开放平台API：${esc(x.modes.api.doc_status_label)}${x.modes.api.quote_ok ? "" : "（运价试算未公开）"}</span>`}</td></tr>`).join("")}</table></fieldset>
      <fieldset class="gb about"><legend>快捷键</legend><table>
      <tr><td>F5 / Enter</td><td>开始查询</td></tr><tr><td>Ctrl+T / Ctrl+W</td><td>新建 / 关闭标签页</td></tr><tr><td>Ctrl+E</td><td>导出当前结果为 CSV</td></tr>
      <tr><td>Ctrl+C / Ctrl+A</td><td>复制选中行 / 全选</td></tr><tr><td>↑ ↓ Home End</td><td>表格内移动选择</td></tr><tr><td>表格列头</td><td>单击排序，拖动右边缘调整列宽，双击边缘恢复</td></tr><tr><td>右键</td><td>表格行右键菜单（复制 / 导出 / 只看该平台）</td></tr></table></fieldset>`;
  });
}


// ---------------- 账号批量导入 / 导出 ----------------
const b64FromBuf = buf => { let bin = ""; const u = new Uint8Array(buf); for (let i = 0; i < u.length; i += 0x8000) bin += String.fromCharCode.apply(null, u.subarray(i, i + 0x8000)); return btoa(bin); };
async function saveBlob(name, blob) {
  const a = desk.api();
  if (a && a.save_bytes) { const p = await a.save_bytes(name, b64FromBuf(await blob.arrayBuffer())); if (p) sbMsg("已保存：" + p); return !!p; }
  const url = URL.createObjectURL(blob); const l = el("a"); l.href = url; l.download = name; document.body.appendChild(l); l.click(); l.remove(); setTimeout(() => URL.revokeObjectURL(url), 2000);
  sbMsg("已导出：" + name); return true;
}
async function downloadApi(url, fallbackName) {
  const r = await fetch(url); if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch (e) {} throw new Error(m); }
  const name = decodeURIComponent(r.headers.get("X-Filename") || "") || fallbackName;
  return saveBlob(name, await r.blob());
}
const accLog = m => { const t = S.tabs.find(x => x.type === "accounts"); if (t && t.log) t.log(m); };
const ACT = { add: ["新增", "ok"], overwrite: ["覆盖", "warn"], same: ["无变化", "mute"], skip: ["跳过", "mute"], dup: ["重复", "mute"], error: ["错误", "bad"] };
const showModal = html => { const m = $("#modal"); m.innerHTML = html; m.style.display = "flex"; return m; };
const hideModal = () => { const m = $("#modal"); m.style.display = "none"; m.innerHTML = ""; };

function pickFile(accept) {
  return new Promise(res => {
    const i = el("input"); i.type = "file"; i.accept = accept; i.style.display = "none"; document.body.appendChild(i);
    i.onchange = () => { const f = i.files[0]; i.remove(); res(f || null); };
    i.addEventListener("cancel", () => { i.remove(); res(null); });
    i.click();
  });
}
async function importAccounts() {
  if (needAdmin()) return;
  const f = await pickFile(".xlsx,.csv,.txt,.tsv"); if (!f) return;
  let pv;
  try { pv = await jsend("/api/accounts/import/preview", { filename: f.name, data: b64FromBuf(await f.arrayBuffer()) }); }
  catch (e) { return msgbox("读取文件失败", e.message); }
  const okRows = pv.rows.filter(r => r.action === "add" || r.action === "overwrite");
  const fieldTxt = r => r.fields.length ? r.fields.map(x => `<span class="ifld${x.changed ? " chg" : ""}" title="${x.changed ? (x.had ? "将覆盖原值" : "新填写") : "与现有相同"}">${esc(x.label)}：${x.secret ? "<i>已填写</i>" : esc(String(x.value).length > 42 ? String(x.value).slice(0, 40) + "…" : x.value)}</span>`).join("") : "";
  const sum = Object.entries(pv.summary).map(([k, n]) => `${(ACT[k] || [k])[0]} ${n}`).join("　");
  const m = showModal(`<div class="dlg wide"><div class="dh">导入账号 — 预览　<span class="muted">${esc(f.name)}</span></div>
    <div class="db imp"><div class="impsum">共 ${pv.rows.length} 行：${esc(sum)}。勾选要写入的行，确认后才会保存（密码/密钥不在此显示）。</div>
    <div class="impscroll"><table class="imptbl"><thead><tr><th style="width:28px"><input type="checkbox" id="impAll"></th><th>行</th><th>平台</th><th>授权方式</th><th>动作</th><th>将写入的字段</th><th>说明</th></tr></thead><tbody>
    ${pv.rows.map(r => { const a = ACT[r.action] || [r.action, "mute"]; const ok = r.action === "add" || r.action === "overwrite"; return `<tr class="${r.action}"><td><input type="checkbox" class="impSel" data-i="${r.i}" ${ok ? "checked" : "disabled"}></td><td>${r.row}</td><td>${esc(r.name || r.platform_text)}</td><td>${esc(r.mode_label)}</td><td><span class="itag ${a[1]}">${a[0]}</span></td><td>${fieldTxt(r)}</td><td class="imsg">${esc(r.message)}</td></tr>`; }).join("")}
    </tbody></table></div>
    <label class="cb" style="margin-top:8px"><input type="checkbox" id="impTest" checked>导入后自动逐个测试连接（每个平台只测一次）</label></div>
    <div class="df"><button class="btn" id="impCancel">取消</button><button class="btn def" id="impOk" ${okRows.length ? "" : "disabled"}>确认导入</button></div></div>`);
  const all = $("#impAll", m); all.checked = okRows.length > 0;
  all.onchange = () => $$(".impSel:not(:disabled)", m).forEach(c => c.checked = all.checked);
  $("#impCancel", m).onclick = hideModal;
  $("#impOk", m).onclick = async () => {
    const rows = $$(".impSel:checked", m).map(c => +c.dataset.i); const doTest = $("#impTest", m).checked;
    if (!rows.length) return msgbox("提示", "没有勾选任何行。");
    let res;
    try { res = await jsend("/api/accounts/import/apply", { batch: pv.batch, rows }); } catch (e) { hideModal(); return msgbox("导入失败", e.message); }
    S.platforms = res.platforms;
    const nm = r => `${esc(r.name)}`;
    const mm = showModal(`<div class="dlg wide"><div class="dh">导入完成 — 已保存 ${res.results.length} 项${doTest ? "，正在测试连接" : ""}</div><div class="db imp"><div class="impscroll"><table class="imptbl"><thead><tr><th>平台</th><th>当前授权方式</th><th>保存</th><th>连接测试</th></tr></thead><tbody>
      ${res.results.map(r => `<tr><td>${nm(r)}</td><td>${r.mode === "api" ? "开放平台API" : "登录账号"}${r.active ? "" : "（已存，未启用）"}</td><td><span class="itag ok">${(ACT[r.action] || [r.action])[0]}</span></td><td class="tres" data-i="${r.i}">${r.testable && doTest ? '<span class="muted">排队中…</span>' : '<span class="muted">—</span>'}</td></tr>`).join("")}</tbody></table></div><div class="impsum" id="impFin"></div></div>
      <div class="df"><button class="btn def" id="impClose" disabled>关闭</button></div></div>`);
    res.results.forEach(r => accLog(`${r.name}：已导入（${r.mode === "api" ? "开放平台API" : "登录账号"}，${(ACT[r.action] || [r.action])[0]}）`));
    let okN = 0, badN = 0;
    if (doTest) {
      await Promise.all(res.results.filter(r => r.testable).map(async r => {
        const c = $(`.tres[data-i="${r.i}"]`, mm); c.innerHTML = '<span class="muted">测试中…</span>';
        try {
          const t = await jsend(`/api/platforms/${r.pid}/test`, {});
          if (t.ok) okN++; else badN++;
          c.innerHTML = `<span class="itag ${t.ok ? "ok" : "bad"}">${t.ok ? "成功" : "未通过"}</span> <span class="imsg">${esc(t.message || "")}${t.elapsed_ms ? `（${(t.elapsed_ms / 1000).toFixed(1)}s）` : ""}</span>`;
          accLog(`${r.name}：测试${t.ok ? "成功" : "未通过"} — ${t.message}`);
        } catch (e) { badN++; c.innerHTML = `<span class="itag bad">失败</span> <span class="imsg">${esc(e.message)}</span>`; }
      }));
      $(".dh", mm).textContent = `导入完成 — 已保存 ${res.results.length} 项，测试完成`;
      $("#impFin", mm).textContent = `测试完成：成功 ${okN}，未通过 ${badN}。失败的平台可在「平台账号」页修改后重试。`;
    } else $("#impFin", mm).textContent = "已保存。可在「平台账号」页点「测试连接」，或菜单 平台→测试全部平台连接。";
    await refreshPlatforms();
    const cb = $("#impClose", mm); cb.disabled = false; cb.onclick = hideModal; cb.focus();
  };
}
async function exportAccounts() {
  if (needAdmin()) return;
  const m = showModal(`<div class="dlg"><div class="dh">导出当前账号配置</div><div class="db"><div class="frow"><span class="ml">格式</span><label class="cb"><input type="radio" name="xf" value="xlsx" checked>Excel (.xlsx)</label><label class="cb"><input type="radio" name="xf" value="csv">CSV</label></div>
    <div class="frow" style="margin-top:8px"><span class="ml">密钥/密码</span><label class="cb"><input type="radio" name="xs" value="none" checked>不导出（留空，推荐）</label><label class="cb"><input type="radio" name="xs" value="mask">打码（如 ab****yz）</label></div>
    <div class="muted" style="margin-top:8px">导出的表格格式与导入模板相同；打码或留空的密码重新导入时不会覆盖已有值。</div></div>
    <div class="df"><button class="btn" id="exCancel">取消</button><button class="btn def" id="exOk">导出</button></div></div>`);
  $("#exCancel", m).onclick = hideModal;
  $("#exOk", m).onclick = async () => {
    const fmt = $('input[name="xf"]:checked', m).value, sec = $('input[name="xs"]:checked', m).value; hideModal();
    try { await downloadApi(`/api/accounts/export?fmt=${fmt}&secrets=${sec}`, "账号配置导出." + fmt); } catch (e) { msgbox("导出失败", e.message); }
  };
}
const downloadTemplate = fmt => downloadApi(`/api/accounts/template?fmt=${fmt}`, "账号导入模板." + fmt).catch(e => msgbox("下载失败", e.message));

// ---------------- 菜单栏 / 工具栏 ----------------
const isQ = () => { const t = cur(); return t && t.type === "query"; };
const cmd = {
  new: () => newQuery(), run: () => runQuery(), reset: () => isQ() && cur().q(".bReset").click(),
  export: () => isQ() && cur().grid.view.length && exportRows(cur(), cur().grid.view),
  exportSel: () => isQ() && exportRows(cur(), cur().grid.selected(), true),
  copy: () => isQ() && copyRows(cur(), cur().grid.selected()),
  selAll: () => isQ() && cur().grid.select(cur().grid.view),
  importAcc: importAccounts, exportAcc: exportAccounts, tplXlsx: () => downloadTemplate("xlsx"), tplCsv: () => downloadTemplate("csv"),
  accounts: () => openAccounts(), history: () => openHistory(), about: () => openAbout(),
  refresh: async () => { await refreshPlatforms(); await refreshHist(); sbMsg("状态已刷新"); },
  clearFilter: () => isQ() && clearFilter(cur()), close: () => cur() && closeTab(cur().id),
  resetCols: () => isQ() && cur().grid.resetWidths(),
  clearHist: clearHistory, exit: () => $("#wClose").click(),
  logout: async () => { try { await jsend("/api/auth/logout", {}); } catch (e) {} location.href = "/login"; },
  users: () => openUsers(), passwd: () => openPasswd(),
  quick: () => quickPopup(""), shellSettings: () => openShellSettings(),
  testAll: async () => {
    if (needAdmin()) return;
    const t = openAccounts(); const ids = S.platforms.filter(p => p.testable && p.has_account).map(p => p.id);
    for (const id of ids) { t.grid.select([plat(id)]); await new Promise(r => setTimeout(r, 50)); $(".aTest", t.panel).click(); await new Promise(r => { const iv = setInterval(() => { if (!$(".aTest", t.panel).disabled) { clearInterval(iv); r(); } }, 400); }); }
  },
};
// ---------------- 网页模式：用户管理 / 修改密码 ----------------
function openPasswd() {
  const m = showModal(`<div class="dlg"><div class="dh">修改我的密码（仅本软件登录密码）</div><div class="db" style="white-space:normal">
    <div class="frow"><span class="ml">原密码</span><input type="password" id="pwOld" autocomplete="current-password"></div>
    <div class="frow" style="margin-top:6px"><span class="ml">新密码</span><input type="password" id="pwNew" autocomplete="new-password" placeholder="至少 8 位"></div>
    <div class="frow" style="margin-top:6px"><span class="ml">确认新密码</span><input type="password" id="pwNew2" autocomplete="new-password"></div>
    <div id="pwErr" style="color:#c42b1c;margin-top:6px;min-height:18px"></div></div>
    <div class="df"><button class="btn" id="pwCancel">取消</button><button class="btn def" id="pwOk">确定</button></div></div>`);
  $("#pwCancel", m).onclick = hideModal;
  $("#pwOk", m).onclick = async () => {
    const o = $("#pwOld", m).value, n = $("#pwNew", m).value, n2 = $("#pwNew2", m).value, er = $("#pwErr", m);
    if (n !== n2) { er.textContent = "两次输入的新密码不一致"; return; }
    try { await jsend("/api/auth/password", { old: o, new: n }); hideModal(); sbMsg("密码已修改（其他设备上的登录已失效）"); }
    catch (e) { er.textContent = e.message; }
  };
  $("#pwOld", m).focus();
}
function askPassword(title) {
  return new Promise(res => {
    const prev = $("#modal").innerHTML;
    const m2 = el("div", "dlg"); m2.style.cssText = "position:fixed;left:50%;top:40%;transform:translate(-50%,-50%);z-index:100000";
    m2.innerHTML = `<div class="dh">${esc(title)}</div><div class="db"><input type="password" id="apw" autocomplete="new-password" placeholder="至少 8 位" style="width:100%;box-sizing:border-box"></div><div class="df"><button class="btn" id="apC">取消</button><button class="btn def" id="apO">确定</button></div>`;
    document.body.appendChild(m2);
    const done = v => { m2.remove(); res(v); };
    $("#apC", m2).onclick = () => done(null); $("#apO", m2).onclick = () => done($("#apw", m2).value);
    $("#apw", m2).onkeydown = e => { if (e.key === "Enter") done(e.target.value); if (e.key === "Escape") done(null); };
    $("#apw", m2).focus();
  });
}
async function openUsers() {
  if (needAdmin()) return;
  const draw = list => {
    const m = showModal(`<div class="dlg wide" style="width:720px"><div class="dh">用户管理</div><div class="db imp"><div class="impscroll" style="max-height:40vh"><table class="imptbl" id="usrTbl"><thead><tr><th>用户名</th><th>角色</th><th>状态</th><th>最近登录</th><th>操作</th></tr></thead><tbody>
      ${list.map(u => `<tr data-u="${esc(u.username)}"><td>${esc(u.username)}${u.username === S.meta.user ? "（我）" : ""}</td><td>${u.role === "admin" ? "管理员" : "普通用户"}</td><td>${u.disabled ? "已停用" : "正常"}</td><td>${u.last_login ? new Date(u.last_login * 1000).toLocaleString() : "—"}</td>
      <td><button class="btn small uRole">${u.role === "admin" ? "设为普通用户" : "设为管理员"}</button> <button class="btn small uPw">重置密码</button> <button class="btn small uDis">${u.disabled ? "启用" : "停用"}</button> <button class="btn small uDel">删除</button></td></tr>`).join("")}</tbody></table></div>
      <div class="frow" style="margin-top:10px"><span class="ml">新增用户</span><input type="text" id="nuName" placeholder="用户名" style="width:130px"> <input type="password" id="nuPw" placeholder="初始密码（≥8 位）" autocomplete="new-password" style="width:150px"> <select id="nuRole"><option value="user">普通用户</option><option value="admin">管理员</option></select> <button class="btn small def" id="nuAdd">添加</button></div>
      <div id="usrErr" style="color:#c42b1c;margin-top:6px;min-height:18px"></div></div>
      <div class="df"><button class="btn def" id="usrClose">关闭</button></div></div>`);
    $("#usrClose", m).onclick = hideModal;
    const act = async fn => { try { draw(await fn()); } catch (e) { $("#usrErr", m).textContent = e.message; } };
    $("#nuAdd", m).onclick = () => act(() => jsend("/api/users", { username: $("#nuName", m).value.trim(), password: $("#nuPw", m).value, role: $("#nuRole", m).value }));
    $$("#usrTbl tbody tr", m).forEach(tr => {
      const n = tr.dataset.u, u = list.find(x => x.username === n);
      $(".uRole", tr).onclick = () => act(() => jsend("/api/users/" + encodeURIComponent(n), { role: u.role === "admin" ? "user" : "admin" }, "PUT"));
      $(".uDis", tr).onclick = () => act(() => jsend("/api/users/" + encodeURIComponent(n), { disabled: !u.disabled }, "PUT"));
      $(".uPw", tr).onclick = async () => { const pw = await askPassword(`为 ${n} 设置新密码`); if (pw) act(() => jsend("/api/users/" + encodeURIComponent(n), { password: pw }, "PUT")); };
      $(".uDel", tr).onclick = async () => { if (await confirmBox("删除用户", `确定删除用户 ${n}？`)) act(async () => { hideModal(); return api("/api/users/" + encodeURIComponent(n), { method: "DELETE" }); }); };
    });
  };
  try { draw(await api("/api/users")); } catch (e) { msgbox("读取用户失败", e.message); }
}
function buildUserChip() {
  const c = el("div", "userchip"); c.id = "userChip";
  c.innerHTML = `<span class="uname">${esc(S.meta.user)}</span><span class="urole">${S.meta.role === "admin" ? "管理员" : "普通用户"}</span><button id="uLogout" class="ulogout">退出登录</button>`;
  $("#titlebar").appendChild(c); $("#uLogout").onclick = cmd.logout;
}
const MENUS = () => [
  { t: "文件", items: [{ t: "新建查询", k: "Ctrl+T", f: cmd.new }, { t: "关闭当前标签", k: "Ctrl+W", f: cmd.close }, { sep: 1 },
    { t: "导入账号…（Excel / CSV）", k: "Ctrl+I", f: cmd.importAcc }, { t: "下载导入模板（Excel）…", f: cmd.tplXlsx }, { t: "下载导入模板（CSV）…", f: cmd.tplCsv }, { t: "导出当前账号配置…", f: cmd.exportAcc }, { sep: 1 },
    { t: "导出全部结果为 CSV…", k: "Ctrl+E", dis: !isQ() || !cur().grid.view.length, f: cmd.export }, { t: "导出选中行为 CSV…", dis: !isQ() || !cur().grid.selected().length, f: cmd.exportSel }, { sep: 1 }, window.__web ? { t: "退出登录", f: cmd.logout } : { t: "退出", k: "Alt+F4", f: cmd.exit }] },
  { t: "查询", items: [{ t: "开始查询", k: "F5", dis: !isQ(), f: cmd.run }, { t: "重置条件", dis: !isQ(), f: cmd.reset }, { t: "清除筛选", dis: !isQ(), f: cmd.clearFilter }, { sep: 1 }, { t: "快速询价浮窗", k: (S.shell && S.shell.hotkey) || "Ctrl+Alt+Q", f: cmd.quick }, { t: "查询历史", f: cmd.history }] },
  { t: "平台", items: [{ t: "平台账号管理", f: cmd.accounts }, { t: "测试全部平台连接", f: cmd.testAll }, { t: "刷新平台状态", f: cmd.refresh }, { sep: 1 }, ...S.platforms.map(p => ({ t: `${p.name}　${p.label}`, dot: p.state, f: () => openAccounts(p.id) }))] },
  { t: "工具", items: [{ t: "复制选中行", k: "Ctrl+C", dis: !isQ() || !cur().grid.selected().length, f: cmd.copy }, { t: "全选", k: "Ctrl+A", dis: !isQ(), f: cmd.selAll }, { sep: 1 }, ...(window.__web ? [] : [{ t: "快捷键与浮窗设置…", f: cmd.shellSettings }]), { t: "恢复默认列宽", dis: !isQ(), f: cmd.resetCols }, { t: window.__web && !isAdmin() ? "清空我的查询历史…" : "清空查询历史…", f: cmd.clearHist },
    ...(window.__web ? [{ sep: 1 }, { t: "修改我的密码…", f: cmd.passwd }, ...(isAdmin() ? [{ t: "用户管理…", f: cmd.users }] : [])] : [])] },
  { t: "帮助", items: [{ t: "使用说明与快捷键", f: cmd.about }, { t: "平台对接说明", f: cmd.about }, { sep: 1 }, { t: `关于 FreightHub v${S.meta.version || ""}`, f: cmd.about }] },
];
function buildMenubar() {
  const mb = $("#menubar"); mb.innerHTML = "";
  ["文件", "查询", "平台", "工具", "帮助"].forEach((n, i) => {
    const d = el("div", "mb-item", n); d.dataset.m = n;
    const open = () => { closeMenus(); d.classList.add("open"); S.openMenu = n; const r = d.getBoundingClientRect(); popMenu(MENUS()[i].items, r.left, r.bottom); };
    d.onclick = () => (S.openMenu === n ? closeMenus() : open());
    d.onmouseenter = () => { if (S.openMenu && S.openMenu !== n) open(); };
    mb.appendChild(d);
  });
}
const TOOLS = [["new", "新建查询", "new"], ["run", "查询", "run", "primary"], ["reset", "重置", "reset"], "|", ["export", "导出CSV", "export"], ["copy", "复制行", "copy"], "|",
  ["accounts", "平台账号", "user"], ["history", "查询历史", "history"], ["refresh", "刷新状态", "refresh"], "|", ["about", "帮助", "info"]];
function buildToolbar() {
  const tb = $("#toolbar");
  tb.innerHTML = TOOLS.map(x => x === "|" ? '<div class="tbsep"></div>' : `<button class="tb ${x[3] || ""}" data-cmd="${x[0]}" title="${x[1]}">${icon(x[2])}<span>${x[1]}</span></button>`).join("");
  $$(".tb", tb).forEach(b => b.onclick = () => cmd[b.dataset.cmd]());
  const qq = el("div", "qqbox", `<span class="qql">快速询价</span><input id="qqIn" placeholder="如：美国 2kg 30x20x10，Enter 在鼠标处弹出" title="输入「国家 重量 [长x宽x高]」后回车，在鼠标指针处弹出各平台最低价浮窗" autocomplete="off"><button class="tb" id="qqGo" title="弹出快速询价浮窗">${icon("run")}</button>`);
  tb.appendChild(qq);
  $("#qqIn").addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); quickPopup($("#qqIn").value.trim()); } });
  $("#qqGo").onclick = () => quickPopup($("#qqIn").value.trim());
}
// 快速询价浮窗：桌面版由外壳在鼠标处弹出；网页 / 浏览器里退化为一个小窗口
async function quickPopup(text) {
  const a = desk.api();
  try {
    if (a && a.quick_popup) { await a.quick_popup(text); return; }
    await jsend("/api/shell/popup", { text });
  } catch (e) {
    const w = 460, h = 540, l = Math.max(0, (window.screenX || 0) + (window.outerWidth || w) - w - 30), t = (window.screenY || 0) + 90;
    window.open("/quick" + (text ? "?text=" + encodeURIComponent(text) : ""), "fhquick", `popup=yes,width=${w},height=${h},left=${l},top=${t}`);
  }
}
async function openShellSettings() {
  let cur = {}, st = {};
  try { cur = await api("/api/shell/settings"); st = await api("/api/shell/status"); } catch (e) { return msgbox("提示", "读取设置失败：" + e.message); }
  const m = showModal(`<div class="dlg"><div class="dh">快捷键与浮窗设置</div><div class="db" style="white-space:normal">
    <div class="frow"><span class="ml">全局快捷键</span><input id="skKey" value="${esc(cur.hotkey)}" readonly style="width:170px" title="点击后直接按下想要的组合键"><span class="muted">点输入框后按下组合键（需含 Ctrl / Alt / Win）</span></div>
    <div class="frow" style="margin-top:8px"><label class="cb"><input type="checkbox" id="skEn" ${cur.quick_enabled ? "checked" : ""}>启用全局快捷键与托盘图标</label></div>
    <div class="frow" style="margin-top:4px"><label class="cb"><input type="checkbox" id="skAuto" ${cur.popup_autoclose ? "checked" : ""}>浮窗失去焦点时自动关闭</label></div>
    <div class="frow" style="margin-top:4px"><label class="cb"><input type="checkbox" id="skTray" ${cur.close_to_tray ? "checked" : ""}>关闭主窗口时最小化到托盘（快捷键继续可用）</label></div>
    <div id="skSt" style="margin-top:8px;min-height:36px;color:${st.hotkey_ok ? "#107c10" : "#8a4b00"}">${esc(st.message || "")}</div></div>
    <div class="df"><button class="btn" id="skCancel">关闭</button><button class="btn" id="skTry">试一下浮窗</button><button class="btn def" id="skOk">保存</button></div></div>`);
  const inp = $("#skKey", m);
  inp.onkeydown = e => {
    e.preventDefault();
    if (["Control", "Alt", "Shift", "Meta"].includes(e.key)) return;
    const mods = [e.ctrlKey && "Ctrl", e.altKey && "Alt", e.shiftKey && "Shift", e.metaKey && "Win"].filter(Boolean);
    let k = e.code.startsWith("Key") ? e.code.slice(3) : e.code.startsWith("Digit") ? e.code.slice(5) : /^F\d+$/.test(e.code) ? e.code : e.code === "Space" ? "Space" : "";
    if (!k) { $("#skSt", m).textContent = "这个按键不能用作快捷键，请换一个字母、数字或 F 键"; return; }
    inp.value = [...mods, k].join("+");
  };
  $("#skCancel", m).onclick = hideModal;
  $("#skTry", m).onclick = () => { hideModal(); quickPopup(""); };
  $("#skOk", m).onclick = async () => {
    try {
      const r = await jsend("/api/shell/settings", { hotkey: inp.value, quick_enabled: $("#skEn", m).checked, popup_autoclose: $("#skAuto", m).checked, close_to_tray: $("#skTray", m).checked }, "PUT");
      S.shell = r.shell; const ok = r.shell && r.shell.hotkey_ok;
      $("#skSt", m).style.color = ok ? "#107c10" : "#8a4b00"; $("#skSt", m).textContent = (r.shell && r.shell.message) || "已保存";
      sbMsg("快捷键与浮窗设置已保存");
    } catch (e) { $("#skSt", m).style.color = "#c42b1c"; $("#skSt", m).textContent = e.message; }
  };
}
function updateToolbar() {
  const q = isQ(), t = cur();
  $$(".tb").forEach(b => { const c = b.dataset.cmd; b.disabled = ["run", "reset", "export", "copy"].includes(c) ? !q || (c === "run" && t.busy) : false; });
  $("#winSub").textContent = t ? "— " + t.title : "";
}

// ---------------- 左侧树 ----------------
const treeOpen = { q: true, recent: true, p: true, s: true };
function buildTree() {
  const tr = $("#tree"); tr.innerHTML = "";
  const node = (lv, o) => {
    const d = el("div", "tn" + (o.grp ? " grp" : "") + (o.sel ? " sel" : ""));
    d.style.paddingLeft = (4 + lv * 14) + "px"; d.dataset.k = o.k || "";
    d.innerHTML = (o.grp ? `<span class="car">${treeOpen[o.key] ? "▼" : "▶"}</span>` : '<span class="car"></span>') + (o.dot ? `<span class="dot ${o.dot}"></span>` : o.ic ? icon(o.ic) : "") + `<span class="tx">${esc(o.t)}</span>` + (o.sub ? `<span class="sub">${esc(o.sub)}</span>` : "");
    d.onclick = () => { if (o.grp) { treeOpen[o.key] = !treeOpen[o.key]; buildTree(); } else o.f && o.f(); };
    if (o.title) d.title = o.title; tr.appendChild(d);
  };
  node(0, { grp: 1, key: "q", t: "询价中心", ic: "run" });
  if (treeOpen.q) {
    node(1, { t: "新建查询", ic: "new", f: cmd.new });
    node(1, { grp: 1, key: "recent", t: "最近查询", ic: "history" });
    if (treeOpen.recent) {
      S.hist.slice(0, 8).forEach(h => node(2, { t: `${h.query.country_name || h.query.country} ${h.query.weight}kg`, sub: `¥${money(h.min_total)}`, title: fmtTime(h.ts), f: () => viewHistory(h.id) }));
      if (!S.hist.length) node(2, { t: "（暂无）" });
    }
  }
  node(0, { grp: 1, key: "p", t: "货代平台", ic: "box" });
  if (treeOpen.p) S.platforms.forEach(p => node(1, { t: p.name, dot: p.state, sub: p.label, k: "p:" + p.id, title: p.system, f: () => openAccounts(p.id) }));
  node(0, { grp: 1, key: "s", t: "系统", ic: "folder" });
  if (treeOpen.s) { node(1, { t: "平台账号", ic: "user", k: "accounts", f: cmd.accounts }); node(1, { t: "查询历史", ic: "history", k: "history", f: cmd.history }); node(1, { t: "帮助与关于", ic: "info", k: "about", f: cmd.about }); }
  markTree();
}
function markTree() {
  const t = cur(); $$("#tree .tn").forEach(n => n.classList.toggle("sel", !!t && n.dataset.k === t.type && t.type !== "query"));
}
// 左侧栏宽度拖动
$("#vsplit").onmousedown = e => {
  e.preventDefault(); const x0 = e.clientX, w0 = $("#nav").offsetWidth;
  const mv = ev => { $("#nav").style.width = Math.max(140, Math.min(420, w0 + ev.clientX - x0)) + "px"; };
  const up = () => { document.removeEventListener("mousemove", mv); document.removeEventListener("mouseup", up); };
  document.addEventListener("mousemove", mv); document.addEventListener("mouseup", up);
};

// ---------------- 状态栏 ----------------
function updateStatus() {
  $("#sbPlats").innerHTML = S.platforms.map(p => `<span title="${esc(p.name)}：${esc(p.label)}${p.last_error ? "\n最近错误：" + esc(p.last_error) : ""}"><span class="dot ${p.state}"></span>${esc(p.name)}</span>`).join("");
  const t = cur(), r = t && t.type === "query" && t.result;
  $("#sbTime").textContent = r ? `耗时：${(t.ms / 1000).toFixed(1)} 秒` : "耗时：-";
  $("#sbCount").textContent = r ? `记录：${t.grid.view.length} / ${r.rows.length}` : "记录：0";
  $("#sbVer").textContent = `v${S.meta.version || ""}`;
  $("#winSub").textContent = t ? "— " + t.title : "";
}

// ---------------- 数据刷新 ----------------
async function refreshPlatforms() {
  S.platforms = await api("/api/platforms");
  S.tabs.forEach(t => { if (t.type === "query") t.platCtl(); if (t.type === "accounts") t.sync(); });
  buildTree(); updateStatus();
}
async function refreshHist() {
  S.hist = await api("/api/history");
  const h = S.tabs.find(t => t.type === "history"); h && h.sync();
  buildTree();
}

// ---------------- 快捷键 ----------------
document.addEventListener("keydown", e => {
  const k = e.key.toLowerCase();
  if (e.key === "F5") { e.preventDefault(); cmd.run(); }
  else if ((e.ctrlKey || e.metaKey) && k === "t") { e.preventDefault(); cmd.new(); }
  else if ((e.ctrlKey || e.metaKey) && k === "w") { e.preventDefault(); cmd.close(); }
  else if ((e.ctrlKey || e.metaKey) && k === "i") { e.preventDefault(); cmd.importAcc(); }
  else if ((e.ctrlKey || e.metaKey) && k === "e") { e.preventDefault(); cmd.export(); }
});

// ---------------- 初始化 ----------------
(async () => {
  const boot = $("#boot");
  try {
    document.body.classList.add("ready");
    S.meta = await api("/api/meta"); S.countries = S.meta.countries;
    window.fhTrial && window.fhTrial(S.meta.trial);
    if (S.meta.mode === "server") { window.__web = true; window.__csrf = S.meta.csrf; document.body.classList.add("web"); }
    $("#countryList").innerHTML = S.countries.map(c => `<option value="${c.name} ${c.code}"></option>`).join("");
    [S.platforms, S.hist] = await Promise.all([api("/api/platforms"), api("/api/history")]);
    buildMenubar(); buildToolbar(); buildTree(); if (window.__web) buildUserChip();
    newQuery(); updateStatus();
    setInterval(() => fetch("/api/ping").catch(() => {}), 5000); fetch("/api/ping");
    window.__hub = { S, cmd, runQuery, viewHistory, quickPopup,
      openQuery: q => { const t = newQuery(q); runQuery(t); return t; },
      openHistory: id => viewHistory(id) };
    api("/api/shell/status").then(x => { S.shell = x; }).catch(() => {});
    const op = new URLSearchParams(location.search).get("open"); if (op) viewHistory(+op).catch(() => {});
    if (boot) { boot.classList.add("hide"); setTimeout(() => boot.remove(), 400); }
  } catch (e) {
    if (boot) { $("#bootMsg").textContent = "启动失败：" + (e && e.message || e); $("#bootSub").textContent = "请关闭后重新打开；详见 %LOCALAPPDATA%\\FreightHub\\data\\freight-hub.log"; boot.querySelector(".sp").style.display = "none"; }
  }
})();
})();
