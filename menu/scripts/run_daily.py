"""每日主流程：取当日午餐/晚餐 + 次日早餐（+ 返校晚餐）→ 拼消息 → 发 QQ 群。

规则：
    · 当天没有午餐也没有晚餐  → 什么都不发（周末、假期自动静默）
    · 有午餐/晚餐            → 发；次日有早餐就附在后面，没有就单独发当天
    · 次日没有晚餐且后面几天有 → 附「返校晚餐」（寄宿制：周五预告周日晚餐）
    · 周日只检查午餐（周日晚餐已在周五预告过，不再重复发）
    · 数据源固定为腾讯文档「每日食谱」智能表格

用法：
    python menu/scripts/run_daily.py                  # 正常发送（服务器定时任务调这个）
    python menu/scripts/run_daily.py --bot menu       # 指定机器人（config.yaml 里 handler: menu 的键名）
    python menu/scripts/run_daily.py --preview        # 只看不发
    python menu/scripts/run_daily.py 2026-10-23 --test   # 真发但不记状态（试发用这个，不影响定时任务）
    python menu/scripts/run_daily.py 2026-10-23 --force  # 已发过也强制重发

多个食谱机器人（比如老师群和学生群各一个）各自维护独立的发送状态：
menu/data/<键名>/state.json。
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "common" / "py"))
import bots  # noqa: E402
from common import MENU_DATA_DIR, load_config, load_env, log, read_json, today, write_json  # noqa: E402
from send_qq import build_message, send_message  # noqa: E402

# 返校晚餐最多往后找几天（覆盖国庆这类长假后返校）
RESUME_LOOKAHEAD = 8
_doc_failed = False                   # 一天只提示一次腾讯文档失败，避免刷屏


def state_file(bot: str) -> Path:
    """每个机器人一份状态，互不干扰。旧版单机器人状态文件继续沿用。"""
    new = MENU_DATA_DIR / bot / "state.json"
    legacy = MENU_DATA_DIR / "state.json"
    if bot == bots.DEFAULT_BOT and not new.exists() and legacy.exists():
        return legacy
    return new


def parse_bot(argv: list[str]) -> tuple[str, list[str]]:
    """从参数里取 --bot，并把这两个词从剩余参数里去掉。"""
    if "--bot" not in argv:
        return bots.DEFAULT_BOT, argv
    index = argv.index("--bot")
    if index + 1 >= len(argv):
        raise SystemExit("--bot 后面要跟 config.yaml 里的机器人键名")
    return argv[index + 1], argv[:index] + argv[index + 2:]


def fetch_day(target: dt.date) -> dict | None:
    """取某天的食谱；取不到返回 None（交由调用方决定发不发）。

    腾讯文档是唯一数据源。读失败时记一条日志，不重试、不猜，当作"没数据"处理。
    """
    import doc_client

    try:
        day = doc_client.get_day(target)
        if day:
            return day
        log(f"腾讯文档里没有 {target} 的数据")
    except Exception as exc:  # noqa: BLE001
        log(f"读腾讯文档失败：{exc}")

    return None


def send_meals_for(target: dt.date) -> tuple[str, ...]:
    """当天要发哪几餐。

    周日只发午餐：寄宿制周日下午返校才吃晚饭，而周日晚餐已经在周五那条里预告过了，
    周日 09:50 再发一遍属于重复。若周日有午餐（调休上课），照常发。
    """
    if target.weekday() == 6:
        return ("午餐",)
    return ("午餐", "晚餐")


def find_resume_dinner(target: dt.date, tomorrow: dict | None) -> dict | None:
    """次日整天都不在校（没午餐也没晚餐）时，往后找第一个有晚餐的日期。

    数据驱动，不认星期几，所以调休能自动适配：
      · 正常周四：周五还有午餐 → 不触发（周五自己会发）
      · 正常周五：周六整天没数据 → 找到周日 → 附周日晚餐
      · 调休周六上课：周六有午餐 → 不触发，周五照常附周六早餐
      · 调休周日不返校：往后都没有晚餐 → 不附
    """
    if tomorrow and (tomorrow.get("meals", {}).get("午餐") or tomorrow.get("meals", {}).get("晚餐")):
        return None

    for offset in range(2, RESUME_LOOKAHEAD + 1):
        candidate = fetch_day(target + dt.timedelta(days=offset))
        if candidate and (candidate.get("meals", {}).get("晚餐") or []):
            log(f"返校晚餐：{candidate['date']}（间隔 {offset} 天）")
            return candidate
    return None


def main() -> int:
    load_env()
    cfg = load_config()
    bot, argv = parse_bot(sys.argv[1:])
    if bots.bot_handler(bot) != "menu":
        raise SystemExit(f"机器人「{bot}」的 handler 不是 menu，不能用 run_daily.py")

    preview = "--preview" in argv
    args = [a for a in argv if not a.startswith("--")]
    target = dt.date.fromisoformat(args[0]) if args else today()
    force = "--force" in argv
    test_mode = "--test" in argv
    log(f"机器人：{bots.get_bot(bot, strict=False).label}（{bot}）")
    log(f"目标日期：{target}（{'一二三四五六日'[target.weekday()]}）")

    # 幂等：同一天发过就跳过，避免手动补发后定时任务又发一遍。
    # --preview 不受影响（不写状态）；--force 强制重发；--test 真发但不记状态，
    # 用完定时任务照常发（手动试发就用这个，别用无参数）。
    state_path = state_file(bot)
    state = read_json(state_path, {})
    sent_record = (state.get("sent") or {}).get(target.isoformat())
    if sent_record and not preview and not force:
        log(f"{target} 已发过（{sent_record.get('at')}）→ 跳过；要重发加 --force")
        return 0

    day = fetch_day(target)
    if day is None:
        log(f"{target} 没有任何食谱数据 → 不发送")
        return 0

    # 当天必须有午餐或晚餐才发；只有早餐的日子（比如周六）静默跳过
    wanted = send_meals_for(target)
    has_main = any(day.get("meals", {}).get(m) for m in wanted)
    if not has_main:
        if target.weekday() == 6:
            log(f"{target} 是周日，只发午餐；没有午餐 → 不发送（周日晚餐已在周五那条预告过）")
        else:
            log(f"{target} 没有任何餐次（可能放假）→ 不发送")
        return 0

    tomorrow = fetch_day(target + dt.timedelta(days=1))
    # 次日早餐单独拎出来；次日是否在校另算，别混用同一个变量
    next_breakfast = tomorrow if (tomorrow and tomorrow.get("meals", {}).get("早餐")) else None
    resume = find_resume_dinner(target, tomorrow)

    content = build_message(day, next_breakfast, resume)
    if not content:
        log("消息内容为空 → 不发送")
        return 0

    if preview:
        log("预览内容：\n" + "-" * 40 + f"\n{content}\n" + "-" * 40)
        return 0

    send_message(content, dry_run=cfg["send"].get("dry_run"), bot=bot)

    if test_mode:
        log("[test] 已发送但不记状态，定时任务到时仍会正常发")
        return 0

    state = read_json(state_path, {})
    state.setdefault("sent", {})[target.isoformat()] = {
        "at": dt.datetime.now().isoformat(timespec="seconds"),
        "with_next_breakfast": bool(next_breakfast),
        "with_resume_dinner": (resume or {}).get("date"),
    }
    write_json(state_path, state)
    log("完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
