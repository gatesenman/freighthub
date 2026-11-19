"""快速询价浮窗的后端与纯逻辑：文本解析、聚合最低价、浮窗定位 / 夹紧 / DPI 数学、热键解析、设置、聚合接口、试用版标志。"""
import json

import pytest

from app import quick
from tests.suite.conftest import FAKE, ois_ok_routes

pytestmark = pytest.mark.unit


# ------------------------------------------------------------ 文本解析
@pytest.mark.parametrize("text,exp", [
    ("美国 2kg", ("US", 2.0, 0, 0, 0)),
    ("US 1kg", ("US", 1.0, 0, 0, 0)),
    ("us 1.5", ("US", 1.5, 0, 0, 0)),
    ("美国 US 2", ("US", 2.0, 0, 0, 0)),
    ("2kg 美国", ("US", 2.0, 0, 0, 0)),
    ("德国 500g", ("DE", 0.5, 0, 0, 0)),
    ("英国 4斤", ("GB", 2.0, 0, 0, 0)),
    ("UK 3 公斤", ("GB", 3.0, 0, 0, 0)),
    ("US 1kg 30x20x10", ("US", 1.0, 30, 20, 10)),
    ("美国 2 30×20×10cm", ("US", 2.0, 30, 20, 10)),
    ("30*20*10 US 2.5kg", ("US", 2.5, 30, 20, 10)),
    ("  法国   0.35kg  ", ("FR", 0.35, 0, 0, 0)),
])
def test_parse_quick_text_ok(text, exp):
    p = quick.parse_quick_text(text)
    assert (p["country"], p["weight"], p["length"], p["width"], p["height"]) == exp


@pytest.mark.parametrize("text,frag", [
    ("", "请输入"), ("   ", "请输入"), ("美国", "重量"), ("2kg", "没认出"), ("火星 2kg", "没认出"),
    ("US 0kg", "重量"), ("US -1", "重量"), ("US 999999kg", "超出"), ("US 1kg 99999x1x1", "超出"),
])
def test_parse_quick_text_errors_are_chinese_and_specific(text, frag):
    with pytest.raises(ValueError) as e:
        quick.parse_quick_text(text)
    assert frag in str(e.value)


def test_resolve_country_variants():
    assert quick.resolve_country("美国")[0] == "US" and quick.resolve_country("美国 US")[0] == "US"
    assert quick.resolve_country("de")[0] == "DE" and quick.resolve_country("") is None and quick.resolve_country("zzz") is None


# ------------------------------------------------------------ 聚合
def row(p, ch, total, cur="CNY", days="5-8"):
    return {"platform": p, "platform_name": p, "channel": ch, "total": total, "currency": cur, "days": days, "days_min": 5, "days_max": 8}


def info(i, state="ok", n=0):
    return {"id": i, "name": i.upper(), "state": state, "count": n, "message": "", "elapsed_ms": 1}


def test_summarize_per_platform_lowest_and_overall():
    rows = [row("a", "A1", 100), row("a", "A2", 80), row("b", "B1", 70), row("b", "B2", 90), row("c", "C1", 75)]
    plats, overall = quick.summarize([info("a", n=2), info("b", n=2), info("c", n=1), info("d", "error")], rows)
    best = {p["id"]: p["best"] for p in plats}
    assert best["a"]["total"] == 80 and best["a"]["channel"] == "A2" and best["b"]["total"] == 70 and best["c"]["total"] == 75 and best["d"] is None
    assert overall["platform"] == "b" and overall["total"] == 70 and overall["channel"] == "B1"


def test_summarize_cny_preferred_over_cheaper_foreign_currency():
    rows = [row("a", "A1", 5, cur="USD"), row("a", "A2", 300), row("b", "B1", 1, cur="EUR")]
    plats, overall = quick.summarize([info("a"), info("b")], rows)
    assert plats[0]["best"]["currency"] == "CNY" and plats[0]["best"]["total"] == 300
    assert plats[1]["best"]["currency"] == "EUR"                 # 只有外币：保留并带币种
    assert overall["currency"] == "CNY" and overall["platform"] == "a"


def test_summarize_handles_nothing_and_garbage_rows():
    plats, overall = quick.summarize([info("a", "error")], [])
    assert overall is None and plats[0]["best"] is None
    assert quick.best_row([{"total": 0}, {"total": -3}, {"total": None}, {"total": "9"}]) is None


def test_summarize_tie_is_stable_and_never_mixes_platform_rows():
    plats, overall = quick.summarize([info("a"), info("b")], [row("a", "A", 50), row("b", "B", 50)])
    assert overall["total"] == 50 and overall["platform"] in ("a", "b")
    assert plats[0]["best"]["channel"] == "A" and plats[1]["best"]["channel"] == "B"


# ------------------------------------------------------------ 浮窗定位
WA = (0, 0, 1920, 1040)          # 1080p 主屏工作区（任务栏 40px）


def test_place_popup_default_is_right_below_cursor():
    assert quick.place_popup((500, 400), (460, 540), WA) == (514, 414)


def test_place_popup_flips_left_near_right_edge_and_up_near_bottom():
    x, y = quick.place_popup((1900, 300), (460, 540), WA)
    assert x == 1900 - 14 - 460 and y == 314
    x, y = quick.place_popup((500, 1030), (460, 540), WA)
    assert y == 1030 - 14 - 540 and x == 514
    x, y = quick.place_popup((1915, 1035), (460, 540), WA)
    assert (x, y) == (1915 - 14 - 460, 1035 - 14 - 540)


def test_place_popup_always_clamped_inside_work_area_for_any_cursor():
    for cx in range(-50, 2000, 97):
        for cy in range(-50, 1100, 89):
            x, y = quick.place_popup((cx, cy), (460, 540), WA)
            assert 8 <= x and x + 460 <= 1920 - 8 and 8 <= y and y + 540 <= 1040 - 8, (cx, cy, x, y)


def test_place_popup_bigger_than_screen_sticks_top_left():
    assert quick.place_popup((100, 100), (3000, 2000), (0, 0, 800, 600)) == (8, 8)


def test_pick_monitor_inside_negative_coordinates_and_gap():
    left, main = (-1920, 0, 0, 1040), (0, 0, 1920, 1040)
    assert quick.pick_monitor((-100, 500), [main, left]) == left
    assert quick.pick_monitor((0, 500), [main, left]) == main
    assert quick.pick_monitor((-1, 500), [main, left]) == left
    gap = [(0, 0, 1000, 800), (1100, 0, 2100, 800)]
    assert quick.pick_monitor((1070, 400), gap) == (1100, 0, 2100, 800)       # 缝隙里取最近
    assert quick.pick_monitor((1010, 400), gap) == (0, 0, 1000, 800)
    with pytest.raises(ValueError):
        quick.pick_monitor((0, 0), [])


def test_place_popup_on_left_secondary_monitor_with_negative_origin():
    left = (-1920, 0, 0, 1040)
    x, y = quick.place_popup((-30, 200), (460, 540), left)           # 靠右边缘 → 翻到鼠标左边，仍在副屏内
    assert -1920 + 8 <= x and x + 460 <= -8 and x == -30 - 14 - 460
    x, y = quick.place_popup((-1915, 5), (460, 540), left)
    assert x >= -1912 and y >= 8


def test_popup_geometry_scales_with_dpi_of_monitor_under_cursor():
    mons = [(0, 0, 1920, 1040), (1920, 0, 5760, 2120)]               # 主屏 100%，右边 4K 200%
    scale = lambda r: 2.0 if r[0] >= 1920 else 1.0
    g1 = quick.popup_geometry((500, 400), (460, 540), mons, scale)
    assert (g1["w"], g1["h"], g1["scale"]) == (460, 540, 1.0)
    g2 = quick.popup_geometry((3000, 500), (460, 540), mons, scale)
    assert (g2["w"], g2["h"], g2["scale"]) == (920, 1080, 2.0)
    assert g2["x"] == 3000 + 28 and g2["y"] == 500 + 28               # 偏移同样按 DPI 放大
    assert g2["monitor"] == [1920, 0, 5760, 2120]
    g3 = quick.popup_geometry((5750, 2100), (460, 540), mons, scale)
    assert g3["x"] + g3["w"] <= 5760 - 16 and g3["y"] + g3["h"] <= 2120 - 16


@pytest.mark.parametrize("sc", [1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0])
def test_popup_geometry_stays_inside_for_common_scales(sc):
    mon = (0, 0, 2560, 1400)
    for cur in [(0, 0), (2559, 1399), (1280, 700), (10, 1390), (2550, 10)]:
        g = quick.popup_geometry(cur, (460, 540), [mon], lambda r: sc)
        assert g["x"] >= 0 and g["y"] >= 0 and g["x"] + g["w"] <= 2560 and g["y"] + g["h"] <= 1400, (sc, cur, g)


def test_popup_geometry_small_screen_shrinks_window_and_bad_scale_falls_back():
    g = quick.popup_geometry((100, 100), (460, 540), [(0, 0, 800, 480)])
    assert g["h"] <= 480 - 16 and g["y"] >= 8
    assert quick.popup_geometry((100, 100), (460, 540), [(0, 0, 1920, 1080)], lambda r: 0)["scale"] == 1.0
    assert quick.popup_geometry((100, 100), (460, 540), [(0, 0, 1920, 1080)], lambda r: 99)["scale"] == 1.0


# ------------------------------------------------------------ 热键
def test_parse_and_format_hotkey():
    assert quick.parse_hotkey("Ctrl+Alt+Q") == (quick.MOD_CONTROL | quick.MOD_ALT, ord("Q"))
    assert quick.parse_hotkey("ctrl + shift + f9") == (quick.MOD_CONTROL | quick.MOD_SHIFT, 0x78)
    assert quick.parse_hotkey("Win+Space")[1] == 0x20
    assert quick.format_hotkey("alt+ctrl+q") == "Ctrl+Alt+Q" and quick.format_hotkey("CTRL+ALT+F12") == "Ctrl+Alt+F12"
    assert quick.format_hotkey("ctrl+alt+5") == "Ctrl+Alt+5" and quick.format_hotkey("ctrl+alt+space") == "Ctrl+Alt+Space"


@pytest.mark.parametrize("bad", ["", "Q", "Ctrl", "Ctrl+", "Shift+Q", "Ctrl+Alt+", "Foo+Q", "Ctrl+Alt+Qq", "Ctrl+Alt+F25", "Ctrl++"])
def test_parse_hotkey_rejects_bad(bad):
    with pytest.raises(ValueError):
        quick.parse_hotkey(bad)


# ------------------------------------------------------------ 设置
@pytest.fixture
def datadir(tmp_path, monkeypatch):
    monkeypatch.setenv("FREIGHT_HUB_DATA", str(tmp_path))
    return tmp_path


def test_settings_defaults_roundtrip_and_validation(datadir):
    from app import settings
    d = settings.load()
    assert d["hotkey"] == "Ctrl+Alt+Q" and d["popup_autoclose"] is True and d["close_to_tray"] is False
    cur = settings.update({"hotkey": "ctrl+shift+f8", "popup_autoclose": False, "quick_last": {"country": "美国 US", "weight": "2"}})
    assert cur["hotkey"] == "Ctrl+Shift+F8" and cur["popup_autoclose"] is False and cur["quick_last"]["weight"] == "2"
    assert settings.load() == cur
    with pytest.raises(ValueError):
        settings.update({"hotkey": "Q"})
    with pytest.raises(ValueError):
        settings.update({"popup_autoclose": "yes"})
    settings.update({"evil": 1})
    assert "evil" not in json.loads((datadir / "settings.json").read_text(encoding="utf-8"))


def test_settings_corrupt_file_falls_back_to_defaults(datadir):
    from app import settings
    (datadir / "settings.json").write_text("{not json", encoding="utf-8")
    assert settings.load()["hotkey"] == "Ctrl+Alt+Q"
    (datadir / "settings.json").write_text(json.dumps({"hotkey": "garbage", "popup_autoclose": "x"}), encoding="utf-8")
    d = settings.load()
    assert d["hotkey"] == "Ctrl+Alt+Q" and d["popup_autoclose"] is True


# ------------------------------------------------------------ 聚合接口
@pytest.fixture
def yt(net, setacct):
    setacct("yutong", auth_mode="api", app_key="FAKE-APPKEY-0001", app_secret=FAKE["app_secret"])
    ois_ok_routes(net, quote_items=[
        {"code": "P1", "name": "美国专线小包", "standardFeeSum": 123.45, "standardCur": "CNY", "aging": "5-8"},
        {"code": "P2", "name": "美国经济专线", "standardFeeSum": 99.9, "standardCur": "CNY", "aging": "8-12"}])
    return net


def hy_routes(net, price=None, fail=False):
    """环洋（host 含 ekttech）：插到最前面，覆盖 yutong 用的同路径路由。"""
    if fail:
        rs = [("ekttech", {"result_code": 1, "body": {"ack": "false", "message": "appKey invalid"}})]
    else:
        rs = [("ekttech.net/pos-web/ois/order/getAuth", {"body": {"ack": "true", "token": "FAKE-T2", "clno": "C1", "coid": "CO1"}}),
              ("ekttech.net/pos-web/ois/order/getProductList", {"result_code": 0, "body": [{"code": "H1", "hubType": "专线"}]}),
              ("ekttech.net/pos-web/tms/expose/queryQuoteList", {"result_code": 0, "body": [{"code": "H1", "name": "环洋美国专线", "standardFeeSum": price, "standardCur": "CNY", "aging": "6-9"}]})]
    for frag, resp in reversed(rs):
        net.routes.insert(0, {"frag": frag, "resp": resp, "method": None, "n": 0})


def test_quick_endpoint_text_input_per_platform_lowest(client, yt):
    r = client.post("/api/quote/quick", json={"text": "美国 2kg", "platforms": ["yutong"]})
    assert r.status_code == 200
    d = r.json()
    assert d["query"] == {"country": "US", "country_name": "美国", "weight": 2.0, "postcode": "", "length": 0.0, "width": 0.0, "height": 0.0}
    p = d["platforms"][0]
    assert p["id"] == "yutong" and p["state"] == "ok" and p["count"] == 2 and p["best"]["total"] == 99.9 and p["best"]["days"] == "8-12"
    assert d["overall"]["platform"] == "yutong" and d["overall"]["total"] == 99.9 and d["history_id"] is None and len(d["tokens"]) == 1


def test_quick_endpoint_structured_fields_and_dims_sent_to_platform(client, yt):
    r = client.post("/api/quote/quick", json={"country": "us", "weight": 1.2, "length": 30, "width": 20, "height": 10, "platforms": ["yutong"]})
    assert r.status_code == 200 and r.json()["query"]["length"] == 30
    sent = [c for c in yt.calls if "queryQuoteList" in c["url"]]
    assert sent


def test_quick_endpoint_partial_failure_keeps_other_platforms(client, yt, setacct):
    setacct("huanyang", auth_mode="api", app_key="K", app_secret="S")
    hy_routes(yt, fail=True)
    r = client.post("/api/quote/quick", json={"text": "US 1kg", "platforms": ["yutong", "huanyang"]}).json()
    st = {p["id"]: p for p in r["platforms"]}
    assert st["yutong"]["state"] == "ok" and st["huanyang"]["state"] != "ok" and st["huanyang"]["best"] is None and st["huanyang"]["message"]
    assert r["overall"]["platform"] == "yutong"


def test_quick_endpoint_unconfigured_and_unknown_platforms(client, yt):
    r = client.post("/api/quote/quick", json={"text": "US 1kg", "platforms": ["yuntu"]}).json()
    assert r["platforms"][0]["state"] == "no_account" and r["overall"] is None
    assert client.post("/api/quote/quick", json={"text": "US 1kg", "platforms": ["nope"]}).status_code == 400


@pytest.mark.parametrize("body,frag", [
    ({"text": "火星 1kg"}, "没认出"), ({"text": "美国"}, "重量"), ({"country": "", "weight": 1}, "目的国"), ({"country": "US", "weight": 0}, "重量"),
    ({"country": "US", "weight": -1}, "重量"),
])
def test_quick_endpoint_validation_errors_are_chinese(client, yt, body, frag):
    r = client.post("/api/quote/quick", json=body)
    assert r.status_code == 400 and frag in r.json()["detail"]


def test_quick_endpoint_rejects_nan_and_oversize(client, yt):
    assert client.post("/api/quote/quick", content='{"country":"US","weight":NaN}', headers={"Content-Type": "application/json"}).status_code in (400, 422)
    assert client.post("/api/quote/quick", json={"country": "US", "weight": 1e9}).status_code == 422


def test_quick_save_flag_writes_history_default_does_not(client, yt):
    client.post("/api/quote/quick", json={"text": "US 1kg", "platforms": ["yutong"]})
    assert client.get("/api/history").json() == []
    r = client.post("/api/quote/quick", json={"text": "US 1kg", "platforms": ["yutong"], "save": True}).json()
    assert r["history_id"] and len(client.get("/api/history").json()) == 1


def test_quick_commit_merges_platform_results_into_one_history_record(client, yt, setacct):
    setacct("huanyang", auth_mode="api", app_key="K", app_secret="S")
    hy_routes(yt, price=123.45)
    toks = []
    for pid in ("yutong", "huanyang"):
        toks += client.post("/api/quote/quick", json={"text": "美国 2kg", "platforms": [pid]}).json()["tokens"]
    r = client.post("/api/quote/quick/commit", json={"tokens": toks})
    assert r.status_code == 200
    h = client.get(f"/api/history/{r.json()['history_id']}").json()
    assert h["query"]["country"] == "US" and {p["id"] for p in h["platforms"]} == {"yutong", "huanyang"}
    assert [(x["platform"], x["total"]) for x in h["rows"]] == [("yutong", 99.9), ("yutong", 123.45), ("huanyang", 123.45)] or sorted(x["total"] for x in h["rows"]) == [99.9, 123.45, 123.45]
    assert {x["platform"] for x in h["rows"]} == {"yutong", "huanyang"}


def test_quick_commit_unknown_or_expired_tokens(client, yt):
    assert client.post("/api/quote/quick/commit", json={"tokens": ["nope"]}).status_code == 404
    assert client.post("/api/quote/quick/commit", json={"tokens": []}).status_code == 404
    assert client.post("/api/quote/quick/commit", json={"tokens": ["x"] * 40}).status_code == 422


def test_quick_cache_is_bounded(client, yt):
    from app import main as m
    for _ in range(70):
        client.post("/api/quote/quick", json={"text": "US 1kg", "platforms": ["yuntu"]})
    assert len(m._quick_cache) <= 60


def test_shell_status_and_settings_endpoints_without_shell(client, datadir):
    st = client.get("/api/shell/status").json()
    assert st["active"] is False and "桌面外壳" in st["message"]
    r = client.put("/api/shell/settings", json={"hotkey": "ctrl+alt+w", "popup_autoclose": False})
    assert r.status_code == 200 and r.json()["settings"]["hotkey"] == "Ctrl+Alt+W"
    assert client.get("/api/shell/settings").json()["popup_autoclose"] is False
    bad = client.put("/api/shell/settings", json={"hotkey": "W"})
    assert bad.status_code == 400 and "修饰键" in bad.json()["detail"]
    assert client.post("/api/shell/popup", json={"text": "US 1kg"}).status_code == 409


def test_shell_settings_change_calls_hook_and_reports_conflict(client, datadir):
    from app import main as m
    seen = []
    m.shell_hooks["settings"] = lambda cur: (seen.append(cur["hotkey"]) or {"active": True, "hotkey_ok": False, "message": "这个快捷键已被其他程序占用"})
    try:
        r = client.put("/api/shell/settings", json={"hotkey": "ctrl+alt+z"}).json()
        assert seen == ["Ctrl+Alt+Z"] and r["shell"]["hotkey_ok"] is False and "占用" in r["shell"]["message"]
        client.put("/api/shell/settings", json={"popup_autoclose": True})
        assert seen == ["Ctrl+Alt+Z"]                      # 与外壳无关的设置不会重注册
    finally:
        m.shell_hooks["settings"] = None


def test_quick_page_served_and_static_assets(client):
    for u in ("/quick", "/static/quick.js", "/static/quick.css", "/static/trial.js"):
        assert client.get(u).status_code == 200, u
    assert "快速询价" in client.get("/quick").text


# ------------------------------------------------------------ 试用版
def test_meta_trial_flag_env_and_file(client, monkeypatch, tmp_path):
    from app import edition
    monkeypatch.delenv("FREIGHT_HUB_TRIAL", raising=False)
    assert client.get("/api/meta").json()["trial"] is False
    monkeypatch.setenv("FREIGHT_HUB_TRIAL", "1")
    assert client.get("/api/meta").json()["trial"] is True
    monkeypatch.setenv("FREIGHT_HUB_TRIAL", "0")
    assert client.get("/api/meta").json()["trial"] is False
    monkeypatch.delenv("FREIGHT_HUB_TRIAL")
    f = tmp_path / "edition.json"
    f.write_text('{"trial": true}')
    monkeypatch.setattr(edition, "_FILE", f)
    assert edition.is_trial() is True
    f.write_text("garbage")
    assert edition.is_trial() is False


def test_server_mode_user_routes_include_quick(client):
    from app import server
    assert server.required_role("POST", "/api/quote/quick") == "user" and server.required_role("POST", "/api/quote/quick/commit") == "user"
    assert server.required_role("PUT", "/api/shell/settings") == "admin" and server.required_role("GET", "/quick") == "user"
