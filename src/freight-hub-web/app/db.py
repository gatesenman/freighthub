"""云端版数据库层：同一套 SQL 同时支持 PostgreSQL（Vercel / Neon，pg8000 纯 Python 驱动）和 SQLite（本地测试）。

- 参数一律用 :name 风格（pg8000.native 与 sqlite3 都支持）。
- 布尔用 0/1，时间用 epoch 秒（REAL / DOUBLE PRECISION），JSON 存 TEXT。
- 连接池：每个线程取一条连接，用完放回；连接出错会丢弃并重试一次（Serverless 实例被冻结后连接可能已断）。
"""
import json
import os
import re
import sqlite3
import ssl
import threading
import time
from urllib.parse import parse_qs, unquote, urlparse

SCHEMA_VERSION = 4


def _ddl(pg: bool):
    pk = "BIGSERIAL PRIMARY KEY" if pg else "INTEGER PRIMARY KEY AUTOINCREMENT"
    real = "DOUBLE PRECISION" if pg else "REAL"
    return [
        "CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)",
        f"CREATE TABLE IF NOT EXISTS users (username_lc TEXT PRIMARY KEY, username TEXT NOT NULL, pw TEXT NOT NULL, role TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0, created {real}, last_login {real}, last_ip TEXT, must_change INTEGER NOT NULL DEFAULT 0)",
        f"CREATE TABLE IF NOT EXISTS roles (name TEXT PRIMARY KEY, label TEXT NOT NULL, descr TEXT NOT NULL DEFAULT '', perms TEXT NOT NULL, system INTEGER NOT NULL DEFAULT 0, created {real}, updated {real})",
        f"CREATE TABLE IF NOT EXISTS sessions (th TEXT PRIMARY KEY, username_lc TEXT NOT NULL, csrf TEXT NOT NULL, created {real}, seen {real}, ip TEXT, ua TEXT, remember INTEGER NOT NULL DEFAULT 0)",
        "CREATE INDEX IF NOT EXISTS sessions_user ON sessions (username_lc)",
        f"CREATE TABLE IF NOT EXISTS throttle (k TEXT PRIMARY KEY, n INTEGER NOT NULL, lock_until {real} NOT NULL DEFAULT 0, ts {real})",
        f"CREATE TABLE IF NOT EXISTS audit (id {pk}, ts {real} NOT NULL, username TEXT, role TEXT, ip TEXT, ua TEXT, action TEXT NOT NULL, target TEXT, status TEXT NOT NULL DEFAULT 'ok', detail TEXT)",
        "CREATE INDEX IF NOT EXISTS audit_ts ON audit (ts)",
        "CREATE INDEX IF NOT EXISTS audit_user ON audit (username)",
        "CREATE INDEX IF NOT EXISTS audit_action ON audit (action)",
        f"CREATE TABLE IF NOT EXISTS accounts (pid TEXT PRIMARY KEY, blob TEXT NOT NULL, updated {real})",
        f"CREATE TABLE IF NOT EXISTS psessions (pid TEXT PRIMARY KEY, blob TEXT NOT NULL, updated {real})",
        "CREATE TABLE IF NOT EXISTS runtime (pid TEXT PRIMARY KEY, blob TEXT NOT NULL)",
        f"CREATE TABLE IF NOT EXISTS login_lock (pid TEXT PRIMARY KEY, ts {real})",
        f"CREATE TABLE IF NOT EXISTS history (id {pk}, ts {real}, query TEXT, platforms TEXT, rows_json TEXT, username TEXT NOT NULL DEFAULT '')",
        "CREATE INDEX IF NOT EXISTS history_user ON history (username)",
        f"CREATE TABLE IF NOT EXISTS batches (id TEXT PRIMARY KEY, ts {real}, blob TEXT NOT NULL)",
    ]


class Db:
    def __init__(self, url: str):
        self.url = url
        self.pg = not (url.startswith("sqlite:") or url == ":memory:")
        self.lock = threading.RLock()
        self._pool = []
        self._sq = None
        # Neon 的 SQL-over-HTTPS（443 端口）：Serverless 里不用建 TCP/TLS 连接、冷启动更快，也不受出站端口限制。
        # FH_DB_TRANSPORT=tcp 时改用 pg8000 直连（本地 Postgres 测试用）。
        self.http = self.pg and os.environ.get("FH_DB_TRANSPORT", "http").strip().lower() != "tcp" and "neon.tech" in url
        self._tl = threading.local()
        if self.http:
            self.endpoint = "https://%s/sql" % urlparse(url).hostname
        if self.pg and not self.http:
            u = urlparse(url)
            qs = parse_qs(u.query)
            self.kw = dict(user=unquote(u.username or ""), password=unquote(u.password or ""), host=u.hostname,
                           port=u.port or 5432, database=(u.path or "/").lstrip("/"))
            mode = (qs.get("sslmode") or ["require"])[0]
            self.kw["ssl_context"] = None if mode == "disable" else ssl.create_default_context()
            self.kw["timeout"] = 20
        elif not self.pg:
            path = ":memory:" if url == ":memory:" else url[len("sqlite:///"):] if url.startswith("sqlite:///") else url[len("sqlite:"):]
            self._sq = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=20)
            self._sq.row_factory = sqlite3.Row

    # ---- 连接 ----
    def _connect(self):
        import pg8000.native
        return pg8000.native.Connection(**self.kw)

    def _take(self):
        with self.lock:
            return self._pool.pop() if self._pool else None

    def _give(self, c):
        with self.lock:
            if len(self._pool) < 6:
                self._pool.append(c)
                return
        try:
            c.close()
        except Exception:
            pass

    def _run(self, sql, p):
        """执行并返回 (rows[list of dict], rowcount)。"""
        if self.http:
            return self._run_http(sql, p)
        if not self.pg:
            with self.lock:
                cur = self._sq.execute(sql, p)
                rows = [dict(r) for r in cur.fetchall()] if cur.description else []
                return rows, cur.rowcount
        last = None
        for attempt in (0, 1):
            c = self._take() if attempt == 0 else None
            fresh = c is None
            try:
                if c is None:
                    c = self._connect()
                res = c.run(sql, **p)
                cols = [(x["name"] if isinstance(x, dict) else x[0]) for x in (c.columns or [])]
                cols = [x.decode() if isinstance(x, bytes) else x for x in cols]
                rows = [dict(zip(cols, r)) for r in (res or [])] if cols else []
                n = c.row_count
                self._give(c)
                return rows, n
            except Exception as e:  # noqa
                last = e
                try:
                    c and c.close()
                except Exception:
                    pass
                import pg8000.exceptions as pe
                # 已拿到数据库的明确错误（约束冲突 / SQL 错误）不重试；只有连接类错误才换新连接重试
                if isinstance(e, pe.DatabaseError) and not isinstance(e, (pe.InterfaceError,)):
                    raise
                if fresh:
                    raise
        raise last

    _NAMED = re.compile(r"(?<![:\w]):([A-Za-z_][A-Za-z0-9_]*)")

    def _to_positional(self, sql, p):
        """:name → $n（跳过单引号字符串里的内容）。"""
        order, parts = [], sql.split("'")
        for i in range(0, len(parts), 2):
            def rep(m):
                n = m.group(1)
                if n not in order:
                    order.append(n)
                return "$%d" % (order.index(n) + 1)
            parts[i] = self._NAMED.sub(rep, parts[i])
        return "'".join(parts), [p[n] for n in order]

    @staticmethod
    def _conv(v, oid):
        if v is None:
            return None
        if oid in (20, 21, 23, 26):
            return int(v)
        if oid in (700, 701, 1700):
            return float(v)
        return v

    def _run_http(self, sql, p):
        q, params = self._to_positional(sql, p)
        s = getattr(self._tl, "s", None)
        if s is None:
            import requests
            s = self._tl.s = requests.Session()
        hdr = {"Neon-Connection-String": self.url, "Content-Type": "application/json", "Neon-Raw-Text-Output": "true"}
        last = None
        for attempt in range(3):
            try:
                r = s.post(self.endpoint, headers=hdr, data=json.dumps({"query": q, "params": params}), timeout=30)
            except Exception as e:  # noqa  网络抖动：重试（SQL 都是幂等 / 单语句）
                last = e
                time.sleep(0.3 * (attempt + 1))
                continue
            if r.status_code >= 500 and attempt < 2:
                time.sleep(0.3 * (attempt + 1))
                continue
            if r.status_code != 200:
                try:
                    msg = r.json().get("message", "")
                except Exception:
                    msg = r.text[:200]
                raise RuntimeError(f"数据库错误 {r.status_code}: {msg}")
            d = r.json()
            fields = d.get("fields") or []
            names = [f["name"] for f in fields]
            oids = [f["dataTypeID"] for f in fields]
            rows = [{n: self._conv(row.get(n), o) for n, o in zip(names, oids)} for row in (d.get("rows") or [])]
            return rows, int(d.get("rowCount") or 0)
        raise RuntimeError(f"数据库不可达：{type(last).__name__}")

    def q(self, sql, **p):
        return self._run(sql, p)[0]

    def one(self, sql, **p):
        r = self._run(sql, p)[0]
        return r[0] if r else None

    def x(self, sql, **p):
        return self._run(sql, p)[1]

    def ins(self, sql, **p):
        """INSERT ... RETURNING id"""
        r = self._run(sql + " RETURNING id", p)[0]
        return r[0]["id"] if r else None

    def close(self):
        with self.lock:
            for c in self._pool:
                try:
                    c.close()
                except Exception:
                    pass
            self._pool = []
            if self._sq:
                self._sq.close()

    # ---- 建表 ----
    def ensure_schema(self, seed=None):
        cur = None
        try:
            r = self.one("SELECT v FROM meta WHERE k=:k", k="schema")
            cur = int(r["v"]) if r else None
        except Exception:
            cur = None
        if cur == SCHEMA_VERSION:
            return
        for s in _ddl(self.pg):
            self.x(s)
        if seed:
            seed(self)
        if cur is None:
            self.x("INSERT INTO meta (k, v) VALUES (:k, :v) ON CONFLICT (k) DO NOTHING", k="schema", v=str(SCHEMA_VERSION))
        self.x("UPDATE meta SET v=:v WHERE k=:k", k="schema", v=str(SCHEMA_VERSION))


def database_url():
    """Vercel 的 Neon 集成会注入 DATABASE_URL / POSTGRES_URL 等变量。"""
    for k in ("FH_DATABASE_URL", "DATABASE_URL", "POSTGRES_URL", "POSTGRES_PRISMA_URL"):
        v = os.environ.get(k)
        if v:
            if v.startswith("postgres://"):
                v = "postgresql://" + v[len("postgres://"):]
            return v
    return None
