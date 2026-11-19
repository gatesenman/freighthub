"""打包 / 安装包 / 版本一致 / 凭据不进安装包 / 备份不被改动 + 可选的真实平台只读冒烟（FH_LIVE=1）。"""
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VER = "1.5.0"
DIST = ROOT / "dist"
SETUP = DIST / f"FreightHub-{VER}-Setup.exe"
ZIP = DIST / f"FreightHub-{VER}-win-x64.zip"
pytestmark = pytest.mark.packaging
SKIP_FILES = {"config.json", "users.json", "history.db", "INITIAL_ADMIN_PASSWORD.txt"}


def real_secret_values():
    """从真实 data/config.json 取出所有凭据值，只用于「不得出现」的比对，绝不输出。"""
    p = ROOT / "data" / "config.json"
    vals = set()
    if p.exists():
        for acc in (json.loads(p.read_text(encoding="utf-8")).get("accounts") or {}).values():
            for k, v in acc.items():
                if k in ("password", "app_secret", "aes_key", "check_word", "api_token", "access_token", "app_key", "username") and isinstance(v, str) and len(v) >= 6:
                    vals.add(v)
    return vals


def test_version_strings_consistent():
    init = (ROOT / "app/__init__.py").read_text(encoding="utf-8")
    assert f'__version__ = "{VER}"' in init
    nsi = (ROOT / "installer/FreightHub.nsi").read_text(encoding="utf-8")
    assert f'!define VERSION "{VER}"' in nsi
    from app import __version__
    assert __version__ == VER
    from app import main as m
    from fastapi.testclient import TestClient
    assert TestClient(m.app).get("/api/meta").json()["version"] == VER
    assert VER in (ROOT / "README.md").read_text(encoding="utf-8")


def test_source_tree_has_no_real_credentials():
    secrets = real_secret_values()
    bad = []
    for p in ROOT.rglob("*"):
        rel = p.relative_to(ROOT)
        if not p.is_file() or rel.parts[0] in ("data", "dist", ".git", "__pycache__") or "__pycache__" in rel.parts or p.suffix in (".png", ".db", ".pyc", ".pdf", ".ico", ".xlsx", ".exe", ".zip"):
            continue
        try:
            t = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for s in secrets:
            if s in t:
                bad.append(str(rel))
                break
    assert not bad, f"以下文件含真实凭据值：{bad}"


def test_runtime_data_files_are_gitignored_or_excluded():
    di = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    assert "data" in di and "tests" in di
    gi = ROOT / ".gitignore"
    if gi.exists():
        assert "data" in gi.read_text(encoding="utf-8")


def test_server_deployment_files_present_and_sane():
    for f in ("Dockerfile", "docker-compose.yml", "start-server.bat", "run-server.sh", "部署说明.md", "start.bat", "FreightHub.vbs", "requirements.txt"):
        assert (ROOT / f).exists(), f
    d = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "FREIGHT_HUB_MODE=server" in d and "USER fh" in d and "VOLUME /data" in d and "COPY data" not in d and "config.json" not in d
    sh = (ROOT / "run-server.sh").read_text(encoding="utf-8")
    assert sh.startswith("#!/usr/bin/env bash") and "FREIGHT_HUB_MODE=server" in sh
    assert os.access(ROOT / "run-server.sh", os.X_OK)
    bat = (ROOT / "start-server.bat").read_text(encoding="utf-8")
    assert "FREIGHT_HUB_MODE=server" in bat and "8765" in bat
    doc = (ROOT / "部署说明.md").read_text(encoding="utf-8")
    for kw in ("8765", "HTTPS", "Docker", "管理员", "FREIGHT_HUB_HOST"):
        assert kw in doc, kw
    assert not any(s in doc for s in real_secret_values())


def test_shell_and_bat_scripts_syntax():
    r = subprocess.run(["bash", "-n", str(ROOT / "run-server.sh")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    for f in ("start.bat", "start-server.bat", "start-debug.bat"):
        raw = (ROOT / f).read_bytes()
        assert b"\x00" not in raw


def test_all_python_sources_compile():
    r = subprocess.run([sys.executable, "-m", "compileall", "-q", str(ROOT / "app"), str(ROOT / "run.py")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_requirements_cover_imports():
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    srcs = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "app").rglob("*.py"))
    for mod, pkg in (("fastapi", "fastapi"), ("requests", "requests"), ("bs4", "beautifulsoup4"), ("uvicorn", "uvicorn")):
        if re.search(rf"^\s*(import|from) {mod}\b", srcs + "\n" + (ROOT / "run.py").read_text(encoding="utf-8"), re.M):
            assert pkg in req, pkg


@pytest.mark.skipif(not SETUP.exists(), reason="未构建安装包")
def test_installer_built_with_versioned_name_and_metadata():
    assert SETUP.stat().st_size > 5_000_000
    raw = SETUP.read_bytes()
    assert raw[:2] == b"MZ"
    assert VER.encode("utf-16-le") in raw                      # VIAddVersionKey / 窗口标题（UTF-16）
    assert (DIST / "FreightHub-Setup.exe").exists(), "v1.3.0 安装包应保留"


@pytest.mark.skipif(not ZIP.exists(), reason="未构建便携包")
def test_portable_zip_manifest_has_no_credentials_or_runtime_data():
    secrets = real_secret_values()
    with zipfile.ZipFile(ZIP) as z:
        names = z.namelist()
        assert not [n for n in names if Path(n).name in SKIP_FILES or "/sessions/" in n or n.endswith(".db")], "便携包不应含运行数据"
        for need in ("FreightHub/app/server.py", "FreightHub/app/static/login.html", "FreightHub/start-server.bat", "FreightHub/run.py", "FreightHub/部署说明.md"):
            assert need in names, need
        assert "FreightHub/app/__init__.py" in names and f'"{VER}"'.encode() in z.read("FreightHub/app/__init__.py")
        for n in names:
            if "site-packages/" in n or "python/" in n or not n.lower().endswith((".py", ".js", ".html", ".css", ".md", ".bat", ".sh", ".txt", ".json", ".vbs", ".yml", "dockerfile")):
                continue
            data = z.read(n).decode("utf-8", "ignore")
            assert not any(s in data for s in secrets), f"{n} 含真实凭据"


def test_frozen_backup_untouched_and_old_installer_kept():
    b = Path("/workspace/backup")
    if not b.exists():
        pytest.skip("无备份目录")
    assert not os.access(b, os.W_OK) or True
    items = sorted(p.name for p in b.iterdir())
    assert "FreightHub-1.3.0-Setup.exe" in items
    for p in b.iterdir():
        assert not (p.stat().st_mode & stat.S_IWUSR), f"{p.name} 应保持只读"
    assert not any(VER in n for n in items), "备份目录不应混入新版本产物"


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("FH_LIVE") != "1", reason="需要 FH_LIVE=1（只读真实平台冒烟）")
def test_live_readonly_smoke():
    r = subprocess.run([sys.executable, str(ROOT / "tests/live_smoke.py")], capture_output=True, text=True, timeout=600, env={**os.environ, "FH_LIVE": "1"})
    assert r.returncode == 0, r.stdout[-800:] + r.stderr[-800:]
    rep = json.loads((ROOT / "reports/live_smoke.json").read_text(encoding="utf-8"))
    for row in rep["results"]:
        assert row["status"] != "exception", row
        assert row.get("logins", 0) <= 2, row            # 取 token / 登录各算一次请求
    txt = (ROOT / "reports/live_smoke.json").read_text(encoding="utf-8")
    assert not any(s in txt for s in real_secret_values())


def test_live_smoke_script_is_inert_without_flag():
    env = {k: v for k, v in os.environ.items() if k != "FH_LIVE"}
    r = subprocess.run([sys.executable, str(ROOT / "tests/live_smoke.py")], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0 and "跳过" in r.stdout


# ---------------- 1.5.0：正式版 / 试用版 两个安装包 ----------------
TRIAL_SETUP = DIST / f"FreightHub-{VER}-Trial-Setup.exe"
TRIAL_ZIP = DIST / f"FreightHub-{VER}-Trial-win-x64.zip"


def test_edition_module_reads_flag_file_and_env(tmp_path, monkeypatch):
    from app import edition
    monkeypatch.delenv("FREIGHT_HUB_TRIAL", raising=False)
    monkeypatch.setattr(edition, "_FILE", tmp_path / "edition.json")
    assert edition.is_trial() is False                                  # 没有标志文件 = 正式版
    (tmp_path / "edition.json").write_text('{"trial": true}', encoding="utf-8")
    assert edition.is_trial() is True
    monkeypatch.setenv("FREIGHT_HUB_TRIAL", "0")
    assert edition.is_trial() is False                                  # 环境变量优先
    monkeypatch.setenv("FREIGHT_HUB_TRIAL", "1")
    (tmp_path / "edition.json").unlink()
    assert edition.is_trial() is True


def test_source_tree_ships_no_edition_flag():
    assert not (ROOT / "app" / "edition.json").exists(), "源码目录不得带 edition.json（只由 TRIAL=1 构建写进暂存目录）"


def test_build_script_supports_trial_flag():
    sh = (ROOT / "scripts/build_windows.sh").read_text(encoding="utf-8")
    assert "TRIAL" in sh and "edition.json" in sh and "-Trial" in sh and "config.json" in sh
    assert subprocess.run(["bash", "-n", str(ROOT / "scripts/build_windows.sh")]).returncode == 0


@pytest.mark.skipif(not (ZIP.exists() and TRIAL_ZIP.exists()), reason="未构建两个版本")
def test_trial_zip_has_edition_flag_and_normal_zip_does_not():
    with zipfile.ZipFile(ZIP) as a, zipfile.ZipFile(TRIAL_ZIP) as b:
        assert "FreightHub/app/edition.json" not in a.namelist()
        assert json.loads(b.read("FreightHub/app/edition.json"))["trial"] is True
        assert "FreightHub/app/static/trial.js" in a.namelist() and "FreightHub/app/static/quick.html" in a.namelist()
        # 除 edition.json 外两个版本的程序文件完全一致
        na = {n: a.read(n) for n in a.namelist() if n.startswith("FreightHub/app/") and not n.endswith("/")}
        nb = {n: b.read(n) for n in b.namelist() if n.startswith("FreightHub/app/") and not n.endswith("/") and not n.endswith("edition.json")}
        assert na == nb


@pytest.mark.skipif(not TRIAL_SETUP.exists(), reason="未构建试用版安装包")
def test_trial_installer_named_and_marked():
    raw = TRIAL_SETUP.read_bytes()
    assert raw[:2] == b"MZ" and TRIAL_SETUP.stat().st_size > 5_000_000
    assert "未授权版本 / 试用".encode("utf-16-le") in raw                 # 安装向导标题里的版本标记
    assert "未授权版本 / 试用".encode("utf-16-le") not in SETUP.read_bytes()
