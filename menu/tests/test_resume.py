"""回归测试：find_resume_dinner 在各种调休形态下的行为。

直接运行：python menu/tests/test_resume.py
"""
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "menu" / "scripts"))
sys.path.insert(0, str(ROOT / "common" / "py"))
import run_daily  # noqa: E402

D = dt.date


def day(date, lunch=None, dinner=None, breakfast=None):
    meals = {}
    if breakfast:
        meals["早餐"] = breakfast
    if lunch:
        meals["午餐"] = lunch
    if dinner:
        meals["晚餐"] = dinner
    return {"date": date.isoformat(), "weekday": "周X", "meals": meals}


def run(name, target, table, expect):
    run_daily.fetch_day = lambda d: table.get(d.isoformat())
    tomorrow = table.get((target + dt.timedelta(days=1)).isoformat())
    got = run_daily.find_resume_dinner(target, tomorrow)
    got_date = (got or {}).get("date")
    flag = "OK  " if got_date == expect else "FAIL"
    print(f"[{flag}] {name:<26} 期望 {expect}  实际 {got_date}")


THU = D(2026, 10, 22)
FRI = D(2026, 10, 23)
SAT = D(2026, 10, 24)
SUN = D(2026, 10, 25)
MON = D(2026, 10, 26)

# a) 正常周四：周五还有午餐 -> 不触发
run("正常周四", THU, {
    "2026-10-23": day(FRI, lunch=["酸汤水饺"], breakfast=["红薯"]),
    "2026-10-25": day(SUN, dinner=["肉片炒面筋"]),
}, None)

# b) 正常周五：周六整天没数据 -> 找到周日晚餐
run("正常周五", FRI, {
    "2026-10-25": day(SUN, dinner=["肉片炒面筋"]),
}, "2026-10-25")

# c) 调休周六上课：周六有午餐晚餐 -> 不触发（周六自己发）
run("调休周六上课", FRI, {
    "2026-10-24": day(SAT, lunch=["米饭"], dinner=["面条"], breakfast=["粥"]),
    "2026-10-25": day(SUN, dinner=["肉片炒面筋"]),
}, None)

# d) 调休周日不返校：往后都没有晚餐 -> 不触发
run("周日不返校", FRI, {
    "2026-10-26": day(MON, lunch=["米饭"], breakfast=["粥"]),
}, None)

# e) 长假：隔了 6 天才上课 -> 仍能找到（假期后返校第一顿）
run("长假后返校", FRI, {
    "2026-10-29": day(D(2026, 10, 29), dinner=["开学第一餐"]),
}, "2026-10-29")

# f) 周六上午在校（只有早餐）、中午离校 -> 触发，找周日晚餐
run("周六半天", FRI, {
    "2026-10-24": day(SAT, breakfast=["粥"]),
    "2026-10-25": day(SUN, dinner=["肉片炒面筋"]),
}, "2026-10-25")
