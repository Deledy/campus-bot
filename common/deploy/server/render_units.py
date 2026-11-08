"""按 config.yaml 的 bots 清单生成 systemd service / timer。

install.sh 会调用它：加机器人只需改 config.yaml + .env，然后重跑 install.sh，
不用再手工复制 unit 文件。

    python common/deploy/server/render_units.py <输出目录>
    python common/deploy/server/render_units.py /etc/systemd/system --app-dir /opt/campus-bot --user campusbot
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "common" / "py"))
import bots  # noqa: E402

# handler -> 服务器上真正执行的入口（一次性任务，可配 timer）
ENTRY = {
    "menu": "menu/scripts/run_daily.py",
    "text": "common/scripts/run_text.py",
}

# handler -> 常驻进程（等群 @ 事件）。只有 config 里 listen 为 true 时才启用；
# reader 天生常驻。同一个机器人可以「常驻 + 定时」并存，各自一个 service。
DAEMON = {
    "menu": "menu/scripts/serve_menu.py",
    "reader": "reader/scripts/serve_reader.py",
}

SERVICE = """[Unit]
Description=Campus bot 「{label}」({name}) -> QQ group
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User={user}
WorkingDirectory={app_dir}
Environment=PYTHONUNBUFFERED=1
ExecStart={app_dir}/.venv/bin/python {app_dir}/{entry} --bot {name}
StandardOutput=append:{app_dir}/logs/{name}.log
StandardError=append:{app_dir}/logs/{name}.log
Restart=on-failure
RestartSec=120
"""

SERVICE_DAEMON = """[Unit]
Description=Campus bot 「{label}」({name}) long-running listener
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={user}
WorkingDirectory={app_dir}
Environment=PYTHONUNBUFFERED=1
ExecStart={app_dir}/.venv/bin/python {app_dir}/{entry} --bot {name}
StandardOutput=append:{app_dir}/logs/{name}.log
StandardError=append:{app_dir}/logs/{name}.log
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
"""

TIMER = """[Unit]
Description=Daily {time} push for campus bot {name}

[Timer]
OnCalendar=*-*-* {time}:00
Persistent=true
AccuracySec=1min
Unit=campus-bot-{unit}.service

[Install]
WantedBy=timers.target
"""

VALID_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
VALID_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", help="生成的 unit 放哪（通常是 /etc/systemd/system）")
    ap.add_argument("--app-dir", default="/opt/campus-bot")
    ap.add_argument("--user", default="campusbot")
    args = ap.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    names = bots.list_bots()
    if not names:
        raise SystemExit("config.yaml 里没有任何机器人")

    for name in names:
        if not VALID_NAME.match(name):
            raise SystemExit(f"机器人键名「{name}」只能用小写字母/数字/连字符")

        handler = bots.bot_handler(name)
        label = str(bots.bot_config(name).get("label") or name)
        schedule = bots.bot_schedule(name)
        daemon = DAEMON.get(handler) if bots.bot_listen(name) else None
        scheduled = ENTRY.get(handler)

        if not daemon and not scheduled:
            print(f"  {name}：handler={handler} 没有可用入口，跳过")
            continue
        if scheduled and schedule and not VALID_TIME.match(schedule):
            raise SystemExit(
                f"机器人「{name}」的 schedule={schedule!r} 不对，要写成 HH:MM（例如 09:50）"
            )
        if daemon and schedule and not scheduled:
            print(f"  {name}：常驻服务不需要 schedule，已忽略 {schedule!r}")
            schedule = ""

        common = {
            "label": label,
            "name": name,
            "user": args.user,
            "app_dir": args.app_dir.rstrip("/"),
        }

        if daemon:
            (out / f"campus-bot-{name}.service").write_text(
                SERVICE_DAEMON.format(entry=daemon, **common), encoding="utf-8"
            )

        if scheduled:
            # 常驻和定时并存时，定时任务用独立的 <name>-push.service，
            # timer 文件名仍是 campus-bot-<name>.timer，管理习惯不变。
            push = f"{name}-push" if daemon else name
            (out / f"campus-bot-{push}.service").write_text(
                SERVICE.format(entry=scheduled, **common), encoding="utf-8"
            )
            if schedule:
                (out / f"campus-bot-{name}.timer").write_text(
                    TIMER.format(time=schedule, name=name, unit=push),
                    encoding="utf-8",
                )

    print("已生成：")
    for name in names:
        handler = bots.bot_handler(name)
        schedule = bots.bot_schedule(name)
        daemon = DAEMON.get(handler) if bots.bot_listen(name) else None
        scheduled = ENTRY.get(handler)
        if daemon:
            print(f"  campus-bot-{name}.service（常驻）")
        if scheduled:
            push = f"{name}-push" if daemon else name
            when = f" + timer {schedule}" if schedule else "（手动）"
            print(f"  campus-bot-{push}.service{when}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
