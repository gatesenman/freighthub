"""云端版存储（Vercel Serverless）：与桌面版 Store 同一接口，数据落在 Postgres（Neon）里。

- 平台账号、平台会话（token / cookie）、导入批次：AES-256-GCM 加密后入库（密钥 = 环境变量 FH_DATA_KEY）。
- 登录失败锁、平台运行状态、查询历史：入库，多个 Serverless 实例共享。
- 账号读缓存 2 秒（减少往返）；写入后本实例立即失效。
"""
import json
import threading
import time

from .db import Db
from .secretbox import SecretBox

HISTORY_KEEP_PER_USER = 300


class _Runtime:
    """store.runtime 的数据库版：只实现适配器 / main 用到的 get / pop。"""

    def __init__(self, st):
        self.st = st

    def get(self, pid, default=None):
        r = self.st.db.one("SELECT blob FROM runtime WHERE pid=:p", p=pid)
        if not r:
            return default
        try:
            return json.loads(r["blob"])
        except Exception:
            return default

    def pop(self, pid, default=None):
        self.st.db.x("DELETE FROM runtime WHERE pid=:p", p=pid)
        return default


class DbStore:
    cloud = True

    def __init__(self, db: Db, box: SecretBox | None = None):
        self.db = db
        self.box = box or SecretBox()
        self.dir = None
        self.lock = threading.RLock()
        self.runtime = _Runtime(self)
        self._cache = None

    # ---------- 配置（加密） ----------
    def _accounts(self):
        with self.lock:
            if self._cache and time.time() - self._cache[0] < 2:
                return self._cache[1]
            out = {}
            for r in self.db.q("SELECT pid, blob FROM accounts"):
                try:
                    out[r["pid"]] = self.box.open(r["blob"], "account:" + r["pid"])
                except Exception:
                    out[r["pid"]] = {}      # 密文无法解密（密钥不对）：当作未配置，不让整个应用崩
            self._cache = (time.time(), out)
            return out

    def _read(self):
        return {"accounts": {k: dict(v) for k, v in self._accounts().items()}}

    def get_account(self, pid):
        return dict(self._accounts().get(pid, {}))

    def set_account(self, pid, patch: dict):
        with self.lock:
            if any(k not in ("enabled",) and v is not None for k, v in patch.items()):
                self.unlock_login(pid)
            self._cache = None
            acc = dict(self._accounts().get(pid, {}))
            for k, v in patch.items():
                if v is not None:
                    acc[k] = v
            blob = self.box.seal(acc, "account:" + pid)
            n = self.db.x("UPDATE accounts SET blob=:b, updated=:t WHERE pid=:p", b=blob, t=time.time(), p=pid)
            if not n:
                self.db.x("INSERT INTO accounts (pid, blob, updated) VALUES (:p,:b,:t) ON CONFLICT (pid) DO UPDATE SET blob=EXCLUDED.blob, updated=EXCLUDED.updated",
                          p=pid, b=blob, t=time.time())
            self._cache = None
            return dict(acc)

    # ---------- 登录失败锁 ----------
    def login_locked(self, pid):
        return bool(self.db.one("SELECT pid FROM login_lock WHERE pid=:p", p=pid))

    def lock_login(self, pid):
        self.db.x("INSERT INTO login_lock (pid, ts) VALUES (:p,:t) ON CONFLICT (pid) DO UPDATE SET ts=EXCLUDED.ts", p=pid, t=time.time())

    def unlock_login(self, pid):
        self.db.x("DELETE FROM login_lock WHERE pid=:p", p=pid)

    # ---------- 平台会话（加密） ----------
    def load_session(self, pid):
        r = self.db.one("SELECT blob FROM psessions WHERE pid=:p", p=pid)
        if not r:
            return None
        try:
            return self.box.open(r["blob"], "psession:" + pid)
        except Exception:
            return None

    def save_session(self, pid, data):
        blob = self.box.seal(data, "psession:" + pid)
        self.db.x("INSERT INTO psessions (pid, blob, updated) VALUES (:p,:b,:t) ON CONFLICT (pid) DO UPDATE SET blob=EXCLUDED.blob, updated=EXCLUDED.updated",
                  p=pid, b=blob, t=time.time())

    def clear_session(self, pid):
        self.db.x("DELETE FROM psessions WHERE pid=:p", p=pid)

    # ---------- 运行状态 ----------
    def mark(self, pid, ok: bool, message: str = ""):
        from .redact import redact
        r = self.runtime.get(pid, {}) or {}
        now = time.time()
        if ok:
            r["last_ok"], r["last_error"] = now, None
        else:
            r["last_fail"], r["last_error"] = now, redact(message)
        self.db.x("INSERT INTO runtime (pid, blob) VALUES (:p,:b) ON CONFLICT (pid) DO UPDATE SET blob=EXCLUDED.blob", p=pid, b=json.dumps(r, ensure_ascii=False))

    # ---------- 导入批次（含明文凭据 → 加密，30 分钟过期） ----------
    def batch_put(self, bid, rows):
        self.db.x("DELETE FROM batches WHERE ts<:t", t=time.time() - 1800)
        self.db.x("INSERT INTO batches (id, ts, blob) VALUES (:i,:t,:b)", i=bid, t=time.time(), b=self.box.seal(rows, "batch:" + bid))

    def batch_pop(self, bid):
        r = self.db.one("SELECT blob, ts FROM batches WHERE id=:i", i=bid)
        if not r:
            return None
        if not self.db.x("DELETE FROM batches WHERE id=:i", i=bid):
            return None         # 被另一个请求先取走了
        if time.time() - r["ts"] > 1800:
            return None
        try:
            return self.box.open(r["blob"], "batch:" + bid)
        except Exception:
            return None

    # ---------- 历史 ----------
    def add_history(self, query, platforms, rows, user=""):
        hid = self.db.ins("INSERT INTO history (ts, query, platforms, rows_json, username) VALUES (:t,:q,:p,:r,:u)",
                          t=time.time(), q=json.dumps(query, ensure_ascii=False), p=json.dumps(platforms, ensure_ascii=False),
                          r=json.dumps(rows, ensure_ascii=False), u=user or "")
        # 每个用户只保留最近 N 条，防止撑爆免费库的 0.5GB
        self.db.x("DELETE FROM history WHERE username=:u AND id NOT IN (SELECT id FROM history WHERE username=:u ORDER BY id DESC LIMIT :n)",
                  u=user or "", n=HISTORY_KEEP_PER_USER)
        return hid

    def list_history(self, limit=200, owner=None):
        sql = "SELECT id, ts, query, platforms, rows_json, username FROM history "
        p = {"n": limit}
        if owner is not None:
            sql += "WHERE username=:o "
            p["o"] = owner
        out = []
        for r in self.db.q(sql + "ORDER BY id DESC LIMIT :n", **p):
            rows = json.loads(r["rows_json"])
            prices = [x["total"] for x in rows if x.get("total") is not None and not x.get("excluded") and (x.get("currency") or "CNY").upper() == "CNY"]
            out.append({"id": r["id"], "ts": r["ts"], "query": json.loads(r["query"]), "user": r["username"],
                        "platforms": json.loads(r["platforms"]), "count": len(rows), "min_total": min(prices) if prices else None})
        return out

    def get_history(self, hid, owner=None):
        r = self.db.one("SELECT * FROM history WHERE id=:i", i=hid)
        if not r or (owner is not None and r["username"] != owner):
            return None
        return {"id": r["id"], "ts": r["ts"], "query": json.loads(r["query"]), "user": r["username"],
                "platforms": json.loads(r["platforms"]), "rows": json.loads(r["rows_json"])}

    def delete_history(self, hid=None, owner=None):
        where, p = [], {}
        if hid is not None:
            where.append("id=:i"); p["i"] = hid
        if owner is not None:
            where.append("username=:o"); p["o"] = owner
        return self.db.x("DELETE FROM history" + (" WHERE " + " AND ".join(where) if where else ""), **p)
