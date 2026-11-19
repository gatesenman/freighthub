// 「US 1kg」「美国 2kg 30x20x10」→ 国家 / 重量 / 尺寸（与桌面版 app/quick.py 的规则一致）。
(function (g) {
  "use strict";
  const ALIAS = { "英国": "GB", "UK": "GB", "美利坚": "US", "USA": "US", "韩国": "KR", "南韩": "KR", "阿联酋": "AE", "俄国": "RU", "德": "DE", "法": "FR", "日": "JP" };
  const NUM = "(\\d+(?:\\.\\d+)?)";
  const DIM = new RegExp(NUM + "\\s*[x×*X]\\s*" + NUM + "\\s*[x×*X]\\s*" + NUM + "(?:\\s*cm)?", "i");
  const WUNIT = new RegExp(NUM + "\\s*(kg|公斤|千克|g|克|斤)(?![A-Za-z])", "i");

  function resolveCountry(text, countries) {
    const t = String(text || "").trim();
    if (!t) return null;
    const byCode = {}, byName = {};
    countries.forEach(c => { byCode[c.code] = c.name; byName[c.name] = c.code; });
    const up = t.toUpperCase();
    if (byCode[up]) return { code: up, name: byCode[up] };
    for (const tok of t.split(/[\s,，/]+/)) {
      const u = tok.toUpperCase();
      if (byCode[u]) return { code: u, name: byCode[u] };
      if (byName[tok]) return { code: byName[tok], name: tok };
      const a = ALIAS[tok] || ALIAS[u];
      if (a) return { code: a, name: byCode[a] || a };
    }
    if (t.length >= 2) for (const c of countries) if (c.name.includes(t) || t.includes(c.name)) return { code: c.code, name: c.name };
    return null;
  }

  // 失败抛 Error（中文原因）
  function parseQuick(text, countries) {
    let s = String(text || "").trim();
    if (!s) throw new Error("请输入「国家 重量」，例如：美国 2kg 或 US 1.5 30x20x10");
    let L = 0, W = 0, H = 0, weight = null, m = DIM.exec(s);
    if (m) { L = +m[1]; W = +m[2]; H = +m[3]; s = (s.slice(0, m.index) + " " + s.slice(m.index + m[0].length)).trim(); }
    if (/(?<![\d.])-\s*\d/.test(s)) throw new Error("重量必须大于 0（不能是负数）");
    m = WUNIT.exec(s);
    if (m) {
      const v = +m[1], u = m[2].toLowerCase();
      weight = (u === "g" || u === "克") ? v / 1000 : u === "斤" ? v / 2 : v;
      s = (s.slice(0, m.index) + " " + s.slice(m.index + m[0].length)).trim();
    } else {
      m = new RegExp("(?<![A-Za-z])" + NUM + "(?![A-Za-z])").exec(s);
      if (m) { weight = +m[1]; s = (s.slice(0, m.index) + " " + s.slice(m.index + m[0].length)).trim(); }
    }
    const hit = resolveCountry(s, countries);
    if (!hit) throw new Error("没认出目的国「" + (s || "空") + "」：请写国家中文名或两位代码（如 美国 / US）");
    if (weight == null || !isFinite(weight) || weight <= 0) throw new Error("请给出大于 0 的重量，例如 2kg");
    if (weight > 100000 || Math.max(L, W, H) > 10000) throw new Error("重量或尺寸超出范围");
    return { country: hit.code, country_name: hit.name, weight: Math.round(weight * 1000) / 1000, length: L, width: W, height: H };
  }
  g.FH = Object.assign(g.FH || {}, { parseQuick, resolveCountry });
})(typeof self !== "undefined" ? self : window);
