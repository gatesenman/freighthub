"""线上端到端测试公共库（Playwright）。口令从 ADMIN_PASSWORD.txt 读取，永不打印。"""
import json, os, pathlib, re, sys, time, traceback
from playwright.sync_api import sync_playwright

URL = os.environ.get("FH_URL", "https://freight.gaosichun.dev").rstrip("/")
ROOT = pathlib.Path(__file__).resolve().parents[2]
SH = ROOT / "screenshots" / "e2e"; SH.mkdir(parents=True, exist_ok=True)
DL = pathlib.Path("/tmp/e2e_dl"); DL.mkdir(exist_ok=True)
PW = re.search(r"密码[:：]\s*(\S+)", (ROOT / "ADMIN_PASSWORD.txt").read_text(encoding="utf-8")).group(1)
RES = []
CONSOLE = []      # (page-tag, kind, text)
TMPPW = "E2e-Tmp-" + str(int(time.time())) + "-Zq"


def rec(name, ok_, extra=""):
    RES.append((name, bool(ok_), str(extra)[:300]))
    print(("PASS " if ok_ else "FAIL ") + name + (f"  [{str(extra)[:200]}]" if extra else ""), flush=True)


def case(name):
    """装饰器：一个用例；异常即失败，继续往下。"""
    def deco(fn):
        def run(*a, **k):
            t0 = time.time()
            try:
                r = fn(*a, **k)
                if r is None or r is True:
                    rec(name, True, f"{time.time()-t0:.1f}s")
                else:
                    rec(name, bool(r), r if not isinstance(r, bool) else "")
            except Exception as e:
                rec(name, False, (type(e).__name__ + ": " + str(e).splitlines()[0])[:240])
                for pg in PAGES:       # 失败后清理残留的对话框 / 菜单，避免连锁失败
                    try:
                        if not pg.is_closed():
                            pg.keyboard.press("Escape"); close_modal(pg)
                    except Exception:
                        pass
        run()
        return run
    return deco


def check(cond, msg="断言失败"):
    if not cond:
        raise AssertionError(msg)


PAGES = []


def watch(page, tag):
    PAGES.append(page)
    page.on("pageerror", lambda e: CONSOLE.append((tag, "pageerror", str(e)[:300])))
    page.on("console", lambda m: CONSOLE.append((tag, "console." + m.type, m.text[:300])) if m.type in ("error",) else None)
    page.on("requestfailed", lambda r: CONSOLE.append((tag, "requestfailed", r.method + " " + r.url[:120] + " " + str(r.failure)[:80])))
    page.on("response", lambda r: CONSOLE.append((tag, "http%d" % r.status, r.request.method + " " + r.url.replace(URL, "")[:120])) if r.status >= 500 else None)


def new_ctx(b, w=1280, h=800, **kw):
    return b.new_context(viewport={"width": w, "height": h}, accept_downloads=True, bypass_csp=True, **kw)


def login(page, user="admin", pw=None, remember=True, wait=True):
    page.goto(URL + "/login")
    page.fill("#u", user); page.fill("#p", pw or PW)
    if not remember:
        page.uncheck("#rm")
    page.click("#ok")
    if wait:
        page.wait_for_url(URL + "/", timeout=45000)
        page.wait_for_selector("#boot", state="detached", timeout=45000)


def jf(page, method, path, body=None):
    return page.evaluate("""async ([m,p,b]) => { const r = await fetch(p, {method:m, headers:{'Content-Type':'application/json','X-CSRF-Token':window.__csrf||''}, body: b?JSON.stringify(b):undefined}); let j=null; try{j=await r.json()}catch(e){}; return {s:r.status, j}; }""", [method, path, body])


def shot(page, name):
    page.screenshot(path=str(SH / f"{name}.png"))


def menu(page, top, item=None):
    page.click(f'#menubar .mb-item[data-m="{top}"]')
    if item:
        page.click(f'#ctxmenu .mi:has-text("{item}")') if page.locator("#ctxmenu").count() and page.locator(f'#ctxmenu .mi:has-text("{item}")').count() else page.click(f'.menu-pop .mi:has-text("{item}")')


def close_modal(page):
    for _ in range(3):
        if not page.locator("#modal").is_visible():
            return
        for txt in ("取消", "关闭", "确定"):
            bt = page.locator(f"#modal .btn:has-text('{txt}')")
            if bt.count():
                try:
                    bt.first.click(timeout=2000); break
                except Exception:
                    pass
        else:
            page.keyboard.press("Escape")
        page.wait_for_timeout(150)


def new_admin_page(b, tag="admin", **kw):
    ctx = new_ctx(b, **kw); p = ctx.new_page(); watch(p, tag); login(p); return ctx, p


def dbexec(code):
    """用 Neon 连接（.secrets/neon.env）在库里执行一段 python（仅测试用：回拨会话时间、清限流）。返回 stdout。"""
    import subprocess, tempfile
    pre = "import os,time,json,sys\nsys.path.insert(0, %r)\nfrom seed_cloud import load_env\nload_env()\n" % str(ROOT / "scripts") + "from app.db import Db, database_url\ndb=Db(database_url())\n"
    f = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False); f.write(pre + code); f.close()
    out = subprocess.run(["bash", "-c", f"set -a; . {ROOT}/.secrets/neon.env; set +a; cd {ROOT} && PYTHONPATH={ROOT} /tmp/fhv/bin/python {f.name}"], capture_output=True, text=True, timeout=60)
    os.unlink(f.name)
    return out.stdout + ("" if out.returncode == 0 else "ERR:" + out.stderr[-200:])


def finish(tag):
    bad = [n for n, ok_, _ in RES if not ok_]
    print(f"\n== [{tag}] {len(RES)-len(bad)}/{len(RES)} 通过 ==")
    for n, _, e in RES:
        if n in bad: print("  FAIL:", n, "|", e)
    cons = {}
    for t_, k, t in CONSOLE: cons[(k, t[:140])] = cons.get((k, t[:140]), 0) + 1
    print("控制台/网络异常：", len(CONSOLE))
    for (k, t), n in list(cons.items())[:40]: print("  ", n, k, t)
    json.dump({"results": RES, "console": CONSOLE}, open(f"/tmp/e2e_last_{tag}.json", "w"), ensure_ascii=False)
    sys.exit(1 if bad else 0)


def only_plat(p, pid):
    boxes = p.locator(".panel:visible .platlist input")
    for i in range(boxes.count()):
        bx = boxes.nth(i); w = bx.get_attribute("data-id") == pid
        if bx.is_enabled() and bx.is_checked() != w: bx.click()


def run_query(p, pid, country="美国 US", weight="1", timeout=90000):
    p.click('#toolbar .tb[data-cmd="new"]'); p.wait_for_selector(".panel:visible .platlist")
    only_plat(p, pid)
    p.fill(".panel:visible .inCountry", country); p.fill(".panel:visible .inWeight", weight)
    t0 = time.time()
    with p.expect_response(lambda r: r.url.endswith("/api/quote"), timeout=timeout) as ri:
        p.keyboard.press("F5")
    p.last_quote_status = ri.value.status
    p.wait_for_timeout(500)
    return time.time() - t0
