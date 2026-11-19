// 试用版水印（v1.0.1 常开，不可关闭）：整页斜向平铺「未授权版本 / 试用」+ 标题栏角标。pointer-events:none，不影响操作。
(function (g) {
  "use strict";
  const TXT = "未授权版本 / 试用";
  g.FH = Object.assign(g.FH || {}, {
    TRIAL_TEXT: TXT,
    mountTrial(badgeHost) {
      if (document.getElementById("trialMark")) return;
      const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="220" height="150"><text x="110" y="80" text-anchor="middle" transform="rotate(-28 110 75)" font-family="Microsoft YaHei,Segoe UI,sans-serif" font-size="20" font-weight="700" fill="rgba(200,32,32,0.17)">' + TXT + "</text></svg>";
      const m = document.createElement("div");
      m.id = "trialMark";
      m.setAttribute("aria-hidden", "true");
      m.style.cssText = 'position:fixed;inset:0;z-index:2147483000;pointer-events:none;background-image:url("data:image/svg+xml;utf8,' + encodeURIComponent(svg) + '");background-repeat:repeat';
      document.body.appendChild(m);
      const b = document.createElement("span");
      b.id = "trialBadge";
      b.textContent = TXT;
      b.title = "当前为未授权的试用版本，仅供演示 / 评估";
      b.style.cssText = "display:inline-block;padding:1px 8px;border:1px solid #c82020;border-radius:3px;background:#fdecec;color:#b01818;font:600 12px 'Microsoft YaHei','Segoe UI',sans-serif;white-space:nowrap";
      (badgeHost || document.body).appendChild(b);
      if (document.title.indexOf("试用") < 0) document.title += "（试用）";
    },
  });
})(self);
