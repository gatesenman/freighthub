"""报价归一化：to_float / parse_days / make_row / Query.dims / 币种 / 重量尺寸边界 / 排序。"""
import math

import pytest

from app.adapters.base import Query, make_row, parse_days, to_float

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("v,exp", [
    ("12.5", 12.5), (" 7 ", 7.0), ("1,234.50", 1234.5), (0, 0.0), ("0", 0.0), (-3, -3.0), (5, 5.0), ("1e3", 1000.0),
    (None, None), ("", None), ("abc", None), ("12元", None), ([], None), ({}, None), ("--", None)])
def test_to_float_basic(v, exp):
    assert to_float(v) == exp


@pytest.mark.parametrize("v", ["nan", "NaN", "inf", "-inf", "Infinity", float("nan"), float("inf")])
def test_to_float_rejects_non_finite(v):
    r = to_float(v)
    assert r is None or math.isfinite(r), "NaN/Inf 会让 /api/quote 的 JSON 序列化失败"


@pytest.mark.parametrize("text,exp", [
    ("5-8", (5, 8)), ("7", (7, 7)), ("8-5", (5, 8)), ("5～8个工作日", (5, 8)), ("T+3", (3, 3)), ("12-20 天", (12, 20)),
    ("", (None, None)), (None, (None, None)), ("待定", (None, None)), ("3 到 5", (3, 5)), (7, (7, 7))])
def test_parse_days(text, exp):
    assert parse_days(text) == exp


def test_parse_days_decimals_not_split_into_wrong_integers():
    lo, hi = parse_days("0.5-1")
    assert (lo, hi) == (1, 1), f"小数时效应向上取整为 1-1，实际 {lo}-{hi}"


def test_make_row_defaults_and_fields():
    r = make_row("p", "平台", "渠道", 12.3)
    assert r["currency"] == "CNY" and r["group"] == "" and r["fees"] == [] and r["remark"] == "" and r["track"] is None
    assert (r["days_min"], r["days_max"]) == (None, None) and r["total"] == 12.3


def test_make_row_days_and_extras():
    r = make_row("p", "平台", "渠道", 1, days="5-8", currency="USD", group="g", track=True, fees=[{"label": "a", "value": "1"}], remark="x")
    assert (r["days_min"], r["days_max"], r["currency"], r["track"]) == (5, 8, "USD", True)


@pytest.mark.parametrize("l,w,h,exp", [(10, 10, 10, True), (0, 10, 10, False), (10, 0, 10, False), (10, 10, 0, False),
                                       (None, 1, 1, False), (0.1, 0.1, 0.1, True), (-1, 5, 5, False), (0, 0, 0, False)])
def test_query_dims_requires_all_three_positive(l, w, h, exp):
    assert Query(country="US", length=l, width=w, height=h).dims() is exp


def test_query_defaults():
    q = Query(country="US")
    assert q.weight == 1.0 and q.postcode == "" and not q.dims()


@pytest.mark.parametrize("w,exp", [(0.1 + 0.2, "0.3"), (1, "1"), (2.50, "2.5"), (0.001, "0.001"), (99999, "99999"), (0.015, "0.015")])
def test_weight_formatting_used_by_adapters_is_clean(w, exp):
    assert f"{w:g}" == exp


def test_rows_sorted_by_price_in_endpoint(client, net, setacct):
    from tests.suite.test_adapters_ois import ois_setup
    ois_setup(net, setacct, items=[
        {"code": "A", "name": "贵", "standardFeeSum": 300}, {"code": "B", "name": "便宜", "standardFeeSum": 100.5},
        {"code": "C", "name": "中", "standardFeeSum": 200}])
    r = client.post("/api/quote", json={"country": "US", "weight": 1, "platforms": ["yutong"]}).json()
    assert [x["total"] for x in r["rows"]] == [100.5, 200, 300]


def test_adapter_rows_with_zero_or_negative_price_are_dropped(client, net, setacct):
    from tests.suite.test_adapters_ois import ois_setup
    ois_setup(net, setacct, pid="zhonghang", items=[{"code": "A", "name": "免费?", "standardFeeSum": 0}, {"code": "B", "name": "负", "standardFeeSum": -5},
                                                    {"code": "C", "name": "正常", "standardFeeSum": 50}])
    r = client.post("/api/quote", json={"country": "US", "weight": 1, "platforms": ["zhonghang"]}).json()
    assert [x["channel"] for x in r["rows"]] == ["正常"]


def test_non_cny_rows_are_flagged_not_silently_mixed(client, net, setacct):
    from tests.suite.test_adapters_ois import ois_setup
    ois_setup(net, setacct, items=[{"code": "A", "name": "美元渠道", "standardFeeSum": 10, "standardCur": "USD"}])
    r = client.post("/api/quote", json={"country": "US", "weight": 1, "platforms": ["yutong"]}).json()
    row = r["rows"][0]
    assert row["currency"] == "USD" and ("USD" in row["remark"] or any("USD" in f["value"] or "币种" in f["label"] for f in row["fees"]))
