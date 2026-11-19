"""不需要浏览器的静态检查 + 在浏览器里跑 JS 解析器的用例。"""
import base64
import hashlib
import json
import re
from pathlib import Path

import pytest

from conftest import EXT, EXT_ID

M = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))


def test_manifest_basics():
    assert M["manifest_version"] == 3 and M["version"] == "1.0.1"
    assert M["side_panel"]["default_path"] == "sidepanel.html"
    assert {"sidePanel", "contextMenus", "storage", "declarativeNetRequest"} <= set(M["permissions"])
    assert "open-panel" in M["commands"] and M["commands"]["open-panel"]["suggested_key"]["default"]
    assert M["background"]["service_worker"] == "background.js"
    assert "未授权" in M["description"] or "试用" in M["name"]


def test_manifest_files_exist_and_no_inline_script():
    for p in list(M["icons"].values()) + [M["side_panel"]["default_path"], M["options_ui"]["page"], M["background"]["service_worker"]]:
        assert (EXT / p).exists(), p
    for h in ("sidepanel.html", "options.html"):
        t = (EXT / h).read_text(encoding="utf-8")
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", t), "MV3 CSP 不允许内联脚本：" + h
        for src in re.findall(r'(?:src|href)="([^"]+)"', t):
            assert not src.startswith("http"), f"不得引用远程资源：{src}"
            assert (EXT / src).exists(), src


def test_fixed_key_matches_id():
    der = base64.b64decode(M["key"])
    h = hashlib.sha256(der).hexdigest()[:32]
    eid = "".join(chr(ord("a") + int(c, 16)) for c in h)
    assert eid == EXT_ID


def test_no_secrets_or_remote_code_in_source():
    bad = re.compile(r"eval\(|new Function\(|https?://[^\"' ]*\.(?:js)\b|localStorage\.setItem\([^)]*pass|password\s*[:=]\s*[\"'][^\"']+[\"']", re.I)
    for f in list((EXT / "js").glob("*.js")) + [EXT / "background.js"]:
        t = f.read_text(encoding="utf-8")
        assert not bad.search(t), f.name
        assert "chrome.storage" not in t or "password" not in re.findall(r"storage\.\w+\.set\(([^)]*)\)", t).__str__().lower(), f.name


def test_only_expected_storage_keys():
    """扩展只存：后端设置 + 上次输入 + 一次性的右键文字；密码与 CSRF 令牌不落盘。"""
    allowed = {"backend", "webUrl", "desktopUrl", "lastInput", "pendingQuery"}
    seen = set()
    for f in list((EXT / "js").glob("*.js")) + [EXT / "background.js"]:
        t = f.read_text(encoding="utf-8")
        for k in re.findall(r"storage\.(?:local|session)\.set\(\{\s*(\w+)", t):
            seen.add(k)
            assert k in allowed, (f.name, k)
        assert not re.search(r"storage\.\w+\.set\([^)]*(csrf|pass)", t, re.I), f.name
    assert {"lastInput", "pendingQuery"} <= seen


COUNTRIES = [{"code": "US", "name": "美国"}, {"code": "GB", "name": "英国"}, {"code": "DE", "name": "德国"}, {"code": "JP", "name": "日本"}, {"code": "KR", "name": "韩国"}]
CASES = [
    ("US 1kg", ("US", 1, 0, 0, 0)),
    ("美国 2kg 30x20x10", ("US", 2, 30, 20, 10)),
    ("us 1.5", ("US", 1.5, 0, 0, 0)),
    ("英国 500g", ("GB", 0.5, 0, 0, 0)),
    ("UK 2斤", ("GB", 1, 0, 0, 0)),
    ("德国 3公斤 10*10*10cm", ("DE", 3, 10, 10, 10)),
    ("30×20×10 JP 0.3kg", ("JP", 0.3, 30, 20, 10)),
    ("  韩国，2 kg ", ("KR", 2, 0, 0, 0)),
    ("USA 0.25kg", ("US", 0.25, 0, 0, 0)),
]
BAD = [("", "请输入"), ("US", "重量"), ("US 0kg", "大于 0"), ("US -1", "不能是负数"), ("火星 1kg", "没认出"), ("1kg", "没认出"), ("US 999999kg", "超出范围")]


@pytest.fixture
def page(ext_page):
    return ext_page


@pytest.mark.parametrize("text,exp", CASES)
def test_parse_ok(ext_page, text, exp):
    r = ext_page.evaluate("([t,c]) => FH.parseQuick(t,c)", [text, COUNTRIES])
    assert (r["country"], r["weight"], r["length"], r["width"], r["height"]) == exp


@pytest.mark.parametrize("text,frag", BAD)
def test_parse_bad(ext_page, text, frag):
    err = ext_page.evaluate("([t,c]) => { try { FH.parseQuick(t,c); return ''; } catch(e) { return e.message; } }", [text, COUNTRIES])
    assert frag in err, err
