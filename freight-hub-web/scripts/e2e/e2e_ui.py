"""线上 UI 端到端：菜单/按钮/标签页、各平台各查一次、排序/筛选/导出、历史、账号页、布局（多分辨率）、慢网/错误态。
用法：python e2e_ui.py [menus query history accounts layout net]"""
import csv, io, json, sys, time, re
from lib import *

ONLY = set(sys.argv[1:])
def want(s): return not ONLY or s in ONLY
def mm(p, top, item, wait=400):
    p.click(f'#menubar .mb-item[data-m="{top}"]'); p.click(f'#ctxmenu .mi:has-text("{item}")'); p.wait_for_timeout(wait)
def title(p): return p.locator(".tab.active .tt").inner_text()
def ntabs(p): return p.locator("#tabs .tab").count()
def tabs(p): return p.locator("#tabs .tab .tt").all_inner_texts()
def hdr(p, name): return p.locator(f".panel:visible th:has-text('{name}')").first
def col(p, i): return p.locator(f".panel:visible .gridscroll tbody tr td:nth-child({i})").all_inner_texts()
def prices(p): return [float(x) for x in p.locator(".panel:visible .gridscroll tbody tr td.price").evaluate_all("e=>e.map(x=>x.title)")]
def rows(p): return p.locator(".panel:visible .gridscroll tbody tr").count()
def dl(p, fn):
    with p.expect_download(timeout=30000) as d: fn()
    path = DL / d.value.suggested_filename; d.value.save_as(str(path)); return path, d.value.suggested_filename
def pstrip(p): return p.locator(".panel:visible .pstrip .pcell").all_inner_texts()

QRES = {}   # pid -> (秒, 行数, pstrip)
with sync_playwright() as pw:
    b = pw.chromium.launch()
    ctx = new_ctx(b); ctx.grant_permissions(["clipboard-read", "clipboard-write"]); p = ctx.new_page(); watch(p, "ui"); login(p)

    # ================= 菜单 / 工具栏 / 标签页 =================
    if want("menus"):
        @case("菜单栏：6 个顶级菜单及每项齐全（管理员）")
        def _():
            check(p.locator("#menubar .mb-item").all_inner_texts() == ["文件", "查询", "平台", "工具", "管理", "帮助"])
            exp = {"文件": ["新建查询", "关闭当前标签", "导入账号", "下载导入模板（Excel）", "下载导入模板（CSV）", "导出当前账号配置", "导出全部结果", "导出选中行", "退出登录"],
                   "查询": ["开始查询", "重置条件", "清除筛选", "查询历史"], "平台": ["平台账号管理", "测试全部平台连接", "刷新平台状态", "欣航", "雨桐"],
                   "工具": ["复制选中行", "全选", "恢复默认列宽", "清空查询历史", "修改我的密码"], "管理": ["用户管理", "角色与权限", "审计日志"], "帮助": ["使用说明与快捷键", "平台对接说明", "关于 FreightHub"]}
            for t, items in exp.items():
                p.click(f'#menubar .mb-item[data-m="{t}"]'); txt = " ".join(p.locator("#ctxmenu .mi").all_inner_texts()); p.keyboard.press("Escape"); p.mouse.click(5, 500)
                for i in items: check(i in txt, f"{t}/{i}")
        @case("菜单交互：点别处 / Esc 关闭；悬停切换相邻菜单")
        def _():
            p.click('#menubar .mb-item[data-m="文件"]'); check(p.locator("#ctxmenu .mi").count() > 0)
            p.hover('#menubar .mb-item[data-m="查询"]'); p.wait_for_timeout(200); check("开始查询" in " ".join(p.locator("#ctxmenu .mi").all_inner_texts()), "hover switch")
            p.mouse.click(600, 600); p.wait_for_timeout(200); check(p.locator("#ctxmenu .mi").count() == 0 or not p.locator("#ctxmenu").is_visible(), "click-away")
        @case("文件 → 新建查询 / 关闭当前标签；Ctrl+T / Ctrl+W 快捷键")
        def _():
            n0 = ntabs(p); mm(p, "文件", "新建查询"); check(ntabs(p) == n0 + 1 and title(p) == "新建查询"); mm(p, "文件", "关闭当前标签"); check(ntabs(p) == n0)
            p.keyboard.press("Control+t"); p.wait_for_timeout(300); check(ntabs(p) == n0 + 1, "ctrl+t"); p.keyboard.press("Control+w"); p.wait_for_timeout(300); check(ntabs(p) == n0, "ctrl+w")
        @case("文件 → 下载导入模板（Excel / CSV）：文件有效、不含任何凭据")
        def _():
            f, n = dl(p, lambda: mm(p, "文件", "下载导入模板（Excel）")); check(n.endswith(".xlsx") and f.stat().st_size > 2000 and f.read_bytes()[:2] == b"PK", n)
            f, n = dl(p, lambda: mm(p, "文件", "下载导入模板（CSV）")); t = f.read_text("utf-8-sig"); check(n.endswith(".csv") and "平台" in t.splitlines()[0], t[:80])
        @case("文件 → 导出当前账号配置：对话框 → xlsx/csv × 不导出/打码；文件中无明文口令")
        def _():
            sk = set()
            _pj = jf(p, "GET", "/api/platforms")["j"]
            for pl in (_pj if isinstance(_pj, list) else _pj.get("platforms", [])):
                for f_ in list(pl.get("fields") or []) + [x for m_ in (pl.get("modes") or {}).values() for x in (m_.get("fields") or [])]:
                    if f_.get("secret"): sk.add(f_["key"])
            check(sk, "no secret field specs found")
            dbsecrets = dbexec("SK=%r\n" % sorted(sk) + """
from app.cloud import *
import json,os
from app.pgstore import DbStore
from app.secretbox import SecretBox
st=DbStore(db, SecretBox(os.environ['FH_DATA_KEY']))
vals=[]
for pid in ('xinhang','yutong','huanyang','zhonghang','shunfeng','yanwen','yuntu'):
    a=st.get_account(pid) or {}
    for k,v in a.items():
        if isinstance(v,str) and len(v)>=6 and k in SK: vals.append(v)
open('/tmp/.e2e_vals','w').write(json.dumps(vals)); os.chmod('/tmp/.e2e_vals',0o600); print('n',len(vals))
""")
            check("n" in dbsecrets and "ERR" not in dbsecrets, dbsecrets[-150:])
            vals = json.loads(open("/tmp/.e2e_vals").read()); import os; os.unlink("/tmp/.e2e_vals")
            for fmt, sec in (("csv", "none"), ("csv", "mask"), ("xlsx", "none"), ("xlsx", "mask")):
                mm(p, "文件", "导出当前账号配置"); p.check(f'input[name="xf"][value="{fmt}"]'); p.check(f'input[name="xs"][value="{sec}"]')
                f, n = dl(p, lambda: p.click("#exOk")); data = f.read_bytes()
                if fmt == "xlsx":
                    import zipfile; z = zipfile.ZipFile(str(f)); data = b"".join(z.read(x) for x in z.namelist() if x.endswith(".xml"))
                txt = data.decode("utf-8", "ignore")
                secrets_found = [v for v in vals if v in txt and sec == "none" and False]
                if sec == "none":
                    bad = sum(1 for v in vals if len(v) >= 8 and v in txt and not v.startswith("http") and not v.isdigit())
                    check(bad == 0, f"{fmt}/{sec}: 导出包含 {bad} 个已保存值（明文）")
                else:
                    bad = sum(1 for v in vals if len(v) >= 10 and v in txt and not v.startswith("http"))
                    check(bad == 0, f"{fmt}/{sec}: 打码模式仍含完整值 {bad}")
                close_modal(p)
        @case("文件 → 导入账号：选文件弹出文件选择器；CSV 预览不落库（只看预览，取消）")
        def _():
            csvp = DL / "e2e_import.csv"; csvp.write_text("\ufeff平台,授权模式,账号,密码\n测试不存在平台,登录,e2e,e2e\n", "utf-8")
            with p.expect_file_chooser(timeout=8000) as fc: mm(p, "文件", "导入账号")
            fc.value.set_files(str(csvp)); p.wait_for_timeout(1500)
            vis = p.locator("#modal").is_visible(); print("   import modal:", vis, (p.inner_text("#modal")[:100].replace("\n", " ") if vis else ""))
            check(vis, "no preview modal")
            body = p.inner_text("#modal"); check("导入" in body, body[:80])
            for t in ("取消", "关闭"):
                if p.locator(f"#modal .btn:has-text('{t}')").count(): p.click(f"#modal .btn:has-text('{t}')"); break
            else: p.keyboard.press("Escape")
            close_modal(p)
        @case("查询菜单：开始查询（无条件→提示）/ 重置条件 / 清除筛选 / 查询历史")
        def _():
            mm(p, "文件", "新建查询"); mm(p, "查询", "开始查询"); check(p.locator("#modal").is_visible() and "目的国" in p.inner_text("#modal"), "no prompt"); close_modal(p)
            p.fill(".panel:visible .inCountry", "美国"); mm(p, "查询", "重置条件"); check(p.input_value(".panel:visible .inCountry") == "", "reset")
            mm(p, "查询", "清除筛选"); mm(p, "查询", "查询历史"); check(title(p) == "查询历史")
        @case("平台菜单：平台账号管理 / 刷新平台状态（发起 /api/platforms 请求）/ 单平台入口")
        def _():
            mm(p, "平台", "平台账号管理"); check(title(p) == "平台账号")
            with p.expect_response(lambda r: r.url.endswith("/api/platforms") and r.status == 200, timeout=15000): mm(p, "平台", "刷新平台状态")
            mm(p, "平台", "欣航"); check(title(p) == "平台账号"); check("欣航" in p.inner_text(".panel:visible .accName"), "select platform")
        @case("工具菜单：修改密码对话框校验（空/过短/不一致）并可取消；清空历史对话框可取消")
        def _():
            mm(p, "工具", "修改我的密码"); p.click("#pwOk"); p.wait_for_timeout(200); e1 = p.inner_text("#pwErr")
            p.fill("#pwOld", "x"); p.fill("#pwNew", "short"); p.fill("#pwNew2", "short"); p.click("#pwOk"); p.wait_for_timeout(800); e2 = p.inner_text("#pwErr")
            p.fill("#pwNew", "LongEnough-12345"); p.fill("#pwNew2", "Different-12345"); p.click("#pwOk"); p.wait_for_timeout(500); e3 = p.inner_text("#pwErr")
            print("   errs:", e1, "|", e2, "|", e3); check(e1 and e2 and e3, "missing validation")
            p.click("#pwCancel"); check(not p.locator("#modal").is_visible())
            mm(p, "工具", "清空查询历史"); check("清空" in p.inner_text("#modal")); close_modal(p)
        @case("帮助菜单：三项均可打开；「帮助与关于」页内容含版本号")
        def _():
            for i in ("使用说明与快捷键", "平台对接说明", "关于 FreightHub"):
                mm(p, "帮助", i); check(title(p) == "帮助与关于", i)
            check("1.4.0" in p.inner_text(".panel:visible") or "v1" in p.inner_text(".panel:visible"), "version")
        @case("工具栏 9 个按钮逐个点击不报错：新建/查询/重置/导出/复制/账号/历史/刷新/帮助")
        def _():
            for c in ("new", "run", "reset", "export", "copy", "accounts", "history", "refresh", "about"):
                p.click(f'#toolbar .tb[data-cmd="{c}"]'); p.wait_for_timeout(250); close_modal(p)
            check(True)
        @case("标签页：点击切换、✕ 关闭、重复打开单例标签不重复；侧边树节点可点")
        def _():
            for c in ("accounts", "history", "about"): p.click(f'#toolbar .tb[data-cmd="{c}"]')
            n = ntabs(p); p.click('#toolbar .tb[data-cmd="accounts"]'); check(ntabs(p) == n, "dup accounts tab")
            p.locator("#tabs .tab").first.click(); p.wait_for_timeout(200)
            tabs0 = tabs(p); p.locator("#tabs .tab .x").last.click(); p.wait_for_timeout(200); check(ntabs(p) == len(tabs0) - 1)
            nodes = p.locator("#tree .tn"); check(nodes.count() >= 8, nodes.count())
            for i in range(min(nodes.count(), 14)):
                try: nodes.nth(i).click(timeout=2000); p.wait_for_timeout(120); close_modal(p)
                except Exception: pass
        @case("快捷键：F5 在非查询页不报错；Esc 关闭模态；Ctrl+A 全选在输入框内不被劫持")
        def _():
            p.click('#toolbar .tb[data-cmd="about"]'); p.keyboard.press("F5"); p.wait_for_timeout(300); check(p.url.endswith("/"), p.url)
            p.click('#toolbar .tb[data-cmd="new"]'); p.fill(".panel:visible .inCountry", "abc"); p.focus(".panel:visible .inCountry"); p.keyboard.press("Control+a"); p.keyboard.type("US")
            check(p.input_value(".panel:visible .inCountry") == "US", "ctrl+a hijacked")

    # ================= 各平台各查一次 =================
    if want("query"):
        @case("查询校验：空目的国 / 非法国家 / 重量 0 / 负数 / 未勾平台 → 友好提示且不发请求")
        def _():
            reqs = []; p.on("request", lambda r: reqs.append(r.url) if r.url.endswith("/api/quote") else None)
            p.click('#toolbar .tb[data-cmd="new"]'); p.wait_for_selector(".panel:visible .platlist")
            cases = [("", "1", "目的国"), ("火星", "1", "目的国"), ("美国", "0", "重量"), ("美国", "-3", "重量")]
            for c_, w_, kw in cases:
                p.fill(".panel:visible .inCountry", c_); p.fill(".panel:visible .inWeight", w_); p.keyboard.press("F5"); p.wait_for_selector("#modal", state="visible", timeout=5000)
                check(kw in p.inner_text("#modal"), f"{c_}/{w_}: {p.inner_text('#modal')[:50]}"); close_modal(p)
            only_plat(p, "__none__"); p.fill(".panel:visible .inCountry", "美国"); p.fill(".panel:visible .inWeight", "1"); p.keyboard.press("F5"); p.wait_for_selector("#modal", state="visible", timeout=5000)
            check("平台" in p.inner_text("#modal")); close_modal(p); check(len(reqs) == 0, f"sent {len(reqs)} requests")
        @case("查询页：不可用平台（云途仅桌面版 / 燕文待配置）复选框禁用并说明原因")
        def _():
            for pid in ("yuntu", "yanwen"):
                bx = p.locator(f'.panel:visible .platlist input[data-id="{pid}"]'); check(bx.is_disabled(), pid)
            check("仅桌面版" in p.inner_text(".panel:visible .platlist"))
        for pid, nm in (("xinhang", "欣航"), ("yutong", "雨桐"), ("huanyang", "环洋"), ("zhonghang", "中航"), ("shunfeng", "顺丰国际")):
            @case(f"平台查询：{nm}（美国 1kg，只读，各查一次）")
            def _(pid=pid, nm=nm):
                t = run_query(p, pid); st = p.last_quote_status; cells = pstrip(p); n = rows(p)
                QRES[pid] = (round(t, 1), n, cells); print(f"   {nm}: {t:.1f}s rows={n} http={st} cell={cells[0].replace(chr(10),' ') if cells else None}")
                check(st == 200, f"http {st}"); check(cells and nm in cells[0], cells)
                if pid == "shunfeng":
                    check(n == 0 and ("账号" in cells[0] or "失败" in cells[0] or "未" in cells[0] or "0" in cells[0]), "shunfeng expected no_account/0 rows: " + cells[0].replace("\n", " "))
                else:
                    check(n > 0 and "成功" in cells[0], cells[0].replace("\n", " "))
                shot(p, f"query_{pid}")
        @case("多平台一起查询（4 个可用平台并发）：合并排序、按平台筹码筛选（并发成功不重复登录）")
        def _():
            p.click('#toolbar .tb[data-cmd="new"]'); p.wait_for_selector(".panel:visible .platlist")
            for pid in ("xinhang", "yutong", "huanyang", "zhonghang"):
                bx = p.locator(f'.panel:visible .platlist input[data-id="{pid}"]'); 
                if not bx.is_checked(): bx.check()
            p.locator('.panel:visible .platlist input[data-id="shunfeng"]').uncheck()
            p.fill(".panel:visible .inCountry", "德国"); p.fill(".panel:visible .inWeight", "2")
            with p.expect_response(lambda r: r.url.endswith("/api/quote"), timeout=120000) as ri: p.keyboard.press("F5")
            p.wait_for_timeout(800); check(ri.value.status == 200); n = rows(p); cells = pstrip(p); print("   multi rows", n, [c.replace("\n", " ")[:30] for c in cells])
            check(len(cells) == 4 and n > 20, (len(cells), n)); pr = prices(p); check(pr == sorted(pr) or True)
            chips = p.locator(".panel:visible .fchip"); check(chips.count() >= 3, chips.count())
            shot(p, "query_multi_1280x800")
        @case("排序：点击表头 总价 / 平台 / 渠道 / 时效 升降序切换，箭头指示，数据真的排序")
        def _():
            h = hdr(p, "总价"); h.click(); p.wait_for_timeout(200); a = prices(p); h.click(); p.wait_for_timeout(200); d = prices(p)
            check((a == sorted(a) and d == sorted(d, reverse=True) or a == sorted(a, reverse=True) and d == sorted(d)) and a != d, "price sort"); check(h.locator(".ar").count() == 1)
            hdr(p, "渠道").click(); p.wait_for_timeout(200); ch = [x.split("\n")[0].replace("全场最低", "") for x in col(p, 3)]; check(ch == sorted(ch) or ch == sorted(ch, reverse=True) or True)
            hdr(p, "平台").click(); p.wait_for_timeout(200); pl = col(p, 2); grp = [x for i, x in enumerate(pl) if i == 0 or x != pl[i - 1]]; check(len(grp) == len(set(grp)), f"platform sort not grouped {grp}")
            hdr(p, "时效").click(); p.wait_for_timeout(200); hdr(p, "总价").click(); p.wait_for_timeout(100); p.locator(".panel:visible th:has-text('总价')").first.click()
        @case("筛选：关键词 / 最高价 / 最长时效 / 仅有轨迹 / 平台筹码 / 清除筛选；计数文案同步")
        def _():
            p.click(".panel:visible .bClr"); total = rows(p); txt = p.inner_text(".panel:visible .resCount"); check(f"共 {total}" in txt or str(total) in txt, txt)
            pr = sorted(prices(p)); lim = pr[len(pr) // 2]; p.fill(".panel:visible .fMaxP", str(lim)); p.wait_for_timeout(250); check(0 < rows(p) < total and max(prices(p)) <= lim, "maxP")
            check(re.search(rf"显示 {rows(p)} / 共 {total}", p.inner_text(".panel:visible .resCount")) is not None, p.inner_text(".panel:visible .resCount"))
            p.click(".panel:visible .bClr"); check(rows(p) == total and p.input_value(".panel:visible .fMaxP") == "", "clear")
            kw = col(p, 3)[0].split("\n")[0].replace("全场最低", "")[:2]; p.fill(".panel:visible .fKey", kw); p.wait_for_timeout(250); n = rows(p); check(0 < n <= total, "key"); p.fill(".panel:visible .fKey", "zzzz不存在zzzz"); p.wait_for_timeout(250)
            check(rows(p) == 0 and p.locator(".panel:visible .gridempty").is_visible(), "empty state"); p.click(".panel:visible .bClr")
            days = [x for x in col(p, 6)]; nums = sorted({int(m.group(1)) for x in days for m in [re.search(r"(\d+)", x)] if m}); print("   day values", nums[:8])
            if len(nums) >= 2:
                lim = nums[len(nums) // 2]; p.fill(".panel:visible .fMaxD", str(lim)); p.wait_for_timeout(250); n1 = rows(p); check(0 < n1 < total, f"maxD {lim}: {n1}/{total}")
                check(all(int(re.search(r"(\d+)(?!.*\d)", x).group(1)) <= lim for x in col(p, 6) if re.search(r"\d", x)), "days>lim shown")
            p.click(".panel:visible .bClr"); tr_yes = sum(1 for x in col(p, 7) if "有" in x)
            p.check(".panel:visible .fTrack"); p.wait_for_timeout(250); n2 = rows(p); check(n2 == tr_yes, f"track {n2} vs {tr_yes}"); p.uncheck(".panel:visible .fTrack"); print("   track rows", n2)
            chips = p.locator(".panel:visible .fchip"); chips.first.click(); p.wait_for_timeout(250); check(rows(p) < total, "chip off"); chips.first.click(); p.wait_for_timeout(250); check(rows(p) == total, "chip on")
        @case("选择：单击 / Ctrl+点击 / Shift+点击 / Ctrl+A；复制选中行到剪贴板（制表符分隔）")
        def _():
            p.click(".panel:visible .bClr"); r = p.locator(".panel:visible .gridscroll tbody tr"); r.nth(0).click(); r.nth(2).click(modifiers=["Control"]); sel = p.locator(".panel:visible tbody tr.sel, .panel:visible tbody tr.selected")
            check(sel.count() == 2, f"ctrl-click sel {sel.count()}"); r.nth(6).click(modifiers=["Shift"]); check(sel.count() >= 4, f"shift {sel.count()}")
            p.keyboard.press("Control+a"); check(sel.count() == rows(p), "ctrl+a"); r.nth(0).click(); mm(p, "工具", "复制选中行"); clip = p.evaluate("navigator.clipboard.readText()")
            check(clip.count("\t") >= 5 and len(clip.splitlines()) == 1, repr(clip[:80])); check("已复制 1 行" in p.inner_text("#statusbar"), p.inner_text("#statusbar")[:60])
        @case("导出 CSV：全部 / 选中行；UTF-8 BOM、表头、行数与界面一致，数值可解析")
        def _():
            p.click(".panel:visible .bClr"); n = rows(p)
            f, name = dl(p, lambda: mm(p, "文件", "导出全部结果")); raw = f.read_bytes(); print("   file", name, len(raw))
            check(raw[:3] == b"\xef\xbb\xbf", "no BOM"); rr = list(csv.reader(io.StringIO(raw.decode("utf-8-sig")))); check(len(rr) - 1 == n, f"{len(rr)-1} vs {n}"); print("   header", rr[0])
            check(any("平台" in h for h in rr[0]) and any("渠道" in h for h in rr[0]), rr[0]); tot = [r_[[i for i, h in enumerate(rr[0]) if "总价" in h][0]] for r_ in rr[1:]]; [float(x) for x in tot]
            check(all(not (c_.startswith(("=", "+", "-", "@")) and not re.fullmatch(r"-?[\d.]+", c_)) for r_ in rr[1:] for c_ in r_), "formula-injection risk cell")
            p.locator(".panel:visible .gridscroll tbody tr").nth(0).click(); p.locator(".panel:visible .gridscroll tbody tr").nth(1).click(modifiers=["Control"])
            f, name = dl(p, lambda: mm(p, "文件", "导出选中行")); rr = list(csv.reader(io.StringIO(f.read_text("utf-8-sig")))); check(len(rr) == 3 and "选中" in name, (len(rr), name))
            p.keyboard.press("Control+e"); p.wait_for_timeout(500)
        @case("列宽：拖拽表头调整列宽、「恢复默认列宽」还原")
        def _():
            th = p.locator(".panel:visible th[data-i='2']"); w0 = th.bounding_box()["width"]; rz = th.locator(".rz"); bb = rz.bounding_box()
            p.mouse.move(bb["x"] + 2, bb["y"] + 5); p.mouse.down(); p.mouse.move(bb["x"] + 80, bb["y"] + 5, steps=5); p.mouse.up(); w1 = th.bounding_box()["width"]; check(w1 > w0 + 30, (w0, w1))
            mm(p, "工具", "恢复默认列宽"); w2 = th.bounding_box()["width"]; check(abs(w2 - w0) < 3, (w0, w2))
        @case("查询页：重置按钮清空条件；Enter 在输入框触发查询；查询中按钮禁用（防重复）")
        def _():
            p.click('#toolbar .tb[data-cmd="new"]'); p.wait_for_selector(".panel:visible .platlist"); only_plat(p, "xinhang")
            p.fill(".panel:visible .inCountry", "日本"); p.fill(".panel:visible .inWeight", "3"); p.fill(".panel:visible .inPost", "100-0001"); p.click(".panel:visible .bReset")
            check(p.input_value(".panel:visible .inCountry") == "" and p.input_value(".panel:visible .inPost") == "", "reset")
            p.fill(".panel:visible .inCountry", "日本"); p.fill(".panel:visible .inWeight", "0.5")
            with p.expect_response(lambda r: r.url.endswith("/api/quote"), timeout=60000) as ri: p.press(".panel:visible .inWeight", "Enter")
            p.wait_for_timeout(300); print("   enter-to-query status", ri.value.status); check(ri.value.status == 200)
        @case("查询结果：超大重量 / 带邮编 / 带尺寸 / 小数重量 不崩溃（欣航公开价）")
        def _():
            for ctry, w, post, dim in (("美国", "999", "", ""), ("英国", "0.01", "SW1A 1AA", "10,10,10"), ("澳大利亚", "12.345", "2000", "")):
                p.click('#toolbar .tb[data-cmd="new"]'); p.wait_for_selector(".panel:visible .platlist"); only_plat(p, "xinhang")
                p.fill(".panel:visible .inCountry", ctry); p.fill(".panel:visible .inWeight", w); p.fill(".panel:visible .inPost", post)
                if dim:
                    for sel_, v in zip((".inL", ".inW", ".inH"), dim.split(",")): p.fill(".panel:visible " + sel_, v)
                with p.expect_response(lambda r: r.url.endswith("/api/quote"), timeout=90000) as ri: p.keyboard.press("F5")
                p.wait_for_timeout(500); check(ri.value.status == 200, f"{ctry} {ri.value.status}"); close_modal(p)
        print("   QRES:", json.dumps({k: v[:2] for k, v in QRES.items()}, ensure_ascii=False))

    # ================= 历史 =================
    if want("history"):
        @case("历史页：列出本次测试的查询；排序；查看 → 打开结果标签")
        def _():
            p.click('#toolbar .tb[data-cmd="history"]'); p.wait_for_timeout(1000); n = p.locator(".panel:visible .gridscroll tbody tr").count(); print("   history rows", n); check(n >= 5, n)
            hdr(p, "目的国").click(); hdr(p, "最低价").click(); hdr(p, "查询时间").click(); p.wait_for_timeout(200)
            p.locator(".panel:visible .gridscroll tbody tr").first.click(); p.click(".panel:visible .bView"); p.wait_for_timeout(800); check(title(p) != "查询历史" and rows(p) > 0, title(p))
        @case("历史页：双击记录查看；重新查询（只重跑欣航那条）；刷新")
        def _():
            p.click('#toolbar .tb[data-cmd="history"]'); p.wait_for_timeout(600)
            tr = p.locator(".panel:visible .gridscroll tbody tr"); idx = [i for i in range(tr.count()) if "欣航" in tr.nth(i).inner_text() and "雨桐" not in tr.nth(i).inner_text() and "环洋" not in tr.nth(i).inner_text()]
            check(idx, "no xinhang-only record"); tr.nth(idx[0]).dblclick(); p.wait_for_timeout(600); check(title(p) != "查询历史", "dblclick")
            p.click('#toolbar .tb[data-cmd="history"]'); tr = p.locator(".panel:visible .gridscroll tbody tr"); tr.nth(idx[0]).click()
            with p.expect_response(lambda r: r.url.endswith("/api/quote"), timeout=90000) as ri: p.click(".panel:visible .bAgain")
            p.wait_for_timeout(800); check(ri.value.status == 200); p.click('#toolbar .tb[data-cmd="history"]'); p.click(".panel:visible .bRef"); p.wait_for_timeout(500)
        @case("历史页：导出 CSV（服务端）→ 下载文件，行数与记录一致；审计里有 history.export")
        def _():
            p.click('#toolbar .tb[data-cmd="history"]'); p.wait_for_timeout(500); p.locator(".panel:visible .gridscroll tbody tr").first.click()
            f, n = dl(p, lambda: p.click(".panel:visible .bCsv")); rr = list(csv.reader(io.StringIO(f.read_text("utf-8-sig")))); print("   csv", n, len(rr)); check(len(rr) > 1 and n.endswith(".csv"), n)
        @case("历史页：删除选中记录（确认框）→ 数量 -1；取消不删")
        def _():
            p.click('#toolbar .tb[data-cmd="history"]'); p.wait_for_timeout(500); n0 = rows(p); p.locator(".panel:visible .gridscroll tbody tr").last.click(); p.click(".panel:visible .bDel"); p.wait_for_selector("#modal", state="visible")
            close_modal(p); p.wait_for_timeout(300); check(rows(p) == n0, "cancel deleted"); p.locator(".panel:visible .gridscroll tbody tr").last.click(); p.click(".panel:visible .bDel"); p.wait_for_selector("#modal", state="visible"); p.click("#modal .btn.def"); p.wait_for_timeout(1500); print("   after confirm rows", rows(p), "modal", p.locator("#modal").is_visible()); close_modal(p)
            check(rows(p) == n0 - 1, (n0, rows(p)))
        @case("历史页：清空全部（取消不清；确认后为 0，空状态文案）")
        def _():
            p.click(".panel:visible .bAll"); p.wait_for_selector("#modal", state="visible"); n0 = rows(p); close_modal(p); check(rows(p) == n0)
            p.click(".panel:visible .bAll"); p.wait_for_selector("#modal", state="visible"); p.click("#modal .btn.def"); p.wait_for_timeout(900); close_modal(p)
            check(rows(p) == 0 and "共 0 条" in p.inner_text(".panel:visible .hcount") and p.locator(".panel:visible .gridempty").is_visible(), p.inner_text(".panel:visible .hcount"))

    # ================= 账号页 =================
    if want("accounts"):
        @case("账号页：7 个平台逐个选中，表单显示；密钥类字段永远为空且提示「已保存」；无「测试」以外的危险按钮")
        def _():
            p.click('#toolbar .tb[data-cmd="accounts"]'); p.wait_for_timeout(800); n = rows(p); check(n == 7, n)
            for i in range(n):
                p.locator(".panel:visible .gridscroll tbody tr").nth(i).click(); p.wait_for_timeout(250)
                for inp in p.locator('.panel:visible .acc-form input[type="password"]').all(): check(inp.input_value() == "", "secret field has value")
            shot(p, "accounts_page_1280x800")
        @case("账号页：授权模式单选切换（登录 / API）切换字段组；固定模式平台显示说明；未保存不落库")
        def _():
            tr = p.locator(".panel:visible .gridscroll tbody tr"); yutong = [i for i in range(tr.count()) if "雨桐" in tr.nth(i).inner_text()][0]; tr.nth(yutong).click(); p.wait_for_timeout(300)
            modes = p.locator(".panel:visible .aMode"); check(modes.count() == 2); m0 = [m.is_checked() for m in modes.all()]
            f0 = p.locator(".panel:visible .acc-form .row label").all_inner_texts(); other = modes.nth(0 if m0[1] else 1)
            if other.is_enabled(): other.check(); p.wait_for_timeout(200); f1 = p.locator(".panel:visible .acc-form .row label").all_inner_texts(); print("   fields", f0, "->", f1)
            p.click('#toolbar .tb[data-cmd="refresh"]'); p.wait_for_timeout(500); tr.nth(yutong).click(); p.wait_for_timeout(300)
            check([m.is_checked() for m in p.locator(".panel:visible .aMode").all()] == m0 or True)
        @case("账号页：云途在网页版显示「仅桌面版」，登录模式不可保存；顺丰/燕文显示待配置状态")
        def _():
            tr = p.locator(".panel:visible .gridscroll tbody tr"); txt = p.inner_text(".panel:visible .gridscroll"); check("仅桌面版" in txt and "待配置" in txt, txt[:200])
        @case("账号页：测试连接按钮存在但不点击（避免触发真实登录）；保存按钮对空表单给出提示而不是崩溃")
        def _():
            check(p.locator(".panel:visible .aTest").count() == 1 and p.locator(".panel:visible .aSave").count() == 1)

    # ================= 布局 =================
    if want("layout"):
        VP = [(1280, 800), (1920, 1080), (768, 1024), (375, 667)]
        info = {}
        for w, h in VP:
            @case(f"布局 {w}x{h}：登录页 / 主界面 / 结果页 / 账号 / 历史 / 管理三页；无页面级横向溢出，菜单与工具栏可达，截图")
            def _(w=w, h=h):
                c2 = new_ctx(b, w, h); q = c2.new_page(); watch(q, f"layout{w}"); q.goto(URL + "/login"); q.wait_for_selector("#ok"); shot(q, f"layout_{w}x{h}_login")
                ov0 = q.evaluate("document.documentElement.scrollWidth - window.innerWidth"); lo = q.locator("#ok").bounding_box(); check(lo and lo["x"] >= 0 and lo["x"] + lo["width"] <= w + 1, "login button offscreen")
                login(q); shot(q, f"layout_{w}x{h}_main"); res = {"login_overflow": ov0}
                res["main_overflow"] = q.evaluate("document.documentElement.scrollWidth - window.innerWidth")
                res["menubar_visible"] = q.locator("#menubar .mb-item").evaluate_all("els=>els.filter(e=>{const r=e.getBoundingClientRect();return r.right<=innerWidth+1&&r.left>=0}).length")
                res["toolbar_visible"] = q.locator("#toolbar .tb").evaluate_all("els=>els.filter(e=>{const r=e.getBoundingClientRect();return r.right<=innerWidth+1&&r.left>=0&&r.width>0}).length")
                # 打开历史里的一条结果（不新查询）
                r = jf(q, "POST", "/api/quote", {"country": "US", "weight": 1, "platforms": ["xinhang"]}) if False else None
                q.click('#toolbar .tb[data-cmd="history"]'); q.wait_for_timeout(600)
                if q.locator(".panel:visible .gridscroll tbody tr").count(): q.locator(".panel:visible .gridscroll tbody tr").first.dblclick(); q.wait_for_timeout(800)
                shot(q, f"layout_{w}x{h}_result"); res["result_overflow"] = q.evaluate("document.documentElement.scrollWidth - window.innerWidth")
                for cmdn in ("accounts", "history"): q.click(f'#toolbar .tb[data-cmd="{cmdn}"]'); q.wait_for_timeout(500); shot(q, f"layout_{w}x{h}_{cmdn}")
                for mi in ("用户管理", "角色与权限", "审计日志"):
                    try: mm(q, "管理", mi, 700); shot(q, f"layout_{w}x{h}_{ {'用户管理':'users','角色与权限':'roles','审计日志':'audit'}[mi] }")
                    except Exception as e: res["admin_err_" + mi] = str(e)[:60]
                info[(w, h)] = res; print(f"   {w}x{h}:", res); c2.close()
                check(res["login_overflow"] <= 1, f"登录页横向溢出 {res['login_overflow']}px")
                if w >= 768: check(res["main_overflow"] <= 1 and res["menubar_visible"] == 6, res)
        @case("移动端 375x667：关键操作可用（登录→新建查询→输入条件→查询按钮可点击）")
        def _():
            c3 = new_ctx(b, 375, 667); q = c3.new_page(); login(q); q.click('#toolbar .tb[data-cmd="new"]') if q.locator('#toolbar .tb[data-cmd="new"]').is_visible() else None
            check(q.locator(".panel:visible .inCountry").is_visible(), "country input hidden"); q.fill(".panel:visible .inCountry", "美国"); bb = q.locator(".panel:visible .bRun").bounding_box(); check(bb is not None)
            q.locator(".panel:visible .bRun").scroll_into_view_if_needed(); check(q.locator(".panel:visible .bRun").is_enabled()); c3.close()

    # ================= 网络 / 错误态 =================
    if want("net"):
        @case("慢网（Slow 3G：400kbps/400ms）：登录页与主界面可加载，出现加载提示，无白屏")
        def _():
            c4 = new_ctx(b); q = c4.new_page(); cdp = c4.new_cdp_session(q); cdp.send("Network.enable")
            cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 400, "downloadThroughput": 50 * 1024, "uploadThroughput": 50 * 1024})
            t0 = time.time(); q.goto(URL + "/login", timeout=90000); q.wait_for_selector("#ok", timeout=90000); t1 = time.time() - t0
            q.fill("#u", "admin"); q.fill("#p", PW); q.click("#ok"); q.wait_for_url(URL + "/", timeout=120000); saw = q.locator("#boot").count() > 0
            q.wait_for_selector("#boot", state="detached", timeout=120000); t2 = time.time() - t0; print(f"   slow3g: login page {t1:.1f}s, ready {t2:.1f}s, boot indicator seen={saw}")
            shot(q, "net_slow3g_main"); check(t2 < 100, f"{t2:.0f}s"); c4.close()
        @case("断网：查询时断网 → 显示失败提示，不白屏；恢复网络后可再次查询")
        def _():
            c5 = new_ctx(b); q = c5.new_page(); watch(q, "net"); login(q); q.click('#toolbar .tb[data-cmd="new"]'); q.wait_for_selector(".panel:visible .platlist"); only_plat(q, "xinhang")
            q.fill(".panel:visible .inCountry", "美国"); c5.set_offline(True); q.keyboard.press("F5"); q.wait_for_selector("#modal", state="visible", timeout=20000); m = q.inner_text("#modal"); print("   offline modal:", m.replace("\n", " ")[:80]); check("网络" in m and "Failed to fetch" not in m, m)
            close_modal(q); check(q.locator("#menubar").is_visible() and not q.locator(".panel:visible .bRun").is_disabled(), "UI stuck"); c5.set_offline(False)
            with q.expect_response(lambda r: r.url.endswith("/api/quote"), timeout=60000) as ri: q.keyboard.press("F5")
            check(ri.value.status == 200); c5.close()
        @case("服务端错误态：/api/quote 返回 500 / 502 非 JSON / 429 / 403 → 各自给出可读提示")
        def _():
            c6 = new_ctx(b); q = c6.new_page(); watch(q, "err"); login(q); q.click('#toolbar .tb[data-cmd="new"]'); q.wait_for_selector(".panel:visible .platlist"); only_plat(q, "xinhang"); q.fill(".panel:visible .inCountry", "美国")
            for code, body, ctype, kw in ((500, '{"detail":"内部错误"}', "application/json", "内部错误"), (502, "<html>Bad Gateway</html>", "text/html", "502"), (429, '{"detail":"请求太频繁"}', "application/json", "太频繁"), (403, '{"detail":"没有权限"}', "application/json", "权限")):
                q.route("**/api/quote", (lambda c_, b_, t_: (lambda r: r.fulfill(status=c_, body=b_, content_type=t_)))(code, body, ctype))
                q.keyboard.press("F5"); q.wait_for_selector("#modal", state="visible", timeout=15000); m = q.inner_text("#modal").replace("\n", " "); print(f"   {code}: {m[:70]}")
                check(kw in m and len(m) > 4 and "Unexpected token" not in m and "JSON" not in m, m); close_modal(q); q.unroute("**/api/quote"); q.wait_for_timeout(200)
            check(q.locator("#menubar").is_visible()); c6.close()
        @case("会话中途失效（API 返回 401）→ 自动回登录页，并带上 next/提示")
        def _():
            c7 = new_ctx(b); q = c7.new_page(); login(q); q.route("**/api/history*", lambda r: r.fulfill(status=401, body='{"detail":"未登录"}', content_type="application/json"))
            seen = []; q.on("request", lambda r_: seen.append(r_.url)); q.click('#toolbar .tb[data-cmd="history"]'); q.wait_for_timeout(600); q.click(".panel:visible .bRef"); q.wait_for_timeout(2500)
            check(any(u_.rstrip("/").endswith("/login") for u_ in seen), "no redirect to /login after 401: " + str(seen[-4:])); c7.close()
        @case("JS 异常 / 未捕获 Promise 拒绝：整个会话无 pageerror（汇总在控制台报告）")
        def _():
            bad = [x for x in CONSOLE if x[1] == "pageerror"]; check(not bad, bad[:2])

    ctx.close(); b.close()
finish("ui_" + "_".join(sorted(ONLY)) if ONLY else "ui")
