"""全场最低的可比性检查：测试数据 / 中港段 / 低价占位 / 非人民币 不能当最低价。"""
from app import rowcheck


def R(ch, total, cur="CNY", remark="", platform="x"):
    return {"platform": platform, "channel": ch, "total": total, "currency": cur, "remark": remark}


def test_hkz_zhonggang_excluded():
    r = R("HKZ-01中港", 1.5, remark="美国 1*1.5")
    assert "中港" in rowcheck.excluded_reason(r, 1)


def test_yanwen_test_env_excluded():
    r = R("大陆UPS经济商业快递", 3.2, remark="【测试环境数据，非真实报价】")
    assert "测试环境" in rowcheck.excluded_reason(r, 1)
    # 即使价格正常，只要被标成测试数据也不计入
    assert rowcheck.excluded_reason(R("燕文专线惠选-普货", 124.5, remark="【测试环境数据，非真实报价】"), 2)


def test_very_low_price_excluded_but_tiny_parcel_ok():
    assert rowcheck.excluded_reason(R("某渠道", 6.0), 1.0)
    assert rowcheck.excluded_reason(R("某渠道", 6.0), 0.1) == ""   # 100g 小包可能真的很便宜


def test_foreign_currency_not_compared():
    assert "USD" in rowcheck.excluded_reason(R("DHL", 5.0, cur="USD"), 1)


def test_real_hk_channels_kept():
    for ch in ("香港-DHL美国限邮编2", "香港联邦IPF小货价", "USZ-H01美国包中包60", "大陆FedEx-T IE"):
        assert rowcheck.excluded_reason(R(ch, 143.0), 1) == ""


def test_lowest_skips_excluded():
    rows = rowcheck.mark([R("HKZ-01中港", 1.5), R("a", 143.0), R("b", 150.0), R("t", 3.2, remark="【测试环境数据，非真实报价】")], 1)
    assert rowcheck.lowest(rows) == 143.0
    assert [bool(x.get("excluded")) for x in rows] == [True, False, False, True]
