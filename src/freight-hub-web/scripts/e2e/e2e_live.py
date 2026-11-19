import csv, io, sys, time, json, pathlib, statistics
from lib import *

ONLY = set(sys.argv[1:])
def want(s): return not ONLY or s in ONLY
PLATS = ["xinhang", "yutong", "huanyang", "zhonghang"]   # 有凭据/公开可查的平台；顺丰缺 customerCode，燕文无凭据，云途登录仅桌面版

def menu_click(p, top, item):
    p.click(f'#menubar .mb-item[data-m="{top}"]')
    p.click(f'#ctxmenu .mi:has-text("{item}")')

def only_plat(p, pid):
    boxes = p.locator(".panel:visible .platlist input")
    for i in range(boxes.count()):
        bx = boxes.nth(i); w = bx.get_attribute("data-id") == pid
        if bx.is_enabled() and bx.is_checked() != w: bx.click()

def run_query(p, pid, country="美国 US", weight="1", timeout=90000):
    p.click('#toolbar .tb[data-cmd="new"]'); p.wait_for_selector(".panel:visible .platlist")
    only_plat(p, pid)
    p.fill(".panel:visible .inCountry", country); p.fill(".panel:visible .inWeight", weight)
    t0 = time.time(); p.keyboard.press("F5")
    p.wait_for_function("(() => { const r = document.querySelector('.panel:not([style*=\"display: none\"]) .resCount'); return r && /共|条|行|无|没有/.test(r.textContent) && !/查询中/.test(r.textContent) })()", timeout=timeout) if False else None
    p.wait_for_selector(".panel:visible .gridscroll tbody tr, .panel:visible .gridempty:visible", timeout=timeout)
    return time.time() - t0

with sync_playwright() as pw:
    b = pw.chromium.launch()

    # ========== 1 冷启动 / 静态 / 登录页 ==========
    if want("boot"):
        @case("冷启动：/login 首字节 + 登录页可用（3 次取最大）")
        def _():
            ts = []
            for _i in range(3):
                c = new_ctx(b); p = c.new_page(); t0 = time.time(); p.goto(URL + "/login"); p.wait_for_selector("#ok"); ts.append(time.time() - t0); c.close()
            print("   login page load s:", [round(x, 2) for x in ts])
            check(max(ts) < 8, f"max {max(ts):.1f}s")
        @case("冷启动：登录 → 主界面加载完成耗时")
        def _():
            c = new_ctx(b); p = c.new_page(); watch(p, "boot"); t0 = time.time(); login(p); d = time.time() - t0; print("   login→ready s:", round(d, 2)); c.close(); check(d < 15, f"{d:.1f}s")
        @case("静态资源 CDN 缓存 / 压缩 / 内容类型")
        def _():
            c = new_ctx(b); p = c.new_page()
            r = p.request.get(URL + "/static/app.js"); check(r.status == 200 and "javascript" in r.headers["content-type"])
            r2 = p.request.get(URL + "/static/app.css"); check(r2.status == 200 and "css" in r2.headers["content-type"])
            check(p.request.get(URL + "/static/nope.js").status == 404, "404")
            for tp in ["/static/../app/main.py", "/static/%2e%2e/app/main.py", "/static/..%2fapp%2fmain.py"]:
                r3 = p.request.get(URL + tp, max_redirects=0); check(r3.status in (302, 400, 401, 403, 404) and "def " not in r3.text()[:500], f"traversal {tp} {r3.status}")
            check(p.request.get(URL + "/favicon.ico").status in (200, 204, 404))
            c.close()
        @case("敏感路径不可访问（.env/.git/app 源码/ADMIN_PASSWORD）")
        def _():
            c = new_ctx(b); p = c.new_page()
            for path in ["/.env", "/.git/config", "/app/main.py", "/ADMIN_PASSWORD.txt", "/.secrets/FH_DATA_KEY", "/vercel.json", "/requirements.txt", "/scripts/seed_cloud.py", "/api/index"]:
                r = p.request.get(URL + path, max_redirects=0); check(r.status in (302, 401, 403, 404, 405) and (r.status != 302 or r.headers.get("location") == "/login") and "FH_DATA_KEY" not in r.text() and "def " not in r.text()[:2000], f"{path} {r.status}")
            c.close()
        @case("登录页：空表单/空口令/超长输入/XSS 字符不崩溃")
        def _():
            c = new_ctx(b); p = c.new_page(); watch(p, "loginpage"); p.goto(URL + "/login")
            p.click("#ok"); p.wait_for_timeout(600)
            p.fill("#u", "<img src=x onerror=alert(1)>"); p.fill("#p", "x" * 5000); p.click("#ok")
            p.wait_for_function("document.getElementById('err').textContent.length>0", timeout=15000)
            check(p.url.endswith("/login")); c.close()
        @case("API：错误方法/畸形 JSON/超大请求体返回 4xx 而非 500")
        def _():
            c = new_ctx(b); p = c.new_page(); p.goto(URL + "/login")
            for m, path, body in [("POST", "/api/auth/login", "{bad"), ("POST", "/api/auth/login", "[]"), ("PUT", "/api/auth/login", "{}"), ("GET", "/api/auth/login", None)]:
                r = p.request.fetch(URL + path, method=m, data=body, headers={"content-type": "application/json"}); check(r.status in (400, 401, 403, 404, 405, 422), f"{m} {path} {r.status}")
            r = p.request.post(URL + "/api/auth/login", data=json.dumps({"username": "a" * 200000, "password": "b"}), headers={"content-type": "application/json"}); check(r.status < 500, f"big {r.status}")
            c.close()

    # ========== 2 登录 / 会话 / 并发 / 过期 ==========
    if want("session"):
        c1, p1 = new_admin_page(b, tag="adm-session")
        a = jf(p1, "POST", "/api/roles", {"name": "e2e_sess", "label": "E2E会话", "perms": ["query.run", "history.view"]})
        jf(p1, "POST", "/api/users", {"username": "e2e_s1", "password": TMPPW, "role": "e2e_sess"})
        @case("临时用户登录（不勾记住我）→ Cookie 为会话 Cookie")
        def _():
            c = new_ctx(b); p = c.new_page(); watch(p, "s1"); login(p, "e2e_s1", TMPPW, remember=False)
            ck = {x["name"]: x for x in c.cookies()}["fh_session"]; check(ck["expires"] == -1, f"expires={ck['expires']}"); c.close()
        @case("同一用户并发 3 个会话互不影响；一个退出，其他仍有效")
        def _():
            cs = []; ps = []
            for i in range(3):
                c = new_ctx(b); p = c.new_page(); login(p, "e2e_s1", TMPPW); cs.append(c); ps.append(p)
            for p in ps: check(p.request.get(URL + "/api/meta").status == 200)
            ps[0].evaluate("document.getElementById('uLogout').click()"); ps[0].wait_for_url("**/login")
            check(ps[0].request.get(URL + "/api/meta").status == 401); check(ps[1].request.get(URL + "/api/meta").status == 200 and ps[2].request.get(URL + "/api/meta").status == 200)
            [c.close() for c in cs]
        @case("改密码后：其他会话立即失效，本会话保持；旧密码不能登录")
        def _():
            c1_ = new_ctx(b); pa = c1_.new_page(); login(pa, "e2e_s1", TMPPW)
            c2_ = new_ctx(b); pb = c2_.new_page(); login(pb, "e2e_s1", TMPPW)
            new = TMPPW + "x"
            r = jf(pa, "POST", "/api/auth/password", {"old": TMPPW, "new": new}); check(r["s"] == 200, r)
            check(pb.request.get(URL + "/api/meta").status == 401, "other session still valid"); check(pa.request.get(URL + "/api/meta").status == 200)
            c3 = new_ctx(b); pc = c3.new_page(); pc.goto(URL + "/login"); pc.fill("#u", "e2e_s1"); pc.fill("#p", TMPPW); pc.click("#ok"); pc.wait_for_function("document.getElementById('err').textContent.length>0"); check(pc.url.endswith("/login"))
            jf(c1, "PUT", "/api/users/e2e_s1", {"password": TMPPW}) if False else jf(p1, "PUT", "/api/users/e2e_s1", {"password": TMPPW})
            [x.close() for x in (c1_, c2_, c3)]
        @case("前端：会话 Cookie 被清除后任意操作 → 跳转登录页")
        def _():
            c = new_ctx(b); p = c.new_page(); login(p, "e2e_s1", TMPPW); c.clear_cookies()
            p.click('#menubar .mb-item[data-m="平台"]'); p.click('#ctxmenu .mi:has-text("刷新平台状态")'); p.wait_for_url("**/login", timeout=15000); c.close()
        @case("服务端会话过期（库内改 seen/created）→ 401 → 前端回登录页")
        def _():
            import subprocess
            c = new_ctx(b); p = c.new_page(); login(p, "e2e_s1", TMPPW, remember=False)
            env = dbexec("db.x(\"UPDATE sessions SET seen=:t WHERE username_lc='e2e_s1'\", t=time.time()-9*3600)\nprint('ok')\n")
            check("ok" in env, "db update failed " + env[-120:])
            check(p.request.get(URL + "/api/meta").status == 401, "not expired")
            p.reload(); p.wait_for_url("**/login", timeout=15000); c.close()
        @case("记住我会话：库内回拨 29 天仍有效，31 天失效")
        def _():
            c = new_ctx(b); p = c.new_page(); login(p, "e2e_s1", TMPPW, remember=True)
            def setage(days):
                return "ok" in dbexec("db.x(\"UPDATE sessions SET seen=:t, created=:t WHERE username_lc='e2e_s1' AND remember=1\", t=time.time()-%d*86400)\nprint('ok')\n" % days)
            check(setage(29)); check(p.request.get(URL + "/api/meta").status == 200, "29d expired")
            check(setage(31)); check(p.request.get(URL + "/api/meta").status == 401, "31d valid"); c.close()
        @case("登录限流：临时用户连续 6 次错误 → 429；锁定后正确口令也被拒")
        def _():
            c = new_ctx(b); p = c.new_page(); p.goto(URL + "/login"); codes = []
            for _i in range(6):
                codes.append(p.request.post(URL + "/api/auth/login", data=json.dumps({"username": "e2e_s1", "password": "bad" + str(_i)}), headers={"content-type": "application/json"}).status)
            r = p.request.post(URL + "/api/auth/login", data=json.dumps({"username": "e2e_s1", "password": TMPPW}), headers={"content-type": "application/json"})
            print("   codes", codes, r.status); check(codes[:5] == [401] * 5 and 429 in codes[5:] + [r.status], f"{codes} {r.status}")
            c.close()
        @case("限流解除后（库内清除）可再次登录")
        def _():
            check("ok" in dbexec("db.x(\"DELETE FROM throttle\")\nprint('ok')\n"))
            c = new_ctx(b); p = c.new_page(); login(p, "e2e_s1", TMPPW); c.close()
        @case("CSRF：无 token / 错 token / 伪造 Origin 的写操作被拒")
        def _():
            c = new_ctx(b); p = c.new_page(); login(p, "e2e_s1", TMPPW)
            f = lambda h: p.evaluate("""async (h) => (await fetch('/api/history', {method:'DELETE', headers:h})).status""", h)
            check(f({}) == 403, "no csrf"); check(f({"X-CSRF-Token": "wrong"}) == 403, "wrong csrf")
            tok = p.evaluate("window.__csrf"); check(f({"X-CSRF-Token": tok, "Origin": "https://evil.example"}) == 403, "evil origin")
            c.close()
        jf(p1, "DELETE", "/api/users/e2e_s1"); jf(p1, "DELETE", "/api/roles/e2e_sess"); c1.close()
    b.close() if False else None
    # 其余分区在后续文件追加
    exec(open(pathlib.Path(__file__).with_name("e2e_part2.py"), encoding="utf-8").read()) if pathlib.Path(__file__).with_name("e2e_part2.py").exists() else None
    b.close()

bad = [n for n, ok_, _ in RES if not ok_]
print(f"\n== {len(RES)-len(bad)}/{len(RES)} 通过 ==")
for n, _, e in RES:
    if n in bad: print("  FAIL:", n, "|", e)
cons = {}
for tag, k, t in CONSOLE: cons[(k, t[:140])] = cons.get((k, t[:140]), 0) + 1
print("\n控制台/网络异常：", len(CONSOLE))
for (k, t), n in list(cons.items())[:40]: print("  ", n, k, t)
json.dump({"results": RES, "console": CONSOLE}, open("/tmp/e2e_last_%s.json" % ("_".join(sorted(ONLY)) or "all"), "w"), ensure_ascii=False)
sys.exit(1 if bad else 0)
