"""把一份周食谱 JSON 校验后写入腾讯文档。

这是本项目的**唯一数据写入口**。设计上让调用方只负责产出 JSON：
无论是 WorkBuddy agent 读完食谱图自己生成的，还是你手工编辑的，都走这里。

## 用法

    # 从标准输入（agent 最常用）
    echo '{"days":[{"date":"2026-10-26","meals":{"午餐":["米饭","红烧肉"]}}]}' \
      | python menu/scripts/save_menu.py -

    # 从文件
    python menu/scripts/save_menu.py 本周.json

    # 交互粘贴
    python menu/scripts/save_menu.py

## 最小 JSON（其余自动补）

    {"days": [
      {"date": "2026-10-26", "meals": {"午餐": ["米饭","红烧肉"], "晚餐": ["面条"]}},
      {"date": "2026-10-27", "meals": {"早餐": ["小米粥"], "午餐": ["米饭","鸡腿"]}}
    ]}

weekday 按 date 自动推算；schema_version / week_start / week_end 自动补。
餐次只认 早餐 / 午餐 / 晚餐（表格只这三列）。

## 参数

    --dry-run    只校验，不写任何地方
    --no-doc     只校验并输出规范化后的 JSON，不写腾讯文档
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "common" / "py"))
from common import load_env, log  # noqa: E402

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
TABLE_MEALS = ("早餐", "午餐", "晚餐")
KNOWN_MEALS = ("早餐", "午餐", "晚餐", "加餐")


# ── 规范化 ────────────────────────────────────────────
def normalize(raw: dict) -> dict:
    """补齐缺失字段，让调用方只写最少的必要信息。"""
    days = raw.get("days") or []
    if not days:
        raise ValueError("JSON 里没有 days 数组")

    clean = []
    for index, day in enumerate(days, 1):
        date = str(day.get("date", "")).strip()
        try:
            dt.date.fromisoformat(date)
        except ValueError:
            raise ValueError(f"第 {index} 项日期格式不对：{date!r}（要 YYYY-MM-DD）") from None

        weekday = day.get("weekday") or WEEKDAYS[dt.date.fromisoformat(date).weekday()]
        meals = {}
        for name, dishes in (day.get("meals") or {}).items():
            items = [str(d).strip() for d in (dishes or []) if str(d).strip()]
            if items:
                meals[name] = items
        if not meals:
            continue
        clean.append({"date": date, "weekday": weekday, "meals": meals, "note": ""})

    if not clean:
        raise ValueError("所有日期都没有餐次内容")

    clean.sort(key=lambda d: d["date"])
    return {
        "schema_version": 1,
        "week_start": raw.get("week_start") or clean[0]["date"],
        "week_end": raw.get("week_end") or clean[-1]["date"],
        "days": clean,
    }


def validate(menu: dict) -> list[str]:
    """硬性校验。返回问题列表，空列表表示通过。"""
    problems: list[str] = []
    days = menu.get("days") or []
    if not days:
        return ["days 为空"]

    seen: set[str] = set()
    for day in days:
        date = day["date"]
        if date in seen:
            problems.append(f"日期重复：{date}")
        seen.add(date)

        meals = day.get("meals") or {}
        if not meals:
            problems.append(f"{date} 没有任何餐次")
        for name, dishes in meals.items():
            if name not in KNOWN_MEALS:
                problems.append(f"{date} 出现非标准餐次名「{name}」（只认 早餐/午餐/晚餐）")
            for dish in dishes:
                if "、" in dish:
                    problems.append(f"{date} 菜名里有顿号，读回来会拆错：{dish!r}")
    return problems


# ── IO ────────────────────────────────────────────────
def read_input(arg: str | None) -> dict:
    if arg and arg != "-":
        return json.loads(Path(arg).read_text(encoding="utf-8-sig"))
    if arg == "-":
        return json.loads(sys.stdin.read())
    print("粘贴 JSON，结束后按 Ctrl+Z 回车（Windows）或 Ctrl+D：", file=sys.stderr)
    return json.loads(sys.stdin.read())


def print_preview(menu: dict) -> None:
    print()
    print(f"周期 {menu['week_start']} ~ {menu['week_end']}｜{len(menu['days'])} 天")
    print("-" * 56)
    for day in menu["days"]:
        meals = "｜".join(f"{k} {len(v)} 道" for k, v in day["meals"].items())
        print(f"  {day['date']} {day['weekday']:<4} {meals}")
    print("-" * 56)


def main() -> int:
    load_env()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    no_doc = "--no-doc" in sys.argv
    target = args[0] if args else None

    try:
        raw = read_input(target)
    except json.JSONDecodeError as exc:
        log(f"JSON 格式错误：{exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        log(f"读取失败：{exc}")
        return 1

    try:
        menu = normalize(raw)
    except ValueError as exc:
        log(f"数据有问题：{exc}")
        return 1

    problems = validate(menu)
    if problems:
        log("校验不通过：")
        for item in problems:
            log(f"   · {item}")
        return 1

    print_preview(menu)

    if dry:
        log("[dry-run] 什么都没写")
        return 0

    if no_doc:
        print(json.dumps(menu, ensure_ascii=False, indent=2))
        return 0

    try:
        import doc_client

        doc_client.upsert_days(menu["days"])
    except Exception as exc:  # noqa: BLE001
        log(f"写腾讯文档失败：{exc}")
        return 1

    log(f"完成。服务器每天 09:50 会按这份数据发送。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
