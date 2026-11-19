"""报价行的"可比性"检查：决定一行能不能参与「全场最低」。

不删除任何行（用户仍能看到全部原始结果），只在行上打 excluded=原因，前端不把它算进「全场最低」/ 平台最低价，历史记录的最低价也跳过它。
排除的三类：
1. 测试环境的假数据（燕文沙箱等，适配器已在备注里写了【测试环境数据，非真实报价】）；
2. 不属于目的国运价的境内/中港段渠道（例如雨桐 HKZ-01中港 ¥1.50：深圳→香港的陆运，不是寄到美国的价格）；
3. 价格低到不合理（< ¥10，且重量 ≥ 0.2kg）：多半是占位价或境内段，保险起见不当作最低价；
4. 非人民币：不和人民币直接比较。
"""
import re

TEST_MARK = "测试环境数据"
# 中港/境内段/头程/仓储类渠道：名字里带这些词的不是「寄到目的国」的运价
_DOMESTIC = re.compile(r"中港|港段|境内段|国内段|内地段|头程|入仓|仓储|(?<![A-Za-z])HKZ(?![A-Za-z])", re.I)
LOW_PRICE = 10.0


def excluded_reason(row, weight=0.0):
    ch = f"{row.get('channel') or ''} {row.get('group') or ''}"
    if TEST_MARK in (row.get("remark") or ""):
        return "测试环境数据，非真实报价"
    if _DOMESTIC.search(ch):
        return "中港/境内段渠道，不是寄到目的国的运价"
    cur = (row.get("currency") or "CNY").upper()
    if cur != "CNY":
        return f"币种 {cur}，未折算为人民币，不与人民币比较"
    t = row.get("total")
    if isinstance(t, (int, float)) and t < LOW_PRICE and (weight or 0) >= 0.2:
        return f"价格低于 ¥{LOW_PRICE:g}，疑似占位价/境内段，不计入最低"
    return ""


def mark(rows, weight=0.0):
    for r in rows:
        why = excluded_reason(r, weight)
        if why:
            r["excluded"] = why
        else:
            r.pop("excluded", None)
    return rows


def lowest(rows):
    xs = [r["total"] for r in rows if not r.get("excluded") and isinstance(r.get("total"), (int, float))]
    return min(xs) if xs else None
