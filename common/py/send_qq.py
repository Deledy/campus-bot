"""发送消息到 QQ 群（官方机器人 OpenAPI）。

要点：
- 每个机器人有自己的 AppID/Secret，也有自己的 group_openid（即使同群也不共用）。
- group_openid 不是群号，是从群事件里拿到的，格式像 A1B2C3D4...
- 主动消息有频控：群聊 20 条/分钟/群、1000 条/群/天，每天一条完全够用。
- 群成员可以在客户端关掉「接收机器人主动消息」，关掉后会静默失败，不报错。
- 消息里如果含链接，需要先在开放平台后台把域名加进「消息 URL 配置」，否则整条发不出去。

机器人清单与凭据见 bots.py / config.yaml。
"""
from __future__ import annotations

import base64
import datetime as dt
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bots  # noqa: E402
from common import load_config, load_env, log  # noqa: E402

API = "https://api.sgroup.qq.com"


WEEKDAY_CN = "一二三四五六日"

# 每天发什么：当日午餐、当日晚餐、次日早餐。其余餐次不发。
TODAY_MEALS = ["午餐", "晚餐"]
NEXT_MEALS = ["早餐"]

MEAL_ICON = {"早餐": "☀️", "午餐": "🍱", "晚餐": "🌙"}

# 天天都有、写出来没有信息量的项：折叠成消息末尾一行「每日标配」
DEFAULT_ITEMS = ("馒头", "花卷", "蒸鸡蛋")
# 主食：不作为「主角」，但在米饭日要写进标题让老师一眼看到
STAPLE_ITEMS = ("米饭", "白米饭")

# 菜名 -> emoji。顺序即优先级，具体词必须排在笼统词前面
# （例如「鸡蛋面汤」要先被 粥/汤 命中，而不是被 蛋 命中）
EMOJI_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("西红柿", "番茄"), "🍅"),
    (("虾",), "🍤"),
    (("蟹",), "🦀"),
    (("鱼香",), "🍖"),
    (("鱼",), "🐟"),
    (("鸡腿", "鸡翅", "炸鸡", "烤鸡"), "🍗"),
    (("饺", "馄饨", "包子"), "🥟"),
    (("汤圆", "粽子", "月饼", "蛋糕", "甜点", "布丁", "桃酥"), "🍰"),
    (("肉卷", "馍片", "发糕", "花卷", "馒头", "卷", "饼", "馍", "糕"), "🍞"),
    (("炒饭", "米饭", "焖饭", "盖饭"), "🍚"),
    (("果汁", "橙汁", "饮料", "汽水"), "🧃"),
    (("牛奶", "豆浆", "酸奶"), "🥛"),
    (("粥", "汤", "羹"), "🥣"),
    (("蛋",), "🥚"),
    (("面筋",), "🍖"),
    (("拌面", "炒面", "汤面", "刀削", "拉面", "面条", "烩面", "卤面", "米线", "米粉"), "🍜"),
    (("面",), "🍜"),
    (("肉", "排", "鸡", "鸭", "牛", "羊", "肠", "丸", "骨"), "🍖"),
    (("玉米",), "🌽"),
    (("红薯", "紫薯", "山药", "芋", "南瓜"), "🍠"),
    (("花生", "核桃", "瓜子", "坚果"), "🥜"),
    (("苹果", "香蕉", "橙", "橘", "梨", "葡萄", "西瓜", "哈密瓜", "草莓", "桃", "圣女果"), "🍎"),
    (("凉拌", "沙拉"), "🥗"),
    (("菇",), "🍄"),
    (("萝卜",), "🥕"),
    (("茄子",), "🍆"),
    (("辣椒", "青椒", "尖椒"), "🌶️"),
    (("白菜", "青菜", "生菜", "包菜", "花菜", "冬瓜", "黄瓜", "丝瓜", "苦瓜", "土豆",
      "豆芽", "粉丝", "粉条", "豆腐", "豆角", "芹菜", "蒜", "木耳", "素", "菜"), "🥬"),
)

# 「第一位是不是主角」的渲染兜底：提取端没设主角时（全是素菜的一餐），
# 不要把素菜硬顶成主打。命中这些字就算像主菜，纯字符串匹配，不承担语义判断。
HERO_HINTS = ("肉", "排", "鸡", "鸭", "牛", "羊", "鱼", "虾", "蟹", "骨",
              "丸", "肠", "饺", "面", "饭", "蛋")


def dish_emoji(name: str) -> str:
    for words, icon in EMOJI_RULES:
        if any(w in name for w in words):
            return icon
    return "🍽"


def build_message(day: dict, next_day: dict | None = None, resume: dict | None = None) -> str:
    """拼当天要发的消息。

    内容固定为：当日午餐 + 当日晚餐 + 次日早餐。
    当日晚餐或明日早餐没有数据时，那一段直接省略（学校放假、周五没有次日早餐等情况）。

    day 当天没有午餐也没有晚餐 -> 返回空字符串，调用方据此「什么都不发」。

    resume：寄宿制学校的「返校晚餐」。周五下午离校、周日下午返校，中间断档，
    周五那条消息里要把周日晚餐一并预告，否则这顿饭的信息就没人知道了。
    由 run_daily 用「往后找第一个有晚餐的日期」算出来传入，这里只负责渲染。

    展示规则（全部是确定性字符串操作，语义判断已在提取端完成）：
    - 每餐第一位是「主角」，提取时按 大荤行 -> 面食主食 -> 首个非标配菜 排好
    - 馒头/花卷/蒸鸡蛋 折叠进末尾「每日标配」
    - 米饭日标题写成「米饭 + 主角」，让老师一眼看到主食配什么菜
    """
    today_blocks, fixed = _meal_blocks(day, TODAY_MEALS)
    if not today_blocks:
        return ""

    date = dt.date.fromisoformat(day["date"])
    weekday = day.get("weekday") or "周" + WEEKDAY_CN[date.weekday()]
    parts = [f"📣 今日食谱 · {date.month}月{date.day}日 {weekday}", ""]
    parts.extend(today_blocks)

    # 当天没晚餐（离校日），明确说一句，否则老师会以为漏发
    if not (day.get("meals", {}).get("晚餐") or []):
        parts.append("")
        parts.append("🚌 今日没有晚餐（下午离校）")

    next_blocks, next_fixed = _meal_blocks(next_day, NEXT_MEALS, with_title=False)
    if next_blocks:
        nd = dt.date.fromisoformat(next_day["date"])
        nwd = next_day.get("weekday") or "周" + WEEKDAY_CN[nd.weekday()]
        parts.append("")
        parts.append(f"—— 明日早餐 · {nd.month}月{nd.day}日 {nwd} ——")
        parts.extend(next_blocks)
        fixed += next_fixed

    # 返校晚餐：中间断档后第一顿在学校吃的饭，提前预告
    resume_blocks, resume_fixed = _meal_blocks(resume, ["晚餐"], with_title=False)
    if resume_blocks:
        rd = dt.date.fromisoformat(resume["date"])
        parts.append("")
        parts.append(f"—— 周{WEEKDAY_CN[rd.weekday()]}晚餐 · {rd.month}月{rd.day}日 ——")
        parts.extend(resume_blocks)
        fixed += resume_fixed

    if fixed:
        unique = list(dict.fromkeys(fixed))
        parts.append("")
        parts.append(f"💡 每日标配：{' · '.join(unique)}")

    text = "\n".join(parts).strip()
    # 群聊长文本容易被截断，留出安全边界
    if len(text) > 1500:
        text = text[:1497] + "..."
    return text


WEEK_MEALS = ["早餐", "午餐", "晚餐"]


def build_week_message(days: dict[str, dict], start: dt.date, end: dt.date) -> str:
    """拼「本周完整食谱」：从周一到周日逐天列出所有餐次。

    days 以 ISO 日期为键，只包含本周范围内的数据；整周都没数据时返回空字符串。
    每道菜都带 dish_emoji，方便一眼区分主食、荤菜、蔬菜、汤和水果。
    """
    if not days:
        return ""

    parts = [f"📣 本周食谱 · {start.month}月{start.day}日～{end.month}月{end.day}日"]
    for offset in range((end - start).days + 1):
        date = start + dt.timedelta(days=offset)
        meals = (days.get(date.isoformat()) or {}).get("meals") or {}

        parts.append("")
        parts.append(f"▪️ 周{WEEKDAY_CN[date.weekday()]} {date.month}月{date.day}日")

        lines: list[str] = []
        for name in WEEK_MEALS:
            dishes = [d for d in (meals.get(name) or []) if d]
            if not dishes:
                continue
            marked = " · ".join(f"{dish_emoji(d)} {d}" for d in dishes)
            lines.append(f"{MEAL_ICON.get(name, '🍽')} {name}：{marked}")
        parts.extend(lines or ["（无餐次安排）"])

    text = "\n".join(parts).strip()
    # 与单日消息保持同样的安全边界
    if len(text) > 1500:
        text = text[:1497] + "..."
    return text


def _meal_blocks(
    day: dict | None, meals: list[str], with_title: bool = True
) -> tuple[list[str], list[str]]:
    """渲染指定餐次。返回 (消息行, 被折叠的标配项)。"""
    if not day:
        return [], []

    blocks: list[str] = []
    folded: list[str] = []
    for name in meals:
        dishes = [d for d in (day.get("meals", {}).get(name) or []) if d]
        if not dishes:
            continue

        folded += [d for d in dishes if d in DEFAULT_ITEMS]
        items = [d for d in dishes if d not in DEFAULT_ITEMS]
        if not items:
            items = ["常规早餐"]

        icon = MEAL_ICON.get(name, "🍽")
        hero = next((d for d in items if d not in STAPLE_ITEMS), "")
        rice = next((d for d in items if d in STAPLE_ITEMS), "")
        if hero and not any(h in hero for h in HERO_HINTS):
            hero = ""  # 第一位是素菜 / 凉菜，不当主打

        if blocks:
            blocks.append("")
        if with_title:
            if hero and rice:
                blocks.append(f"{icon} {name}｜{rice} + {hero}")
            elif hero:
                blocks.append(f"{icon} {name}｜主打：{hero}")
            elif rice:
                blocks.append(f"{icon} {name}｜{rice}")
            else:
                blocks.append(f"{icon} {name}")
            detail = [d for d in items if d != hero and d != rice]
        else:
            # 次日早餐没有「主角」概念，全部平铺
            detail = items

        blocks.extend(f"　{dish_emoji(d)} {d}" for d in detail)

    return blocks, folded


def send_group_text(
    group_openid: str,
    content: str,
    bot: str = bots.DEFAULT_BOT,
    timeout: int = 15,
    msg_id: str | None = None,
    msg_seq: int = 1,
) -> dict:
    body = {"msg_type": 0, "content": content}
    # 群聊长文本容易被截断或发送失败，按 1500 字安全切分
    if len(content) > 1500:
        content = content[:1497] + "..."
        body["content"] = content
    # 带 msg_id 走被动回复，不占用主动消息额度
    if msg_id:
        body.update({"msg_id": msg_id, "msg_seq": msg_seq})

    for attempt in (1, 2):
        resp = requests.post(
            f"{API}/v2/groups/{group_openid}/messages",
            headers={"Authorization": f"QQBot {bots.get_token(bot, force=attempt == 2)}",
                     "Content-Type": "application/json"},
            json=body,
            timeout=timeout,
        )
        if resp.status_code == 200:
            data = resp.json()
            log(f"发送成功 message_id={data.get('id', '?')}")
            return data

        # 401/403 多半是 token 过期，重试一次
        if resp.status_code in (401, 403) and attempt == 1:
            log(f"鉴权失败（{resp.status_code}），刷新 token 重试")
            continue

        if resp.status_code == 429 or "50015014" in resp.text:
            raise RuntimeError("触发频控（50015014），今天发得太频繁了")
        raise RuntimeError(f"发送失败 {resp.status_code}: {resp.text[:300]}")

    raise RuntimeError("发送失败：token 刷新后仍然鉴权不通过，检查 AppID/AppSecret 与 IP 白名单")


def upload_group_file(
    group_openid: str,
    file_path: str | Path,
    bot: str = bots.DEFAULT_BOT,
    timeout: int = 60,
) -> dict:
    """上传文件到群，返回 {file_uuid, file_info, ttl}（file_type=4 表示文件）。

    QQ 的富媒体接口要求先上传拿 file_info，再用 msg_type=7 发送。
    srv_send_msg=False 时不占用主动消息频次，由调用方决定怎么发。
    """
    path = Path(file_path)
    body = {
        "file_type": 4,
        "file_data": base64.b64encode(path.read_bytes()).decode("ascii"),
        "file_name": path.name,  # 不带文件名会显示「未命名」
        "srv_send_msg": False,
    }
    for attempt in (1, 2):
        resp = requests.post(
            f"{API}/v2/groups/{group_openid}/files",
            headers={"Authorization": f"QQBot {bots.get_token(bot, force=attempt == 2)}",
                     "Content-Type": "application/json"},
            json=body,
            timeout=timeout,
        )
        if resp.status_code == 200:
            data = resp.json()
            if not data.get("file_info"):
                raise RuntimeError(f"上传文件失败：返回里没有 file_info（{data}）")
            log(f"文件已上传 {path.name}（{path.stat().st_size // 1024} KB）")
            return data
        if resp.status_code in (401, 403) and attempt == 1:
            log(f"上传鉴权失败（{resp.status_code}），刷新 token 重试")
            continue
        raise RuntimeError(f"上传文件失败 {resp.status_code}: {resp.text[:300]}")
    raise RuntimeError("上传文件失败：token 刷新后仍然鉴权不通过")


def send_group_file(
    group_openid: str,
    file_path: str | Path,
    bot: str = bots.DEFAULT_BOT,
    msg_id: str | None = None,
    msg_seq: int = 1,
    content: str = "",
    timeout: int = 30,
) -> dict:
    """把文件作为富媒体消息发到群。

    msg_id 非空时走「被动回复」（在 5 分钟内回给 @ 机器人的人），
    不占用每月 4 条的主动消息额度。
    """
    upload = upload_group_file(group_openid, file_path, bot=bot)
    body = {
        "msg_type": 7,
        "content": content,
        "media": {"file_info": upload["file_info"]},
    }
    if msg_id:
        body.update({"msg_id": msg_id, "msg_seq": msg_seq})

    for attempt in (1, 2):
        resp = requests.post(
            f"{API}/v2/groups/{group_openid}/messages",
            headers={"Authorization": f"QQBot {bots.get_token(bot, force=attempt == 2)}",
                     "Content-Type": "application/json"},
            json=body,
            timeout=timeout,
        )
        if resp.status_code == 200:
            data = resp.json()
            log(f"文件发送成功 message_id={data.get('id', '?')}")
            return data
        if resp.status_code in (401, 403) and attempt == 1:
            log(f"文件发送鉴权失败（{resp.status_code}），刷新 token 重试")
            continue
        raise RuntimeError(f"文件发送失败 {resp.status_code}: {resp.text[:300]}")
    raise RuntimeError("文件发送失败：token 刷新后仍然鉴权不通过")


def send_message(
    content: str, dry_run: bool = False, bot: str = bots.DEFAULT_BOT
) -> dict | None:
    if dry_run:
        log("[dry-run] 不实际发送，消息内容如下：\n" + "-" * 40 + f"\n{content}\n" + "-" * 40)
        return None
    return send_group_text(bots.get_bot(bot).group_openid, content, bot=bot)


if __name__ == "__main__":
    load_env()
    cfg = load_config()
    text = sys.argv[1] if len(sys.argv) > 1 else "测试消息：机器人已连通"
    send_message(text, dry_run=bool(cfg["send"].get("dry_run")))
