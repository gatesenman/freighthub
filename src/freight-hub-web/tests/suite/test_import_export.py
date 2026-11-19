"""批量导入账号（xlsx/csv/原四列表/重复/覆盖/坏行/编码/API+登录同平台优先）与导出、模板。"""
import base64
import io
import json
import zipfile

import pytest

from app import importer
from app.xlsx import read_xlsx, write_xlsx
from tests.suite.conftest import FAKE

pytestmark = pytest.mark.unit
HDR = importer.COLUMNS


def b64(b):
    return base64.b64encode(b).decode()


def xlsx_bytes(rows, header=True, name="账号配置"):
    return write_xlsx([{"name": name, "rows": ([HDR] if header else []) + rows, "widths": [12] * 12, "header": header}])


def csv_bytes(rows, enc="utf-8-sig", delim=","):
    lines = [delim.join([HDR_ for HDR_ in HDR])] + [delim.join(str(c) for c in r) for r in rows]
    return ("\n".join(lines)).encode(enc)


def row(platform="雨桐", mode="API", user="", pwd="", key="", sec="", aes="", cust="", url="", extra="", note="", en="是"):
    return [platform, mode, user, pwd, key, sec, aes, cust, url, extra, note, en]


def preview(client, data, name):
    r = client.post("/api/accounts/import/preview", json={"filename": name, "data": b64(data)})
    assert r.status_code == 200, r.text
    return r.json()


def apply(client, batch, rows=None):
    r = client.post("/api/accounts/import/apply", json={"batch": batch, "rows": rows})
    assert r.status_code == 200, r.text
    return r.json()


def actions(j):
    return [x["action"] for x in j["rows"]]


# ---- 格式 ----
@pytest.mark.parametrize("kind", ["xlsx", "csv-utf8bom", "csv-gbk", "csv-utf8", "csv-tab"])
def test_same_content_all_formats(client, store, kind):
    rows = [row("雨桐", "API", key="FAKE-K1", sec=FAKE["app_secret"]), row("中航", "登录", user="FAKE-U", pwd=FAKE["password"])]
    if kind == "xlsx":
        data, fn = xlsx_bytes(rows), "a.xlsx"
    elif kind == "csv-utf8bom":
        data, fn = csv_bytes(rows), "a.csv"
    elif kind == "csv-gbk":
        data, fn = csv_bytes(rows, enc="gb18030"), "a.csv"
    elif kind == "csv-utf8":
        data, fn = csv_bytes(rows, enc="utf-8"), "a.csv"
    else:
        data, fn = csv_bytes(rows, delim="\t"), "a.txt"
    j = preview(client, data, fn)
    assert actions(j) == ["add", "add"], j
    apply(client, j["batch"])
    assert store.get_account("yutong")["app_key"] == "FAKE-K1" and store.get_account("zhonghang")["username"] == "FAKE-U"


def test_preview_never_returns_secret_values(client):
    j = preview(client, xlsx_bytes([row("雨桐", "API", key="FAKE-K1", sec=FAKE["app_secret"])]), "a.xlsx")
    assert FAKE["app_secret"] not in json.dumps(j, ensure_ascii=False)
    f = {x["key"]: x for x in j["rows"][0]["fields"]}
    assert f["app_secret"]["value"] == "" and f["app_secret"]["secret"] and f["app_key"]["value"] == "FAKE-K1"


def test_original_four_column_sheet_with_and_without_header(client, store):
    four = [["平台名", "账号", "密码", "网址"], ["中航", "FAKE-U", FAKE["password"], "http://x.example"]]
    j = preview(client, write_xlsx([{"name": "Sheet1", "rows": four, "widths": [10] * 4}]), "旧表.xlsx")
    assert actions(j) == ["add"] and j["rows"][0]["mode"] == "login"
    noh = [["中航", "FAKE-U", FAKE["password"], "http://x.example"], ["云途", "FAKE-U2", "FAKE-P2", ""]]
    j = preview(client, write_xlsx([{"name": "Sheet1", "rows": noh, "widths": [10] * 4}]), "旧表2.xlsx")
    assert actions(j) == ["add", "add"] and "无表头" in j["rows"][0]["message"]
    apply(client, j["batch"])
    assert store.get_account("yuntu")["username"] == "FAKE-U2"


def test_header_aliases_and_case_and_fullwidth_paren(client):
    csvb = "Platform,Mode,Username,Password\n中航,login,FAKE-U,FAKE-P\n".encode("utf-8")
    assert actions(preview(client, csvb, "a.csv")) == ["add"]
    csvb = "平台（必填）,授权方式（登录/API）,账号,密码\n中航,登录,FAKE-U,FAKE-P\n".encode("utf-8")
    assert actions(preview(client, csvb, "a.csv")) == ["add"]


def test_picks_named_sheet_or_first_nonempty(client):
    data = write_xlsx([{"name": "空", "rows": [[]], "widths": [5]}, {"name": "数据", "rows": [HDR, row("中航", "登录", user="U", pwd="P")], "widths": [5] * 12}])
    assert actions(preview(client, data, "a.xlsx")) == ["add"]


def test_xlsx_sniffed_by_magic_even_with_wrong_extension(client):
    assert actions(preview(client, xlsx_bytes([row("中航", "登录", user="U", pwd="P")]), "a.csv")) == ["add"]


# ---- 行为 ----
def test_overwrite_vs_same_vs_add(client, store):
    r = [row("雨桐", "API", key="K1", sec="S1")]
    j = preview(client, xlsx_bytes(r), "a.xlsx")
    assert actions(j) == ["add"]
    apply(client, j["batch"])
    assert actions(preview(client, xlsx_bytes(r), "a.xlsx")) == ["same"]
    j = preview(client, xlsx_bytes([row("雨桐", "API", key="K2", sec="S1")]), "a.xlsx")
    assert actions(j) == ["overwrite"] and j["rows"][0]["fields"][0]["changed"]
    apply(client, j["batch"])
    assert store.get_account("yutong")["app_key"] == "K2"


def test_blank_secret_keeps_old_and_masked_values_never_overwrite(client, store):
    store.set_account("yutong", {"app_key": "K1", "app_secret": "REAL-SECRET-1", "auth_mode": "api"})
    j = preview(client, xlsx_bytes([row("雨桐", "API", key="K1", sec="")]), "a.xlsx")
    assert actions(j) == ["same"]
    j = preview(client, xlsx_bytes([row("雨桐", "API", key="K9", sec="RE****-1")]), "a.xlsx")
    apply(client, j["batch"])
    a = store.get_account("yutong")
    assert a["app_secret"] == "REAL-SECRET-1" and a["app_key"] == "K9"


def test_duplicate_rows_later_wins(client, store):
    j = preview(client, xlsx_bytes([row("雨桐", "API", key="OLD", sec="S"), row("雨桐", "API", key="NEW", sec="S")]), "a.xlsx")
    assert actions(j) == ["dup", "add"]
    apply(client, j["batch"])
    assert store.get_account("yutong")["app_key"] == "NEW"


def test_same_platform_api_and_login_both_saved_api_active(client, store):
    j = preview(client, xlsx_bytes([row("中航", "登录", user="U", pwd="P"), row("中航", "API", key="K", sec="S")]), "a.xlsx")
    assert actions(j) == ["add", "add"] and "两套都保存" in j["rows"][0]["message"]
    res = apply(client, j["batch"])["results"]
    a = store.get_account("zhonghang")
    assert a["username"] == "U" and a["app_key"] == "K" and a["auth_mode"] == "api"
    assert [x["active"] for x in res].count(True) == 1, "每个平台只测一次连接，避免多次登录"


def test_api_listed_first_still_wins(client, store):
    j = preview(client, xlsx_bytes([row("中航", "API", key="K", sec="S"), row("中航", "登录", user="U", pwd="P")]), "a.xlsx")
    apply(client, j["batch"])
    assert store.get_account("zhonghang")["auth_mode"] == "api"


@pytest.mark.parametrize("r,frag", [
    (row("不存在的货代", "API", key="k", sec="s"), "无法识别平台"), (row("雨桐", "乱写", key="k", sec="s"), "无法识别"),
    (row("雨桐", "API", key="k"), "缺少"), (row("雨桐", "API"), "缺少"), (row("雨桐", "API", key="k", sec="s", extra="bogus=1"), "不是该平台的字段"),
    (row("顺丰", "API", key="k", sec="s", aes="a", cust="c", extra="env=moon"), "不在可选项"), (row("燕文", "API", key="1"), "缺少")])
def test_bad_rows_are_flagged_with_reason_and_never_written(client, store, r, frag):
    j = preview(client, xlsx_bytes([r]), "a.xlsx")
    assert j["rows"][0]["action"] == "error" and frag in j["rows"][0]["message"]
    apply(client, j["batch"])
    assert store.get_account("yutong") == {} and store.get_account("shunfeng") == {} and store.get_account("yanwen") == {}


def test_good_and_bad_rows_mixed_apply_only_good(client, store):
    j = preview(client, xlsx_bytes([row("雨桐", "API", key="K", sec="S"), row("???", "API", key="k", sec="s"), row("欣航", "")]), "a.xlsx")
    assert actions(j) == ["add", "error", "skip"]
    res = apply(client, j["batch"])["results"]
    assert [x["pid"] for x in res] == ["yutong"]


def test_apply_with_row_selection_only_selected(client, store):
    j = preview(client, xlsx_bytes([row("雨桐", "API", key="K", sec="S"), row("中航", "登录", user="U", pwd="P")]), "a.xlsx")
    apply(client, j["batch"], rows=[1])
    assert store.get_account("yutong") == {} and store.get_account("zhonghang")["username"] == "U"


def test_batch_is_single_use(client):
    j = preview(client, xlsx_bytes([row("中航", "登录", user="U", pwd="P")]), "a.xlsx")
    apply(client, j["batch"])
    assert client.post("/api/accounts/import/apply", json={"batch": j["batch"]}).status_code == 400


def test_apply_clears_sessions_and_marks_enabled(client, store):
    store.save_session("zhonghang", {"token": "old"})
    store.set_account("zhonghang", {"username": "OLD", "password": "x"})
    j = preview(client, xlsx_bytes([row("中航", "登录", user="NEW", pwd="P", en="否")]), "a.xlsx")
    apply(client, j["batch"])
    assert store.load_session("zhonghang") is None and store.get_account("zhonghang")["enabled"] is False


def test_import_unlocks_login_lock_because_credentials_changed(client, store):
    store.lock_login("zhonghang")
    j = preview(client, xlsx_bytes([row("中航", "登录", user="NEW", pwd="P")]), "a.xlsx")
    apply(client, j["batch"])
    assert not store.login_locked("zhonghang")


def test_platform_aliases_recognised(client):
    names = ["云途", "YunExpress", "中航", "欣航", "环洋", "燕文", "顺丰国际", "sf.global", "雨桐"]
    j = preview(client, xlsx_bytes([row(n, "登录", user="U", pwd="P") for n in names]), "a.xlsx")
    assert [x["pid"] for x in j["rows"]] == ["yuntu", "yuntu", "zhonghang", "xinhang", "huanyang", "yanwen", "shunfeng", "shunfeng", "yutong"]
    assert j["rows"][3]["action"] == "skip"


def test_extra_params_parse_variants(client, store):
    j = preview(client, xlsx_bytes([row("顺丰", "API", key="K", sec="S", aes="A" * 43, cust="C", extra="环境=沙箱; products=INT1，INT2 ；sender_post：100000")]), "a.xlsx")
    apply(client, j["batch"])
    a = store.get_account("shunfeng")
    assert a["env"] == "sandbox" and a["products"] == "INT1，INT2" and a["sender_post"] == "100000"
    j = preview(client, xlsx_bytes([row("顺丰", "API", key="K", sec="S", aes="A" * 43, cust="C", extra="env=sandbox; products=INT1,INT2; sender_post=100000")]), "a.xlsx")
    apply(client, j["batch"])
    a = store.get_account("shunfeng")
    assert a["env"] == "sandbox" and a["products"] == "INT1,INT2" and a["sender_post"] == "100000"


def test_huanyang_custom_url_goes_to_api_base_but_login_url_ignored(client, store):
    j = preview(client, xlsx_bytes([row("环洋", "API", key="K", sec="S", url="https://ekttech.net/pos-web")]), "a.xlsx")
    apply(client, j["batch"])
    assert store.get_account("huanyang")["api_base"] == "https://ekttech.net/pos-web"
    j = preview(client, xlsx_bytes([row("中航", "登录", user="U", pwd="P", url="http://login.example")]), "a.xlsx")
    apply(client, j["batch"])
    assert "api_base" not in store.get_account("zhonghang")


@pytest.mark.parametrize("v,exp", [("否", False), ("no", False), ("FALSE", False), ("0", False), ("停用", False), ("是", True), ("", True), ("y", True)])
def test_enabled_column_values(client, store, v, exp):
    j = preview(client, xlsx_bytes([row("中航", "登录", user="U", pwd="P", en=v)]), "a.xlsx")
    apply(client, j["batch"])
    assert store.get_account("zhonghang")["enabled"] is exp


def test_whitespace_and_unicode_values_trimmed_but_secrets_kept(client, store):
    j = preview(client, xlsx_bytes([row("雨桐", "API", key="  K中文  ", sec="  S p  ")]), "a.xlsx")
    apply(client, j["batch"])
    a = store.get_account("yutong")
    assert a["app_key"] == "K中文" and a["app_secret"] == "S p"      # 单元格首尾空白（粘贴时常见）统一去掉，中间空格保留


def test_empty_and_header_only_files(client):
    for data, fn in [(csv_bytes([]), "a.csv"), (xlsx_bytes([]), "a.xlsx"), (b"\n\n\n", "a.csv")]:
        r = client.post("/api/accounts/import/preview", json={"filename": fn, "data": b64(data)})
        assert r.status_code == 400 and "没有读到" in r.json()["detail"] or "空" in r.json()["detail"]


def test_row_limit(client):
    rows = [row("中航", "登录", user=f"U{i}", pwd="P") for i in range(1001)]
    r = client.post("/api/accounts/import/preview", json={"filename": "a.csv", "data": b64(csv_bytes(rows))})
    assert r.status_code == 400 and "上限" in r.json()["detail"]


def test_xlsx_hostile_inputs(client):
    # 超大列号：不能无限膨胀内存
    z = io.BytesIO()
    src = zipfile.ZipFile(io.BytesIO(xlsx_bytes([row("中航", "登录", user="U", pwd="P")])))
    with zipfile.ZipFile(z, "w") as out:
        for n in src.namelist():
            d = src.read(n)
            if n.startswith("xl/worksheets/sheet1"):
                d = d.replace(b'r="A1"', b'r="ZZZZZZZZ1"', 1)
            out.writestr(n, d)
    r = client.post("/api/accounts/import/preview", json={"filename": "a.xlsx", "data": b64(z.getvalue())})
    assert r.status_code in (200, 400)


def test_zip_bomb_rejected(client):
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as out:
        out.writestr("xl/workbook.xml", "<x/>")
        out.writestr("big.bin", b"\0" * (90 * 1024 * 1024))
    r = client.post("/api/accounts/import/preview", json={"filename": "a.xlsx", "data": b64(z.getvalue())})
    assert r.status_code == 400 and "过大" in r.json()["detail"]


# ---- 模板与导出 ----
def test_template_roundtrips_through_importer(client):
    t = client.get("/api/accounts/template").content
    sheets = read_xlsx(t)
    assert [s[0] for s in sheets] == ["账号配置", "字段说明", "示例（假数据）"] and sheets[0][1][0] == HDR
    ex = next(s for s in sheets if s[0].startswith("示例"))[1]
    j = preview(client, write_xlsx([{"name": "账号配置", "rows": ex, "widths": [8] * 12}]), "ex.xlsx")
    assert "skip" in actions(j)


def test_export_roundtrip_overwrites_with_same(client, store):
    store.set_account("yutong", {"app_key": "K1", "app_secret": "S1", "auth_mode": "api", "enabled": True})
    store.set_account("zhonghang", {"username": "U", "password": "P", "auth_mode": "login", "enabled": False})
    data = client.get("/api/accounts/export?secrets=none").content
    j = preview(client, data, "e.xlsx")
    assert {x["pid"] for x in j["rows"]} == {"yutong", "zhonghang"}
    assert all(x["action"] in ("same", "error") for x in j["rows"])


def test_export_filename_tags(client):
    assert "不含密钥" in client.get("/api/accounts/export").headers["content-disposition"] or "%E4%B8%8D%E5%90%AB" in client.get("/api/accounts/export").headers["content-disposition"]


def test_export_skips_public_site_and_empty_accounts(client, store):
    store.set_account("xinhang", {"enabled": True})
    t = client.get("/api/accounts/export?fmt=csv").content.decode("utf-8-sig")
    assert t.strip().count("\n") == 0, "无账号时只有表头"


def test_xlsx_writer_reader_roundtrip_special_chars():
    rows = [["a<b&c>", "引号\"'", "换行\nok", "  空格  ", "=1+1", "0123", "1.50"]]
    out = read_xlsx(write_xlsx([{"name": "S", "rows": rows, "widths": [8] * 7}]))[0][1][0]
    assert out[0] == "a<b&c>" and out[1] == "引号\"'" and out[2] == "换行\nok" and out[4] == "=1+1" and out[5] == "0123" and out[6] == "1.50"
