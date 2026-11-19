"""本地配置与历史存储。

- 账号凭据保存在 data/config.json（只存在本机，不进前端、不进代码仓库）。
- 平台会话（Cookie / token）保存在 data/sessions/*.json。
- 查询历史保存在 data/history.db（SQLite）。
"""
import json
import os
import sqlite3
import threading
import time
from pathlib import Path


def data_dir() -> Path:
    from .paths import data_path
    p = data_path()
    p.mkdir(parents=True, exist_ok=True)
    (p / "sessions").mkdir(exist_ok=True)
    return p


def _write_private(path, data: bytes):
    """创建时就是 0600，不存在「先 644 写入再 chmod」的窗口。"""
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    finally:
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass


class Store:
    def __init__(self):
        self.dir = data_dir()
        self.cfg_path = self.dir / "config.json"
        self.lock = threading.RLock()
        self.runtime = {}  # 平台运行状态：最近一次成功/失败
        self._batches = {}  # 账号导入批次（含明文凭据，只在内存、30 分钟过期）
        self._init_db()
        if not self.cfg_path.exists():
            self._write({"accounts": {}})

    # ---------- 配置 ----------
    def _read(self):
        try:
            cfg = json.loads(self.cfg_path.read_text(encoding="utf-8"))
            if not isinstance(cfg, dict) or not isinstance(cfg.get("accounts", {}), dict):
                raise ValueError("config.json 结构不正确")
            return cfg
        except FileNotFoundError:
            return {"accounts": {}}
        except Exception:
            # 配置文件损坏：先备份（0600），再返回空配置，避免下一次保存把用户的凭据直接覆盖丢失
            try:
                import hashlib
                raw = self.cfg_path.read_bytes()
                bak = self.cfg_path.with_name("config.json.corrupt-" + hashlib.sha1(raw).hexdigest()[:8])
                if raw.strip() and not bak.exists():
                    _write_private(bak, raw)
            except Exception:
                pass
            return {"accounts": {}}

    def _write(self, cfg):
        tmp = self.cfg_path.with_suffix(".tmp")
        _write_private(tmp, json.dumps(cfg, ensure_ascii=False, indent=2).encode("utf-8"))
        os.replace(tmp, self.cfg_path)

    def get_account(self, pid):
        with self.lock:
            return dict(self._read().get("accounts", {}).get(pid, {}))

    # ---------- 登录失败锁（登录一次原则）----------
    def _locks(self):
        try:
            d = json.loads((self.dir / "login_lock.json").read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def login_locked(self, pid):
        return pid in self._locks()

    def lock_login(self, pid):
        with self.lock:
            d = self._locks()
            d[pid] = time.time()
            _write_private(self.dir / "login_lock.json", json.dumps(d).encode("utf-8"))

    def unlock_login(self, pid):
        with self.lock:
            d = self._locks()
            if pid in d:
                d.pop(pid)
                _write_private(self.dir / "login_lock.json", json.dumps(d).encode("utf-8"))

    def set_account(self, pid, patch: dict):
        with self.lock:
            if any(k not in ("enabled",) and v is not None for k, v in patch.items()):
                self.unlock_login(pid)   # 改了凭据 / 授权模式：允许再试一次
            cfg = self._read()
            acc = cfg.setdefault("accounts", {}).setdefault(pid, {})
            for k, v in patch.items():
                if v is None:
                    continue
                acc[k] = v
            self._write(cfg)
            return dict(acc)

    # ---------- 会话 ----------
    def load_session(self, pid):
        f = self.dir / "sessions" / f"{pid}.json"
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return None

    def save_session(self, pid, data):
        f = self.dir / "sessions" / f"{pid}.json"
        _write_private(f, json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def clear_session(self, pid):
        f = self.dir / "sessions" / f"{pid}.json"
        try:
            f.unlink()
        except FileNotFoundError:
            pass

    # ---------- 运行状态 ----------
    def mark(self, pid, ok: bool, message: str = ""):
        with self.lock:
            r = self.runtime.setdefault(pid, {})
            now = time.time()
            if ok:
                r["last_ok"] = now
                r["last_error"] = None
            else:
                r["last_fail"] = now
                from .redact import redact
                r["last_error"] = redact(message)

    # ---------- 导入批次 ----------
    def batch_put(self, bid, rows):
        for k in [k for k, v in self._batches.items() if time.time() - v[0] > 1800]:
            self._batches.pop(k, None)
        self._batches[bid] = (time.time(), rows)

    def batch_pop(self, bid):
        v = self._batches.pop(bid, None)
        return v[1] if v and time.time() - v[0] <= 1800 else None

    # ---------- 历史 ----------
    def _db(self):
        con = sqlite3.connect(self.dir / "history.db")
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self):
        with self._db() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, query TEXT, platforms TEXT, rows TEXT)"
            )
            cols = [r[1] for r in con.execute("PRAGMA table_info(history)")]
            if "user" not in cols:      # 1.4.0：服务器模式按用户隔离历史；老库自动加列（桌面版 user 为空串）
                con.execute("ALTER TABLE history ADD COLUMN user TEXT NOT NULL DEFAULT ''")

    def add_history(self, query, platforms, rows, user=""):
        with self._db() as con:
            cur = con.execute(
                "INSERT INTO history (ts, query, platforms, rows, user) VALUES (?,?,?,?,?)",
                (time.time(), json.dumps(query, ensure_ascii=False), json.dumps(platforms, ensure_ascii=False), json.dumps(rows, ensure_ascii=False), user or ""),
            )
            return cur.lastrowid

    def list_history(self, limit=200, owner=None):
        """owner=None 返回全部；否则只返回该用户自己的记录。"""
        with self._db() as con:
            out = []
            sql, args = "SELECT id, ts, query, platforms, rows, user FROM history ", ()
            if owner is not None:
                sql, args = sql + "WHERE user=? ", (owner,)
            for r in con.execute(sql + "ORDER BY id DESC LIMIT ?", args + (limit,)):
                rows = json.loads(r["rows"])
                prices = [x["total"] for x in rows if x.get("total") is not None and not x.get("excluded") and (x.get("currency") or "CNY").upper() == "CNY"]
                out.append({
                    "id": r["id"], "ts": r["ts"], "query": json.loads(r["query"]), "user": r["user"],
                    "platforms": json.loads(r["platforms"]), "count": len(rows),
                    "min_total": min(prices) if prices else None,
                })
            return out

    def get_history(self, hid, owner=None):
        with self._db() as con:
            r = con.execute("SELECT * FROM history WHERE id=?", (hid,)).fetchone()
            if not r or (owner is not None and r["user"] != owner):
                return None
            return {"id": r["id"], "ts": r["ts"], "query": json.loads(r["query"]), "user": r["user"],
                    "platforms": json.loads(r["platforms"]), "rows": json.loads(r["rows"])}

    def delete_history(self, hid=None, owner=None):
        with self._db() as con:
            where, args = [], []
            if hid is not None:
                where.append("id=?"); args.append(hid)
            if owner is not None:
                where.append("user=?"); args.append(owner)
            con.execute("DELETE FROM history" + (" WHERE " + " AND ".join(where) if where else ""), args)
