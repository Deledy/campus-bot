"""一键自检：告诉你现在缺什么、下一步该做什么。

用法：
    python common/scripts/check_setup.py

输出四块检查结果：
    1. 配置完整性（.env 各项是否填了）
    2. QQ 鉴权是否真的能用（会真调一次 token 接口，不发送消息）
    3. 大模型 / OCR 是否可用
    4. 食谱数据是否覆盖今天
    5. 服务器 SSH 是否连通、服务器上是否装好了
任何一项❌都会给出具体修复动作。
"""
from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
from common import load_config, load_env, today  # noqa: E402

OK = "✅"
NO = "❌"
WARN = "⚠️ "

problems: list[str] = []
next_steps: list[str] = []


def section(title: str) -> None:
    print()
    print(f"── {title} " + "─" * max(0, 54 - len(title)))


def item(flag: str, label: str, detail: str = "") -> None:
    print(f"  {flag} {label}" + (f"  {detail}" if detail else ""))


# ── 1. 配置完整性 ─────────────────────────────────────
def check_env() -> None:
    section("1. 配置完整性（.env）")
    import bots

    names_in_config = bots.list_bots()
    if not names_in_config:
        item(NO, "config.yaml 里没有机器人", "在 bots 段加一个")
        problems.append("没有任何机器人配置")
        return

    for name in names_in_config:
        bot = bots.get_bot(name, strict=False)
        names = bots.bot_env_names(name)
        schedule = bots.bot_schedule(name)
        try:
            handler = bots.bot_handler(name)
        except Exception as exc:  # noqa: BLE001
            item(NO, f"{bot.label} 类型配置错误", str(exc))
            problems.append(f"{bot.label} 的 handler 配置错误")
            continue
        item(OK, f"{bot.label} 类型",
             f"{handler}" + (f"，每天 {schedule}" if schedule else "，手动"))

        for field, label in (("appid", "AppID"), ("secret", "AppSecret")):
            val = getattr(bot, field)
            if val:
                shown = val if field == "appid" else f"{val[:6]}…({len(val)}位)"
                item(OK, f"{bot.label} {label}", shown)
            else:
                item(NO, f"{bot.label} {label}", f"未填 {names[field]}")
                problems.append(f".env 缺少 {names[field]}")
                next_steps.append(f"去 QQ 开放平台复制「{bot.label}」的 {label}，填进 .env")

        if bot.group_openid:
            item(OK, f"{bot.label} 群 openid", f"{bot.group_openid[:8]}…")
        else:
            item(NO, f"{bot.label} 群 openid", f"未填 {names['group_openid']}")
            problems.append(f".env 缺少 {names['group_openid']}")
            next_steps.append(
                f"跑 python common/scripts/capture_group_openid.py --bot {name}，"
                f"然后去群里 @ {bot.label} 说句话"
            )

    optional = {
        "LLM_API_KEY": "大模型 API Key（解析图片用，可选）",
        "LLM_VISION_MODEL": "多模态模型（解析图片用，可选）",
        "LLM_BASE_URL": "大模型接口地址",
    }
    doc_vars = {
        "TENCENT_DOC_FILE_ID": "腾讯文档 fileID（真实 id，不是 URL 里那串）",
        "TENCENT_MCP_TOKEN": "腾讯文档 MCP Token（服务器必填）",
    }

    for key, label in optional.items():
        val = os.environ.get(key, "").strip()
        item(OK if val else WARN, label, val or "没填（模型支持读图时不需要）")

    if True:  # 腾讯文档是唯一数据源，始终检查
        for key, label in doc_vars.items():
            val = os.environ.get(key, "").strip()
            if val:
                item(OK, label, val if len(val) < 14 else f"{val[:8]}…")
                continue
            if key == "TENCENT_MCP_TOKEN":
                # 本机有宿主票据兜底，所以不是阻塞项
                try:
                    import doc_client as _dc

                    _tok, _src = _dc._token()
                    item(OK, label, f"未填，但本机走「{_src}」——服务器上必须填")
                except Exception:  # noqa: BLE001
                    item(NO, label, "未填")
                    problems.append("腾讯文档无可用 token")
                    next_steps.append(
                        "去 https://docs.qq.com/open/auth/mcp.html 生成个人 Token，"
                        "填进 .env 的 TENCENT_MCP_TOKEN"
                    )
                continue
            item(NO, label, "未填")
            problems.append(f".env 缺少 {key}")
            if key == "TENCENT_DOC_FILE_ID":
                next_steps.append(
                    "把腾讯文档 URL 发给我，或自己用 MCP 查真实 file_id 填进 .env"
                )


# ── 1b. 腾讯文档连通性 ────────────────────────────────
def check_tencent_doc() -> None:
    section("2. 腾讯文档连通性")
    if not os.environ.get("TENCENT_DOC_FILE_ID", "").strip():
        item(NO, "跳过（缺 fileID）")
        return

    import doc_client as dc

    try:
        tok, src = dc._token()
        item(OK, "token 可用", f"来源：{src}（{len(tok)} 字符）")
    except Exception as exc:  # noqa: BLE001
        item(NO, "token 不可用", str(exc).splitlines()[0][:80])
        problems.append("腾讯文档 token 不可用")
        next_steps.append(
            "去 https://docs.qq.com/open/auth/mcp.html 生成个人 Token，"
            "填进 .env 的 TENCENT_MCP_TOKEN（服务器上必须这样做）"
        )
        return

    try:
        sheets = dc.list_tables()
    except Exception as exc:  # noqa: BLE001
        item(NO, "MCP 调用失败", str(exc).splitlines()[0][:80])
        problems.append("腾讯文档 MCP 调不通")
        return

    item(OK, "MCP 连通", f"{len(sheets)} 个子表")
    for s in sheets:
        item(OK, f"子表「{s.get('title')}」", s["sheet_id"])

    try:
        fields = dc.list_fields()
        item(OK, "列结构", " / ".join(fields))
        missing = [c for c in dc.CORE_COLUMNS if c not in fields]
        if missing:
            item(NO, "缺少必需列", str(missing))
            problems.append(f"表格缺少列：{missing}")
            next_steps.append("在腾讯文档里把列名改成：日期 / 星期 / 早餐 / 午餐 / 晚餐")
        extra = [c for c in ("加餐", "备注") if c in fields]
        if extra:
            item(WARN, "表里有 %s，程序不会写" % extra, "留着无害")
    except Exception as exc:  # noqa: BLE001
        item(NO, "列结构读取失败", str(exc).splitlines()[0][:80])

    try:
        records = dc.list_records()
        filled = [r for r in records if r["values"].get("日期", "").strip()]
        blank = len(records) - len(filled)
        item(OK, "记录读取正常",
             f"{len(filled)} 行有效" + (f"，{blank} 行空白" if blank else ""))
        for offset, label in ((0, "今天"), (1, "明天")):
            d = today() + dt.timedelta(days=offset)
            iso = d.isoformat()
            hit = [r for r in filled if r["values"].get("日期") == iso]
            if not hit:
                item(WARN, f"{label}（{iso}）没有数据", "当天无午/晚餐则静默不发")
                continue
            v = hit[0]["values"]
            meals = [m for m in dc.MEAL_COLUMNS if v.get(m)]
            item(OK if meals else WARN, f"{label}（{iso}）",
                 "、".join(meals) if meals else "三个餐次都空")
    except Exception as exc:  # noqa: BLE001
        item(NO, "记录读取失败", str(exc).splitlines()[0][:80])
        problems.append("腾讯文档记录读取失败")



# ── 2. QQ 鉴权 ────────────────────────────────────────
def check_qq() -> None:
    section("3. QQ 机器人鉴权")
    import bots

    for name in bots.list_bots():
        bot = bots.get_bot(name, strict=False)
        if not bot.appid or not bot.secret:
            item(NO, f"{bot.label} 跳过", "AppID/Secret 未填")
            continue

        try:
            token = bot.token()
        except Exception as exc:  # noqa: BLE001
            item(NO, f"{bot.label} 鉴权失败", str(exc)[:100])
            problems.append(f"{bot.label} AppID/AppSecret 不对，去开放平台重置")
            next_steps.append(f"到 QQ 开放平台 → 开发管理 → 重置「{bot.label}」的 AppSecret，更新 .env")
            continue

        item(OK, f"{bot.label} token", f"获取成功（{len(token)} 字符）")

        # 顺便查一下 gateway，说明机器人状态正常
        try:
            gw = requests.get(
                "https://api.sgroup.qq.com/gateway",
                headers={"Authorization": f"QQBot {token}"},
                timeout=15,
            ).json()
            if gw.get("url"):
                item(OK, f"{bot.label} gateway", gw["url"])
            else:
                item(WARN, f"{bot.label} gateway 异常", str(gw)[:80])
        except Exception as exc:  # noqa: BLE001
            item(WARN, f"{bot.label} gateway 查询失败", str(exc)[:60])


# ── 3. 解析能力 ───────────────────────────────────────
def check_parser() -> None:
    section("4. 食谱解析能力")
    vision = os.environ.get("LLM_VISION_MODEL", "").strip()
    llm_key = os.environ.get("LLM_API_KEY", "").strip()

    if vision and llm_key:
        item(OK, "多模态模型已配置", f"{vision}（能读食谱图）")
        return

    if llm_key:
        item(OK, "大模型 Key 已配置", os.environ.get("LLM_MODEL", "deepseek-chat"))
        item(NO, "缺视觉模型", "有 key 但没填 LLM_VISION_MODEL，读不了食谱图")
    else:
        item(WARN, "未配置 LLM", "图片食谱无法用外部 API 解析")

    print()
    print("  ⚠️  图片食谱必须有视觉能力，OCR 不可替代——")
    print("     表格信息在于「周三 ↔ 红烧带鱼」的对齐关系，OCR 逐行扫码会丢失这一关系。")
    print("     三个选择：")
    print("       ① 换 WorkBuddy 模型为多模态，让它自己读图后调 menu/scripts/save_menu.py（零 key）")
    print("       ② .env 填 LLM_VISION_MODEL + LLM_API_KEY")
    print("       ③ 直接在腾讯文档里手工填 5 行（一周一次）")
    next_steps.append(
        "解析食谱图需要视觉能力：把 WorkBuddy 模型换成多模态（之后自动化会自己读图），"
        "或在 .env 配 LLM_VISION_MODEL + LLM_API_KEY，或在腾讯文档手工填表"
    )


# ── 4. 食谱数据 ───────────────────────────────────────
def check_data() -> None:
    section("5. 本周数据覆盖情况")
    # 腾讯文档连通性已在第 2 节检查过，这里只回答"够不够发这一周"
    if not os.environ.get("TENCENT_DOC_FILE_ID", "").strip():
        item(WARN, "跳过（第 2 节已报错）")
        return

    try:
        import doc_client as dc

        records = [r for r in dc.list_records() if r["values"].get("日期", "").strip()]
    except Exception:  # noqa: BLE001
        item(WARN, "跳过（读不到表格）")
        return

    dates = sorted(r["values"]["日期"] for r in records)
    if not dates:
        item(NO, "表格里没有任何数据")
        problems.append("腾讯文档是空的")
        next_steps.append("把食谱截图放进 menu/inbox/，让 WorkBuddy 自动化读图后写入表格")
        return

    item(OK, "已录入", f"{len(dates)} 天：{dates[0]} ~ {dates[-1]}")

    d = today()
    upcoming = [x for x in dates if x >= d.isoformat()][:5]
    if upcoming:
        item(OK, "未来 5 个工作日有数据", "、".join(upcoming))
    else:
        item(WARN, "今天及以后没有数据", "到点不会发消息（符合预期，若无食谱）")

    # 寄宿制专项：周日有晚餐，周五那条消息才能预告「返校第一顿」
    sunday_dinners = []
    for r in records:
        raw = r["values"].get("日期", "").strip()
        if not raw or not r["values"].get("晚餐", "").strip():
            continue
        try:
            if dt.date.fromisoformat(raw).weekday() == 6:
                sunday_dinners.append(raw)
        except ValueError:
            continue
    if sunday_dinners:
        item(OK, "周日晚餐已录（周五会预告返校餐）", "、".join(sorted(sunday_dinners)[-3:]))
    else:
        item(WARN, "没有周日晚餐", "周五消息将无法预告返校第一顿——检查提取时周日是否漏录")


# ── 5. 服务器 ─────────────────────────────────────────
def check_server() -> None:
    section("6. 云服务器")
    cfg = load_config()
    dep = cfg.get("deploy") or {}
    host = dep.get("ssh_host", "").strip()
    if not host:
        item(NO, "deploy.ssh_host 未配置")
        problems.append("不知道服务器地址，无法上传")
        return

    port = str(dep.get("ssh_port", 22))
    remote = dep.get("remote_dir", "/opt/campus-bot")

    # 5.1 SSH 连通 + 免密
    key = dep.get("ssh_key", "").strip()
    key_args = ["-i", key] if key else []
    cmd = ["ssh", *key_args, "-p", port, "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host,
           "echo CONNECTED && ls -d " + remote + " 2>/dev/null"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
    except subprocess.TimeoutExpired:
        item(NO, "SSH 超时", host)
        problems.append("SSH 连不上服务器")
        return

    if res.returncode != 0:
        err = (res.stderr or "").strip().splitlines()
        reason = err[0] if err else f"exit {res.returncode}"
        item(NO, "SSH 连不上", f"{host} — {reason[:70]}")
        if "Permission denied" in (res.stderr or ""):
            problems.append("SSH 需要密钥，本机还没有配好免密")
            next_steps.append(
                "在 config.yaml 的 deploy.ssh_key 填 .pem 私钥路径，并把公钥加成 root 的 authorized_keys"
            )
        else:
            problems.append("SSH 无法连接")
        return

    out = res.stdout.strip().splitlines()
    item(OK, "SSH 免密可用", host)

    if any(line.endswith(remote) for line in out):
        item(OK, "服务器已装项目", remote)
    else:
        item(NO, "服务器上没有项目目录", remote)
        problems.append("服务器未部署")
        next_steps.append(
            "把项目传到服务器并跑 sudo bash common/deploy/server/install.sh（见 RUNBOOK 步骤 3）"
        )

    import bots

    # 5.2 常驻服务：监听群 @ 的机器人（reader / menu），检查进程是否在跑
    for name in bots.list_bots():
        if not bots.bot_listen(name):
            continue
        cmd2 = ["ssh", *key_args, "-p", port, "-o", "BatchMode=yes", host,
                f"systemctl is-active campus-bot-{name}.service 2>/dev/null || echo inactive"]
        try:
            r2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=20)
            state = r2.stdout.strip().splitlines()[-1] if r2.stdout.strip() else "unknown"
            if state == "active":
                item(OK, f"{name} 常驻服务在跑", "群里 @ 即可回复")
            else:
                item(WARN, f"{name} 常驻服务未启动", state)
                next_steps.append(
                    f"服务器上执行 sudo systemctl enable --now campus-bot-{name}.service"
                )
        except Exception:  # noqa: BLE001
            item(WARN, f"{name} 常驻服务状态未知")

    # 5.3 定时器状态：每个配了 schedule 的机器人各有一个 timer
    scheduled = [
        (name, bots.bot_schedule(name))
        for name in bots.list_bots()
        if bots.bot_schedule(name)
    ]
    if not scheduled:
        item(WARN, "没有定时机器人", "config.yaml 里所有 schedule 都为空")
        return

    for name, when in scheduled:
        cmd2 = ["ssh", *key_args, "-p", port, "-o", "BatchMode=yes", host,
                f"systemctl is-active campus-bot-{name}.timer 2>/dev/null || echo inactive"]
        try:
            r2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=20)
            state = r2.stdout.strip().splitlines()[-1] if r2.stdout.strip() else "unknown"
            if state == "active":
                item(OK, f"{name} 定时器已启用", f"每天 {when}")
            else:
                item(WARN, f"{name} 定时器未启用", state)
                next_steps.append(
                    f"服务器上执行 sudo systemctl enable --now campus-bot-{name}.timer"
                )
        except Exception:  # noqa: BLE001
            item(WARN, f"{name} 定时器状态未知")


# ── 汇总 ──────────────────────────────────────────────
def summary() -> None:
    print()
    print("=" * 60)
    if not problems:
        import bots

        print(f"{OK} 全部就绪。当前机器人：")
        for name in bots.list_bots():
            label = str(bots.bot_config(name).get("label") or name)
            handler = bots.bot_handler(name)
            when = bots.bot_schedule(name) or "手动"
            print(f"   · {label}（{name}）：{handler}，{when}")
        print("   食谱每周只需把截图放进 menu/inbox/，让 WorkBuddy 自动化读图写表")
        print("   文本机器人：python common/scripts/run_text.py --bot <键名> --text \"...\"")
    else:
        print(f"{NO} 有 {len(problems)} 项待处理：")
        for p in dict.fromkeys(problems):
            print(f"   · {p}")
        if next_steps:
            print()
            print("下一步动作（按顺序做）：")
            for i, s in enumerate(dict.fromkeys(next_steps), 1):
                print(f"   {i}. {s}")
    print("=" * 60)


def main() -> int:
    load_env()
    print("校园机器人 · 环境自检")
    print(f"时间：{dt.datetime.now():%Y-%m-%d %H:%M:%S}")

    check_env()
    check_tencent_doc()
    check_qq()
    check_parser()
    check_data()
    check_server()
    summary()
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
