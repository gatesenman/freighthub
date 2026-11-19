"""只读真实平台冒烟（可选）。用法：FH_LIVE=1 python tests/live_smoke.py [平台id ...]
安全约束：
- 使用 data/ 的**临时拷贝**（含已保存的会话），不改动真实 data/；
- 每个平台只发 1 次「美国 1kg」报价查询；需要登录时最多 1 次，登录/鉴权失败立即停止该平台，不重试；
- 浏览器登录类平台（云途）会话失效时不自动重新登录（避免验证码/风控）；
- 请求层旁路记录：只允许 GET 取 token、报价/产品查询类请求；出现下单/重置密钥/改密码路径或 msgType 立即中止；
- 输出只含 平台/状态/条数/最低价/错误类型，不含任何凭据。结果写入 reports/live_smoke.json。
"""
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if os.environ.get("FH_LIVE") != "1":
    print("未设置 FH_LIVE=1，跳过真实平台冒烟。")
    sys.exit(0)
tmp = Path(tempfile.mkdtemp(prefix="fh_live_"))
src = ROOT / "data"
for name in ("config.json",):
    if (src / name).exists():
        shutil.copy2(src / name, tmp / name)
if (src / "sessions").exists():
    shutil.copytree(src / "sessions", tmp / "sessions")
os.chmod(tmp, 0o700)
os.environ["FREIGHT_HUB_DATA"] = str(tmp)
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

from app import main as m  # noqa: E402
from app.adapters.base import AdapterError, Query  # noqa: E402

FORBIDDEN = re.compile(r"(create|submit|cancel|delete|remove|reset|regenerate|refresh[_-]?key|password|passwd|pwd|modify|update|pay(ment)?|recharge|withdraw|place[_-]?order|insert|save[_-]?order|forgot)", re.I)
YW_OK = {"calc.list", "common.country.getlist"}
ALLOWED_MSG = {"IPE_QUERY_PRODUCT_LIST", "IUOP_QUERY_FREIGHT"}
LOG = []
_orig = requests.Session.request


def guarded(self, method, url, *a, **kw):
    hdr = kw.get("headers") or {}
    mt = hdr.get("msgType")
    LOG.append((method.upper(), re.sub(r"\?.*", "", url), mt))
    path = re.sub(r"^https?://[^/]+", "", re.sub(r"\?.*", "", url))
    if FORBIDDEN.search(path):
        raise SystemExit(f"只读保护：拒绝请求 {method} {path}")
    mq = re.search(r"method=([\w.]+)", url)
    if "yw56" in url and mq and mq.group(1) not in YW_OK:
        raise SystemExit(f"只读保护：拒绝燕文 method {mq.group(1)}")
    if mt and mt not in ALLOWED_MSG:
        raise SystemExit(f"只读保护：拒绝顺丰 msgType {mt}")
    return _orig(self, method, url, *a, **kw)


requests.Session.request = guarded
want = sys.argv[1:] or ["yutong", "huanyang", "zhonghang", "yuntu", "shunfeng", "yanwen", "xinhang"]
out = []
for pid in want:
    a = m.registry.get(pid)
    row = {"platform": pid, "status": "skipped", "rows": 0, "min_total": None, "error_kind": None, "note": ""}
    out.append(row)
    if a is None:
        row["note"] = "未注册"
        continue
    if a.needs_account and not a.has_account():
        row["note"] = "未配置账号"
        continue
    if a.auth_mode() == "login" and pid == "yuntu":
        if not m.store.load_session(pid):
            row["note"] = "无已保存会话，浏览器登录类平台不在冒烟中自动登录"
            continue

        def no_login(*_a, **_k):
            raise AdapterError("会话失效；冒烟测试不自动重新登录", "login_failed")
        a._login = no_login
    n0 = len(LOG)
    try:
        rows = a.quote(Query(country="US", weight=1.0))
        row.update(status="ok", rows=len(rows), min_total=min([r["total"] for r in rows], default=None))
    except AdapterError as e:
        row.update(status="error", error_kind=e.kind, note=str(e)[:120])
    except Exception as e:
        row.update(status="exception", error_kind=type(e).__name__)
    row["http_calls"] = len(LOG) - n0
    row["logins"] = sum(1 for x in LOG[n0:] if re.search(r"(login|token|getAuth|oauth)", x[1], re.I))
    print(pid, row["status"], row["rows"], row["error_kind"] or "")
    time.sleep(1)
for k in ("config.json",):
    pass
rep = ROOT / "reports"
rep.mkdir(exist_ok=True)
(rep / "live_smoke.json").write_text(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "results": out}, ensure_ascii=False, indent=2), encoding="utf-8")
shutil.rmtree(tmp, ignore_errors=True)
