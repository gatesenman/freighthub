"""创建 / 重置给客户试用的 demo 账号（角色 demo：只能询价 + 自己的历史；每小时最多 N 次询价；不能改密码）。

用法：python scripts/seed_demo.py [--qph 30] [--reset-password]
口令只写到 DEMO_ACCOUNT.txt（0600）；这个口令本来就是给客户的，但脚本不在终端打印它。
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
sys.path.insert(0, str(ROOT / "scripts"))
ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"      # 去掉 i l o 0 1，客户手敲不会看错


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qph", type=int, default=30)
    ap.add_argument("--reset-password", action="store_true")
    ap.add_argument("--file", default=str(ROOT / "DEMO_ACCOUNT.txt"))
    ap.add_argument("--url", default="https://freight.gaosichun.dev")
    a = ap.parse_args()
    from seed_cloud import load_env
    load_env()
    from app.cloud import seed_roles
    from app.db import Db, database_url
    from app.server import hash_password
    db = Db(database_url())
    db.ensure_schema(seed=seed_roles)
    seed_roles(db)
    exists = db.one("SELECT username_lc FROM users WHERE username_lc='demo'")
    f = Path(a.file)
    if exists and f.exists() and not a.reset_password:
        print("demo 账号已存在，口令文件保持不变")
        pw = None
    else:
        pw = "".join(secrets.choice(ALPHABET) for _ in range(10))
        now = time.time()
        if exists:
            db.x("UPDATE users SET pw=:p, role='demo', disabled=0, must_change=0 WHERE username_lc='demo'", p=hash_password(pw))
            db.x("DELETE FROM sessions WHERE username_lc='demo'")
        else:
            db.x("INSERT INTO users (username_lc, username, pw, role, disabled, created, must_change) VALUES ('demo','demo',:p,'demo',0,:t,0)", p=hash_password(pw), t=now)
        fd = os.open(str(f), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"试用网址：{a.url}\n用户名：demo\n密码：{pw}\n说明：试用账号，只能询价和查看自己的查询历史；每小时最多查询 {a.qph} 次；页面带“未授权版本 / 试用”水印。\n")
        os.chmod(str(f), 0o600)
        print("已写入", f)
    db.x("DELETE FROM meta WHERE k='policy:demo'")
    db.x("INSERT INTO meta (k, v) VALUES ('policy:demo', :v)", v=json.dumps({"qph": a.qph, "no_pw": True, "trial": True}))
    print("策略：每小时最多询价", a.qph, "次；不能改密码；角色 demo")


if __name__ == "__main__":
    main()
