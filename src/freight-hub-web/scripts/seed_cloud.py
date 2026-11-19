"""把桌面版的平台账号（加密）和初始管理员写进云端数据库。在本机运行，不会把任何明文写进仓库 / 输出。

用法：python scripts/seed_cloud.py --config /path/to/config.json [--sessions /path/to/sessions]
需要环境变量：DATABASE_URL、FH_DATA_KEY（或放在 .secrets/ 下，脚本自动读取）。
- 管理员 admin：随机强口令，只写到 ADMIN_PASSWORD.txt（0600），已存在就不动；
- 平台账号：AES-256-GCM 加密后入库（已存在的平台默认跳过，--overwrite 才覆盖）。
"""
import argparse
import json
import os
import secrets
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load_env():
    for name in ("FH_DATA_KEY", "DATABASE_URL"):
        f = ROOT / ".secrets" / name
        if not os.environ.get(name) and f.exists():
            os.environ[name] = f.read_text().strip()
    ne = ROOT / ".secrets" / "neon.env"
    if not os.environ.get("DATABASE_URL") and ne.exists():
        for line in ne.read_text().splitlines():
            if line.startswith("DATABASE_URL="):
                os.environ["DATABASE_URL"] = line.split("=", 1)[1].strip().strip('"')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config"), ap.add_argument("--sessions")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--admin-file", default=str(ROOT / "ADMIN_PASSWORD.txt"))
    a = ap.parse_args()
    load_env()
    from app.cloud import seed_roles
    from app.db import Db, database_url
    from app.pgstore import DbStore
    from app.server import hash_password
    db = Db(database_url())
    db.ensure_schema(seed=seed_roles)
    store = DbStore(db)
    # 管理员
    if db.one("SELECT username_lc FROM users WHERE username_lc='admin'"):
        print("管理员 admin 已存在，保持不变")
    else:
        pw = secrets.token_urlsafe(18)
        fd = os.open(a.admin_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"用户名：admin\n密码：{pw}\n登录后请在「工具 → 修改我的密码」里改成自己的密码。\n")
        os.chmod(a.admin_file, 0o600)
        db.x("INSERT INTO users (username_lc, username, pw, role, disabled, created, must_change) VALUES ('admin','admin',:p,'admin',0,:t,0)",
             p=hash_password(pw), t=time.time())
        print("已创建管理员 admin，口令写入", a.admin_file)
    # 平台账号
    if a.config:
        cfg = json.loads(Path(a.config).read_text(encoding="utf-8")).get("accounts", {})
        for pid, acc in cfg.items():
            if not a.overwrite and db.one("SELECT pid FROM accounts WHERE pid=:p", p=pid):
                print("跳过（已存在）：", pid)
                continue
            store.set_account(pid, {k: v for k, v in acc.items() if v is not None})
            print("已加密写入平台账号：", pid, "字段数", len(acc))
    # 平台会话（只迁移仍可能有效的 token；过期的适配器会自行重新登录一次）
    if a.sessions:
        for f in Path(a.sessions).glob("*.json"):
            if f.stem == "yuntu":        # 云途登录 Cookie 来自真实浏览器，网页版不用
                continue
            try:
                store.save_session(f.stem, json.loads(f.read_text(encoding="utf-8")))
                print("已加密迁移会话：", f.stem)
            except Exception as e:  # noqa
                print("会话迁移失败：", f.stem, type(e).__name__)
    print("完成")


if __name__ == "__main__":
    main()
