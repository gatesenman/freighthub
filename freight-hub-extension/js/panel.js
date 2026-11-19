// 侧边栏主逻辑
(function () {
  "use strict";
  const $ = id => document.getElementById(id);
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = v => (v == null || isNaN(v)) ? "-" : Number(v).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const price = (v, cur) => (!cur || cur === "CNY") ? "¥" + money(v) : esc(cur) + " " + money(v);
  const STATE_LABEL = { error: "失败", login_failed: "登录失败", no_account: "未配置", pending: "待开通", manual: "需人工", skipped: "已停用", ok: "无渠道" };

  const S = {
    cfg: null, api: null, meta: null, countries: [], platforms: [],
    user: "", perms: null, quota: null,
    result: null,            // 最近一次结果 {id, query, platforms, rows}
    sort: { k: "total", d: 1 }, busy: false, runSeq: 0, tab: "quick", hist: null, pending: null,
  };

  // ---------- 视图 ----------
  function show(view) {
    $("vLogin").hidden = view !== "login";
    $("vMain").hidden = view !== "main";
    $("fatal").hidden = view !== "fatal";
    $("btnUser").hidden = view !== "main" || !S.user;
  }
  function conn(state, txt) { $("connDot").className = "dot " + state; $("connTxt").textContent = txt; }
  function fatal(title, msg, withRetry) {
    show("fatal");
    $("fatal").innerHTML = "<b>" + esc(title) + "</b>" + esc(msg) + (withRetry ? '<br><button class="primary" id="btnRetry">重试</button> <button class="hbtn" id="btnFatalOpts">打开设置</button>' : "");
    const r = $("btnRetry"); if (r) r.onclick = init;
    const o = $("btnFatalOpts"); if (o) o.onclick = () => chrome.runtime.openOptionsPage();
  }
  const where = () => S.cfg.backend === "desktop" ? "本机桌面版 · " + S.cfg.desktopUrl.replace(/^https?:\/\//, "") : "网页版 · " + S.cfg.webUrl.replace(/^https?:\/\//, "");

  // ---------- 初始化 / 登录 ----------
  async function init() {
    S.cfg = await FH.getSettings();
    S.api = new FH.Client(S.cfg);
    conn("wait", "正在连接 " + where() + " …");
    try {
      await chrome.runtime.sendMessage({ type: "apply-origin-rule" });
    } catch (e) { /* 后台未就绪也不影响 */ }
    let meta;
    try { meta = await S.api.get("/api/meta"); }
    catch (e) {
      if (e.kind === "auth") { conn("wait", where() + " · 未登录"); S.user = ""; show("login"); $("liUser").focus(); return; }
      conn("bad", where() + " · 未连接");
      return fatal("无法使用", e.message, true);
    }
    await onMeta(meta);
  }
  async function onMeta(meta) {
    S.meta = meta; S.countries = meta.countries || []; S.user = meta.user || "";
    S.perms = meta.perms || null; S.quota = meta.quota || null;
    if (meta.csrf) S.api.csrf = meta.csrf;
    $("countryList").innerHTML = S.countries.map(c => `<option value="${esc(c.name)}">${esc(c.code)}</option>`).join("");
    const b = $("btnUser");
    if (S.user && meta.mode === "server") { b.textContent = S.user + " ⏏"; b.title = "已登录：" + S.user + (meta.role_label ? "（" + meta.role_label + "）" : "") + "，点击退出登录"; } else b.textContent = "";
    show("main");
    b.hidden = !(S.user && meta.mode === "server");
    conn("ok", where() + (S.user ? " · " + S.user : "") + (meta.version ? " · v" + meta.version : ""));
    renderQuota();
    $("bGo").disabled = false;
    if (S.perms && S.perms.indexOf("query.run") < 0) { $("bGo").disabled = true; setBest("fail", "当前账号没有「运行查询」权限，请联系管理员"); }
    loadPlatforms();
    if (S.pending) { const t = S.pending; S.pending = null; runText(t); }
    else await consumePending();
    if (!$("qText").value) { const d = await chrome.storage.local.get("lastInput"); if (d.lastInput) $("qText").value = d.lastInput; }
  }
  function renderQuota() {
    if (S.quota && S.quota.limit) $("ftL").textContent = "v1.0.1 · 本时段剩余查询 " + S.quota.left + "/" + S.quota.limit + " 次"; else $("ftL").textContent = "v1.0.1";
  }
  async function loadPlatforms() {
    S.platforms = [];
    if (S.perms && S.perms.indexOf("platforms.view") < 0) return;
    try { S.platforms = await S.api.get("/api/platforms"); } catch (e) { /* 只用于「查询中」占位，失败不影响 */ }
  }

  $("fLogin").addEventListener("submit", async ev => {
    ev.preventDefault();
    const u = $("liUser").value.trim(), p = $("liPass").value;
    $("liMsg").textContent = ""; $("liGo").disabled = true;
    try {
      const r = await S.api.post("/api/auth/login", { username: u, password: p, remember: $("liRemember").checked });
      $("liPass").value = "";
      if (r && r.csrf) S.api.csrf = r.csrf;
      await init();
    } catch (e) { $("liMsg").textContent = e.message; $("liPass").select(); }
    finally { $("liGo").disabled = false; }
  });
  $("btnUser").addEventListener("click", async () => {
    if (!confirm("退出登录 " + S.user + " ？")) return;
    try { await S.api.post("/api/auth/logout", {}); } catch (e) { /* 忽略 */ }
    S.result = null; S.hist = null; renderAll(); await init();
  });
  $("btnOpts").addEventListener("click", () => chrome.runtime.openOptionsPage());

  // ---------- 查询 ----------
  function setBest(cls, html) { const b = $("qbest"); b.className = "best " + cls; b.innerHTML = html; }
  function fieldsToReq() {
    const code = FH.resolveCountry($("inCountry").value, S.countries);
    if (!code) throw new Error("没认出目的国「" + ($("inCountry").value || "空") + "」：请写国家中文名或两位代码（如 美国 / US）");
    const w = parseFloat($("inWeight").value);
    if (!(w > 0)) throw new Error("请填写大于 0 的重量");
    const n = id => Math.max(0, parseFloat($(id).value) || 0);
    return { country: code.code, weight: w, length: n("inL"), width: n("inW"), height: n("inH") };
  }
  function fillFields(q) {
    $("inCountry").value = q.country_name || q.country || "";
    $("inWeight").value = q.weight || "";
    $("inL").value = q.length || ""; $("inW").value = q.width || ""; $("inH").value = q.height || "";
  }
  async function runText(text) {
    $("qText").value = text;
    try {
      const q = FH.parseQuick(text, S.countries);
      fillFields(q);
      chrome.storage.local.set({ lastInput: text });
      return run();
    } catch (e) { setTab("quick"); S.result = null; renderAll(); setBest("fail", esc(e.message)); }
  }
  async function run() {
    let req;
    try { req = fieldsToReq(); } catch (e) { setTab("quick"); return setBest("fail", esc(e.message)); }
    const seq = ++S.runSeq;
    S.busy = true; $("bGo").disabled = true; setTab("quick");
    const label = (S.countries.find(c => c.code === req.country) || {}).name || req.country;
    setBest("wait", '<i class="spin"></i>正在查询 ' + esc(label) + " " + req.weight + "kg …");
    const usable = S.platforms.filter(p => p.implemented && p.state !== "no_account" && p.state !== "disabled");
    $("qlist").innerHTML = usable.map(p => `<div class="it loading"><span class="pb ${esc(p.id)}">${esc(p.name)}</span><span class="ch"><i class="spin"></i>查询中…</span><span class="pr">-</span><span class="dy"></span></div>`).join("");
    $("qmore").textContent = "";
    try {
      const r = await S.api.post("/api/quote", req);
      if (seq !== S.runSeq) return;
      S.result = r; S.hist = null;
      if (r.query) fillFields(r.query);
      if (r.query) chrome.storage.local.set({ lastInput: `${r.query.country_name || r.query.country} ${r.query.weight}kg` + (r.query.length ? ` ${r.query.length}x${r.query.width}x${r.query.height}` : "") });
      await refreshQuota();
    } catch (e) {
      if (seq !== S.runSeq) return;
      if (e.kind === "auth") { S.result = null; renderAll(); return init(); }
      S.result = null; renderAll();
      setBest("fail", esc(e.message));
      return;
    } finally { if (seq === S.runSeq) { S.busy = false; $("bGo").disabled = !!(S.perms && S.perms.indexOf("query.run") < 0); } }
    renderAll();
  }
  async function refreshQuota() {
    if (!S.quota) return;
    try { const m = await S.api.get("/api/meta"); S.quota = m.quota || null; renderQuota(); } catch (e) { /* 忽略 */ }
  }

  // ---------- 渲染 ----------
  function bestOf(rows) {      // 每个平台的最低价（人民币优先）
    const by = {};
    rows.forEach(r => {
      if (r.total == null) return;
      const k = r.platform, o = by[k];
      const rank = x => (x.currency || "CNY") === "CNY" ? 0 : 1;
      if (!o || rank(r) < rank(o) || (rank(r) === rank(o) && r.total < o.total)) by[k] = r;
    });
    return by;
  }
  function renderQuick() {
    const r = S.result;
    if (!r) { $("qlist").innerHTML = ""; $("qmore").textContent = ""; return; }
    const by = bestOf(r.rows || []);
    const items = (r.platforms || []).map(p => ({ p, best: by[p.id] }));
    const okItems = items.filter(i => i.best);
    let overall = null;
    okItems.forEach(i => { const b = i.best; if (!overall || ((b.currency || "CNY") === "CNY" ? 0 : 1) < ((overall.currency || "CNY") === "CNY" ? 0 : 1) || (((b.currency || "CNY") === "CNY") === ((overall.currency || "CNY") === "CNY") && b.total < overall.total)) overall = b; });
    const q = r.query || {};
    const qtxt = esc(q.country_name || q.country) + " " + q.weight + "kg" + (q.length ? " " + q.length + "×" + q.width + "×" + q.height + "cm" : "");
    if (overall) setBest("", `<span>全部最低 · ${qtxt}</span><b class="p">${price(overall.total, overall.currency)}</b><span class="m">${esc(overall.platform_name)} · ${esc(overall.channel)}${overall.days ? " · " + esc(overall.days) + " 天" : ""}</span>`);
    else setBest("fail", qtxt + "：没有平台返回可用报价（见下方各平台原因）");
    const shown = items.filter(i => i.best || ["error", "login_failed", "manual"].includes(i.p.state) || (i.p.state === "ok"));
    const hidden = items.filter(i => !shown.includes(i));
    shown.sort((a, b) => (a.best ? 0 : 1) - (b.best ? 0 : 1) || (a.best && b.best ? a.best.total - b.best.total : 0));
    $("qlist").innerHTML = shown.map(({ p, best }) => {
      const isMin = best && best === overall;
      if (best) return `<div class="it ok${isMin ? " min" : ""}" data-p="${esc(p.id)}" title="点击查看该平台的全部渠道"><span class="pb ${esc(p.id)}">${esc(p.name)}</span><span class="ch">${esc(best.channel)}${isMin ? '<span class="tg">全场最低</span>' : ""}${(best.currency || "CNY") !== "CNY" ? `<small>币种 ${esc(best.currency)}，未折算人民币</small>` : ""}</span><span class="pr">${price(best.total, best.currency)}</span><span class="dy">${best.days ? esc(best.days) + " 天" : "-"}</span></div>`;
      const cls = p.state === "ok" ? "empty-ok" : "err";
      return `<div class="it ${cls}" title="${esc(p.message)}"><span class="pb ${esc(p.id)}">${esc(p.name)}</span><span class="ch">${esc(p.message || STATE_LABEL[p.state] || p.state)}</span><span class="pr">-</span><span class="dy"></span></div>`;
    }).join("");
    $("qmore").textContent = hidden.length ? "另有 " + hidden.length + " 个平台未参与（未配置账号 / 已停用 / 待开通）：" + hidden.map(i => i.p.name).join("、") : "";
    $("qmore").title = hidden.map(i => i.p.name + "：" + (i.p.message || i.p.state)).join("\n");
  }

  function sortedRows() {
    const r = S.result; if (!r) return [];
    const kw = $("fKey").value.trim().toLowerCase(), pf = $("fPlat").value;
    let rows = (r.rows || []).filter(x => (!pf || x.platform === pf) && (!kw || [x.platform_name, x.channel, x.remark, x.group].join(" ").toLowerCase().includes(kw)));
    const k = S.sort.k, d = S.sort.d;
    rows = rows.slice().sort((a, b) => {
      if (k === "total") { const ca = (a.currency || "CNY") === "CNY" ? 0 : 1, cb = (b.currency || "CNY") === "CNY" ? 0 : 1; if (ca !== cb) return ca - cb; }
      let x = a[k], y = b[k];
      if (x == null) return 1; if (y == null) return -1;
      if (typeof x === "number" && typeof y === "number") return (x - y) * d;
      return String(x).localeCompare(String(y), "zh-CN") * d;
    });
    return rows;
  }
  function renderGrid() {
    const r = S.result;
    $("cntAll").textContent = r ? (r.rows || []).length : 0;
    const sel = $("fPlat"), cur = sel.value;
    sel.innerHTML = '<option value="">全部平台</option>' + (r ? (r.platforms || []).filter(p => p.count).map(p => `<option value="${esc(p.id)}">${esc(p.name)}（${p.count}）</option>`).join("") : "");
    sel.value = cur && sel.querySelector(`option[value="${cur}"]`) ? cur : "";
    document.querySelectorAll("#grid th").forEach(th => { th.classList.remove("sort-asc", "sort-desc"); if (th.dataset.k === S.sort.k) th.classList.add(S.sort.d > 0 ? "sort-asc" : "sort-desc"); });
    const rows = sortedRows(), tb = document.querySelector("#grid tbody");
    const minCny = Math.min(...(r ? r.rows : []).filter(x => (x.currency || "CNY") === "CNY").map(x => x.total));
    tb.innerHTML = rows.map((x, i) => `<tr class="r${x.total === minCny && (x.currency || "CNY") === "CNY" ? " low" : ""}" data-i="${i}"><td><span class="pb ${esc(x.platform)}">${esc(x.platform_name)}</span></td><td>${esc(x.channel)}${x.remark ? `<div class="muted">${esc(x.remark.length > 40 ? x.remark.slice(0, 40) + "…" : x.remark)}</div>` : ""}</td><td class="num">${price(x.total, x.currency)}</td><td class="num">${esc(x.days || "-")}</td></tr>`).join("");
    tb._rows = rows;
    $("gridNote").textContent = r ? `共 ${(r.rows || []).length} 条，当前显示 ${rows.length} 条 · 点击行查看费用明细 · 点击表头排序` : "还没有结果，先去查询";
  }
  document.querySelector("#grid tbody").addEventListener("click", ev => {
    const tr = ev.target.closest("tr.r"); if (!tr) return;
    const nx = tr.nextElementSibling;
    if (nx && nx.classList.contains("det")) { nx.remove(); tr.classList.remove("sel"); return; }
    const x = document.querySelector("#grid tbody")._rows[+tr.dataset.i]; if (!x) return;
    const fees = (x.fees || []).map(f => `<div class="fee"><span>${esc(f.label)}</span><span>${esc(typeof f.value === "number" ? money(f.value) : f.value)}</span></div>`).join("") || '<span class="muted">无费用明细</span>';
    const d = document.createElement("tr"); d.className = "det";
    d.innerHTML = `<td colspan="4">${fees}${x.days ? `<div class="muted">时效 ${esc(x.days)} 天</div>` : ""}${x.track ? `<div class="muted">轨迹：${esc(x.track)}</div>` : ""}${x.remark ? `<div class="muted">${esc(x.remark)}</div>` : ""}</td>`;
    tr.after(d); tr.classList.add("sel");
  });
  document.querySelector("#grid thead").addEventListener("click", ev => {
    const th = ev.target.closest("th"); if (!th) return;
    const k = th.dataset.k; S.sort = S.sort.k === k ? { k, d: -S.sort.d } : { k, d: 1 }; renderGrid();
  });
  $("fKey").addEventListener("input", renderGrid);
  $("fPlat").addEventListener("change", renderGrid);
  $("qlist").addEventListener("click", ev => {
    const it = ev.target.closest(".it.ok"); if (!it) return;
    $("fPlat").value = it.dataset.p; $("fKey").value = ""; setTab("all"); renderGrid();
  });

  async function loadHistory(force) {
    if (S.hist && !force) return renderHistory();
    if (S.perms && S.perms.indexOf("history.view") < 0) { $("histList").innerHTML = ""; $("histNote").textContent = "当前账号没有查看历史的权限"; return; }
    $("histNote").textContent = "加载中…";
    try { S.hist = await S.api.get("/api/history"); } catch (e) { $("histNote").textContent = e.message; return; }
    renderHistory();
  }
  function renderHistory() {
    const h = S.hist || [];
    $("histNote").textContent = h.length ? "最近 " + h.length + " 条查询" : "还没有历史记录";
    $("histList").innerHTML = h.map(x => {
      const q = x.query || {}, d = new Date(x.ts * 1000);
      const t = d.toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
      return `<div class="h" data-id="${x.id}"><span class="t">${esc(q.country_name || q.country)} ${q.weight}kg${q.length ? " · " + q.length + "×" + q.width + "×" + q.height : ""}</span><span class="p">${x.min_total != null ? "¥" + money(x.min_total) : "-"}</span><span class="s">${t}${x.user ? " · " + esc(x.user) : ""} · ${x.count} 条</span><span class="s" style="text-align:right">最低</span></div>`;
    }).join("");
  }
  $("histList").addEventListener("click", async ev => {
    const el = ev.target.closest(".h"); if (!el) return;
    try {
      const r = await S.api.get("/api/history/" + el.dataset.id);
      S.result = r; fillFields(r.query || {}); renderAll(); setTab("quick");
    } catch (e) { setBest("fail", esc(e.message)); setTab("quick"); }
  });
  $("btnHistRefresh").addEventListener("click", () => loadHistory(true));

  function renderAll() { renderQuick(); renderGrid(); if (!S.result) { setBest("idle", "输入目的国和重量，按 Enter 查询各平台最低价"); } }
  function setTab(t) {
    S.tab = t;
    document.querySelectorAll("#tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === t));
    $("pQuick").hidden = t !== "quick"; $("pAll").hidden = t !== "all"; $("pHist").hidden = t !== "hist";
    if (t === "hist") loadHistory(false);
  }
  $("tabs").addEventListener("click", ev => { const b = ev.target.closest("button"); if (b) setTab(b.dataset.tab); });

  // ---------- 输入 ----------
  $("qf").addEventListener("submit", ev => { ev.preventDefault(); if (S.busy) return; run(); });
  $("qText").addEventListener("keydown", ev => {
    if (ev.key !== "Enter") return;
    ev.preventDefault(); ev.stopPropagation(); if (S.busy) return;
    const t = $("qText").value.trim();
    if (t) runText(t); else run();
  });

  // ---------- 右键菜单传来的选中文字 ----------
  async function consumePending() {
    const d = await chrome.storage.session.get("pendingQuery");
    const p = d.pendingQuery;
    if (!p) return;
    await chrome.storage.session.remove("pendingQuery");
    if (Date.now() - p.ts > 60000) return;
    if (!S.meta) { S.pending = p.text; return; }
    runText(p.text);
  }
  chrome.storage.onChanged.addListener((ch, area) => {
    if (area === "session" && ch.pendingQuery && ch.pendingQuery.newValue) consumePending();
    if (area === "local" && (ch.backend || ch.webUrl || ch.desktopUrl)) { S.result = null; S.hist = null; renderAll(); init(); }
  });

  // ---------- 启动 ----------
  FH.mountTrial($("hdBadge"));
  setTab("quick");
  renderAll();
  show("fatal"); $("fatal").hidden = true;
  init();
  window.__fh = { S, run, runText, init };
})();
