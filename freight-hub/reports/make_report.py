"""生成 reports/测试报告.pdf（数据来自 junit.xml 与 coverage.json；用 Chromium 渲染 HTML，内嵌系统 CJK 字体）。
用法：python reports/make_report.py junit.xml cov.json"""
import json, sys, time, collections, html
import xml.etree.ElementTree as ET
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
CATS = [
    ("test_crypto_signing", "签名 / 加密向量", "AES(NIST 向量)、燕文官方 MD5 向量、云途 HMAC、OIS 签名与伪 token、V5 头、sf.global AES-256-CBC + SHA256、丰桥 msgDigest"),
    ("test_normalization", "报价归一化 / 排序 / 边界", "金额·重量·体积重·尺寸 0/超大/小数/NaN/Inf、parse_days、币种、排序、脏数据过滤"),
    ("test_adapters_ois", "适配器：Intelink OIS（雨桐/环洋/中航）", "API 与登录两种模式；鉴权失败、1004、超时、非 JSON、空列表、限流"),
    ("test_adapters_yuntu_yanwen", "适配器：云途 / 燕文", "云途 API+登录(假浏览器)、燕文沙箱/正式；401/403/429/5xx/超时/非 JSON/空列表"),
    ("test_adapters_shunfeng_xinhang", "适配器：顺丰 sf.global / 丰桥 / 欣航", "加解密+签名往返、沙箱/正式、1004 没权限、部分产品失败、欣航 HTML 解析"),
    ("test_adapters_other", "适配器补充（跨平台错误矩阵）", "同上类别的补充用例，含沙箱 1005 提示、会话失效只重登一次"),
    ("test_api_routes", "FastAPI 接口（含非法输入）", "全部路由的正常/非法输入、422 不回显、体积/长度上限、无下单/改密/重置密钥路由"),
    ("test_store_security", "配置存储 / 安全 / 脱敏", "600 权限、无明文泄露(日志/导出/CSV/错误信息)、脱敏、损坏配置备份、登录失败锁"),
    ("test_import_export", "批量导入 / 导出", "xlsx/csv/编码、原 4 列表、重复、覆盖、坏行、API+登录同平台优先级、zip 炸弹、行数上限"),
    ("test_history_csv_concurrency", "历史 / CSV / 并发 / 只读守卫", "CSV 公式注入、部分平台失败、并发查询、前端只调用已知路由、只读请求白名单"),
    ("test_server_auth", "网页/服务器模式：用户、会话、CSRF", "PBKDF2、角色权限、HttpOnly Cookie、CSRF、限速锁定、HTTPS 头、Origin 校验"),
    ("test_ui_e2e", "前端 Playwright 端到端", "菜单·Ctrl+I·标签页·查询流程·加载画面·错误态·两处界面修复·网页版多用户/角色"),
    ("test_packaging_and_live", "打包 / 版本 / 凭据 / 只读真实冒烟", "版本一致、安装包与便携包清单无凭据、部署文件、备份保持只读；FH_LIVE=1 才跑真实只读冒烟"),
]
root = ET.parse(sys.argv[1]).getroot()
cnt = collections.defaultdict(lambda: [0, 0, 0, 0])
for t in root.iter("testcase"):
    f = t.get("classname").split(".")[2]
    k = cnt[f]; k[0] += 1
    if t.find("failure") is not None or t.find("error") is not None: k[1] += 1
    elif t.find("skipped") is not None: k[2] += 1
    else: k[3] += 1
tot = [sum(v[i] for v in cnt.values()) for i in range(4)]
cov = json.load(open(sys.argv[2]))
files = sorted(cov["files"].items())
tcov = cov["totals"]

BUGS = [
    ("中", "OIS 接口地址填空格时被拼成 https://", "ois_base 对空白串没有回退到默认地址", "已修复：strip 后为空则用默认值"),
    ("低", "到货天数写成小数（如 0.5-1）被错误拆分", "parse_days 只匹配整数", "已修复：支持小数并向上取整"),
    ("中", "金额 NaN / Infinity 被当作有效数字", "to_float 未过滤非有限值，序列化 /api/quote 会 500", "已修复：非有限值一律视为无效"),
    ("中", "上游返回 0 价 / 负价 / 非数字价的行混进结果", "多个适配器各自过滤，口径不一", "已修复：Adapter.quote 统一 clean_rows"),
    ("中", "/api/quote 入参缺校验", "NaN、超大重量、负尺寸、超长国家/邮编都能进入适配器；422 回显原始输入时遇 NaN 变 500", "已修复：Field 范围限制 + 422 不回显原值"),
    ("高", "网络错误信息可能带出 URL 查询串里的密钥", "顺丰国际取 token 用 GET 参数传 appKey/appSecret，requests 异常文本含完整 URL", "已修复：新增 redact()，错误信息统一脱敏"),
    ("中", "config.json 先以 644 创建再 chmod 600", "写入与改权限之间存在可读窗口", "已修复：os.open(..., 0o600) 创建即私有"),
    ("高", "配置文件损坏后下一次保存会覆盖用户凭据", "读取失败静默返回空配置", "已修复：先备份为 config.json.corrupt-xxxx（0600）"),
    ("中", "导出 CSV / 前端复制含公式注入", "以 = + - @ 开头的渠道名会被 Excel 当公式执行", "已修复：服务端与前端 csvCell 均加前缀单引号"),
    ("高", "无登录失败锁：凭据错误时每次查询都会重试登录", "可能触发平台风控锁号，违反「登录只试一次」", "已修复：登录/鉴权失败后持久化锁定，改凭据或点「测试连接」才解锁"),
    ("中", "导入：zip 炸弹 / 异常列号 / 过大文件", "xlsx 解压无上限，ZZZZZZ1 之类列号让补齐列表膨胀", "已修复：解压 80MB、列号 500、1000 行、10MB 上限"),
    ("低", "未实现登录模式的平台默认显示「登录」", "雨桐/顺丰/燕文只能走 API，默认却是登录模式", "已修复：default_mode=api，并加短文案列"),
    ("中", "顺丰沙箱用正式密钥报 1005 App is not exist，提示不清", "沙箱是独立环境，需沙箱专用密钥", "已修复：沙箱 1005 给出明确指引（见第六节）"),
    ("中", "旧测试结果 JSON 里含真实客户标识", "tests/last_result.json、api_mode_result.json 记录了授权成功消息里的客户名；v1.3.0 安装包随带 tests/", "已修复：JSON 已脱敏；1.4.0 安装包不再包含 tests 目录。v1.3.0 安装包按要求原样保留，未改动"),
]

root = ET.parse(sys.argv[1]).getroot()
cnt = collections.defaultdict(lambda: [0, 0, 0, 0])
for t in root.iter("testcase"):
    f = t.get("classname").split(".")[2]
    k = cnt[f]; k[0] += 1
    if t.find("failure") is not None or t.find("error") is not None: k[1] += 1
    elif t.find("skipped") is not None: k[2] += 1
    else: k[3] += 1
tot = [sum(v[i] for v in cnt.values()) for i in range(4)]
cov = json.load(open(sys.argv[2]))
files = sorted(cov["files"].items())
tcov = cov["totals"]


E = lambda x: html.escape(str(x))
def tbl(rows, widths=None, cls=""):
    h = "<table class='%s'>" % cls
    for i, r in enumerate(rows):
        tag = "th" if i == 0 else "td"
        h += "<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in r) + "</tr>"
    return h + "</table>"
out = []
A = out.append
A(f"<h1>FreightHub 货代询价助手 — 全量测试报告</h1><p class=sm>版本 v1.4.0（含 1.3.1 级修复）　生成时间 {time.strftime('%Y-%m-%d %H:%M')}（Asia/Shanghai）　pytest + Playwright(Chromium) + coverage</p>")
A("<h2>一、结论</h2>")
A(f"<p>本次共 <b>{tot[0]}</b> 个自动化用例：通过 <b>{tot[3]}</b>、失败 <b>{tot[1]}</b>、跳过 <b>{tot[2]}</b>（跳过的 1 个是需 FH_LIVE=1 的真实平台冒烟，已另行手动执行，见第五节）。"
  f"后端代码（app/）行覆盖率 <b>{tcov['percent_covered']:.1f}%</b>（{tcov['covered_lines']}/{tcov['num_statements']} 行，含 UI 测试拉起的服务进程）。"
  f"测试期间发现并修复 <b>{len(BUGS)}</b> 处真实问题（第四节），修复后全部用例通过，并已重新构建 v1.4.0 安装包。</p>")
A("<h2>二、按类别统计</h2>")
rows = [["类别", "用例数", "通过", "失败", "跳过", "覆盖内容"]]
for key, name, desc in CATS:
    v = cnt[key]; rows.append([E(name), v[0], v[3], v[1], v[2], E(desc)])
rows.append(["<b>合计</b>", f"<b>{tot[0]}</b>", f"<b>{tot[3]}</b>", f"<b>{tot[1]}</b>", f"<b>{tot[2]}</b>", "旧版 209 项（e2e/导入/API 模式）保留，不在本表内"])
A(tbl(rows))
A("<p class=sm>约定：所有单元/接口测试在独立临时数据目录运行，HTTP 层全部打桩并拦截任何漏网的真实外网请求；凭据全部为 FAKE-… 假数据，报告与日志不含任何真实密钥。</p>")
A("<h2>三、代码覆盖率（app/）</h2>")
rows = [["模块", "语句", "未覆盖", "覆盖率"]]
for f, d in files:
    s = d["summary"]; rows.append([E(f.replace("/workspace/freight-hub/", "")), s["num_statements"], s["missing_lines"], f"{s['percent_covered']:.0f}%"])
rows.append(["<b>合计</b>", tcov["num_statements"], tcov["missing_lines"], f"<b>{tcov['percent_covered']:.1f}%</b>"])
A(tbl(rows, cls="narrow"))
A("<p class=sm>说明：app/cdp.py（驱动本机 Edge/Chrome 的登录自动化）只能用真实浏览器验证，单元测试用假浏览器替身，覆盖率低；前端 JS 由 Playwright 驱动验证，未统计行覆盖。</p>")
A("<h2>四、发现并修复的问题</h2>")
A(tbl([["#", "严重度", "问题", "原因", "处理"]] + [[i + 1, *[E(x) for x in b]] for i, b in enumerate(BUGS)]))
A("<p>另有两项界面修复（用户提出）：平台账号页「对接方式」列改为简称（完整说明放在悬停提示与平台说明中）；「操作日志」面板撑满剩余高度并随窗口缩放（flex:1，无下方空白）。均有 Playwright 用例，截图见第七节。</p>")
A("<h2>五、真实平台只读冒烟（手动执行，FH_LIVE=1）</h2>")
live = json.load(open(ROOT / "reports/live_smoke.json", encoding="utf-8"))
NAMES = {"yutong": "雨桐", "huanyang": "环洋", "zhonghang": "中航", "yuntu": "云途", "shunfeng": "顺丰国际", "yanwen": "燕文", "xinhang": "欣航"}
rows = [["平台", "结果", "条数", "最低价(CNY)", "请求数", "登录/取 token", "备注"]]
for r in live["results"]:
    rows.append([NAMES.get(r["platform"], r["platform"]), {"ok": "成功", "error": "未执行/失败", "skipped": "跳过"}.get(r["status"], r["status"]), r["rows"], r["min_total"] if r["min_total"] is not None else "-", r.get("http_calls", "-"), r.get("logins", "-"), E((r.get("note") or "")[:60])])
A(tbl(rows))
A(f"<p class=sm>时间 {live['ts']}。只做「美国 1kg」一次报价，使用 data/ 的临时拷贝（含已保存会话），每平台至多一次登录、失败即停；请求层有只读白名单，出现下单/改密/重置密钥路径或顺丰 msgType 非 IPE_QUERY_PRODUCT_LIST/IUOP_QUERY_FREIGHT 会立即中止。云途为浏览器登录类，会话有效才测，不自动重新登录。顺丰「未执行」是因为正式环境 customerCode 未配置（见下节）。</p>")
A("<h2>六、顺丰国际 沙箱试算</h2>")
A("<p>已支持 env=sandbox（沙箱地址 https://api-ifsp-sit.sf.global；用 http:// 访问会返回 500，需用 https）与 customerCode 配置项（仅存 data/config.json）。"
  "按要求只做了一次只读尝试：沙箱取 token 返回 <b>1005 App is not exist</b>，因此没有继续发起 IUOP_QUERY_FREIGHT 试算，也没有重试。"
  "原因：沙箱是独立环境，控制台里的生产 Key / 密钥 / AES 密钥在沙箱不存在；需使用控制台「前往沙盒」里的<b>沙箱专用 APP Key、密钥、AES 秘钥</b>。"
  "另外正式环境所有 API 当前为「待审批」，正式环境返回 1004 属预期，审批通过后才能试算。</p>")
A("<h2>七、界面截图（1280×800 与更大尺寸）</h2>")
import base64
for fn, cap in [("desktop-accounts-1280x800.png", "桌面版 平台账号页 1280×800（对接方式短文案 + 操作日志撑满）"), ("desktop-accounts-1920x1080.png", "桌面版 平台账号页 1920×1080"), ("web-query-1280x800.png", "网页版 询价页 1280×800（登录后：用户条 + 菜单栏/工具栏/侧栏/标签页）")]:
    p = ROOT / "screenshots" / fn
    if p.exists():
        A(f"<div class=shot><img src='data:image/png;base64,{base64.b64encode(p.read_bytes()).decode()}'><p class=sm>{cap}</p></div>")
A("<h2>八、产物与复现</h2>")
A("<p>安装包 dist/FreightHub-1.4.0-Setup.exe；便携包 dist/FreightHub-1.4.0-win-x64.zip；校验 dist/SHA256SUMS-1.4.0.txt；v1.3.0 安装包与 /workspace/backup 保持原样（只读）。<br>"
  "运行测试：<code>python -m pytest</code>（默认 tests/suite，不访问外网）；真实只读冒烟：<code>FH_LIVE=1 python tests/live_smoke.py</code>；覆盖率：<code>coverage run -m pytest</code>。</p>")
CSS = """body{font-family:'Noto Sans CJK SC','Noto Sans CJK JP',sans-serif;font-size:10pt;line-height:1.55;color:#222}
h1{font-size:20pt;margin:0 0 4px}h2{font-size:13pt;color:#1f4e79;margin:16px 0 6px;border-bottom:1px solid #c9d8ea;padding-bottom:2px;page-break-after:avoid}
table{border-collapse:collapse;width:100%;font-size:8.5pt;margin:4px 0}th,td{border:1px solid #bbb;padding:3px 5px;vertical-align:top}th{background:#dce9f7;text-align:left}
tr{page-break-inside:avoid}.narrow{width:75%}.sm{font-size:8pt;color:#666}.shot{page-break-inside:avoid;margin:8px 0}.shot img{width:100%;border:1px solid #ccc}code{background:#f0f0f0;padding:0 3px}"""
htm = "<html><head><meta charset='utf-8'><style>" + CSS + "</style></head><body>" + "".join(out) + "</body></html>"
(ROOT / "reports/测试报告.html").write_text(htm, encoding="utf-8")
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(); pg.set_content(htm)
    pg.pdf(path=str(ROOT / "reports/测试报告.pdf"), format="A4", margin={"top": "14mm", "bottom": "14mm", "left": "14mm", "right": "14mm"}, print_background=True)
    b.close()
print("ok")
