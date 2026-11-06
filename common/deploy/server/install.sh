#!/usr/bin/env bash
# 云服务器一键初始化脚本（Ubuntu / Debian 系，需 sudo）
# 用法：把整个项目 scp/rsync 到服务器后，在项目根目录执行
#   sudo bash common/deploy/server/install.sh
#
# 做的事：
#   1) 建独立系统用户 campusbot（不用 root 跑脚本）
#   2) 装 python3-venv，创建 .venv，安装依赖
#   3) 时区设为 Asia/Shanghai（cron/timer 才不会偏 8 小时）
#   4) 按 config.yaml 的 bots 清单生成 systemd service + timer：
#      每个机器人都装 service，schedule 非空的额外装 timer
#   5) chmod 600 .env，防止密钥被其他用户读到
#   6) 清掉旧单元（school-menu-bot 及本次 config 里已删除的机器人）
set -euo pipefail

APP_DIR="/opt/campus-bot"
RUN_USER="campusbot"

echo "==> 检查运行位置"
if [ ! -f ./common/config.yaml ]; then
  echo "请在项目根目录执行（当前缺少 common/config.yaml）" >&2
  exit 1
fi

echo "==> 时区"
timedatectl set-timezone Asia/Shanghai || echo "警告：设置时区失败，请手动确认 timedatectl"

echo "==> 系统依赖"
# 第三方 apt 源（如 docker-ce 镜像）偶尔同步不全，update 失败不该阻断安装
apt-get update -qq || echo "警告：apt-get update 部分源失败，继续尝试安装"
apt-get install -y -qq python3 python3-venv python3-pip tzdata ca-certificates \
  || echo "警告：部分系统包安装失败，若 venv 创建失败请手动装 python3-venv"

echo "==> 创建用户 $RUN_USER"
id -u "$RUN_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$RUN_USER"

echo "==> 部署到 $APP_DIR"
mkdir -p "$APP_DIR"
# 如果已经在目标目录里跑，就跳过同步（rsync 自己到自己会报错）
CUR="$(cd "$(dirname ./)"; pwd -P)"
if [ "$CUR" = "$(cd "$APP_DIR" && pwd -P)" ]; then
  echo "    已在 $APP_DIR，跳过文件同步"
elif command -v rsync >/dev/null 2>&1; then
  rsync -a --delete \
    --exclude '.venv' --exclude '__pycache__' --exclude '.git' \
    --exclude 'menu/data/cache' --exclude 'logs' \
    --exclude 'reader/data' --exclude 'reader/out' \
    ./ "$APP_DIR"/
else
  cp -r ./ "$APP_DIR"/
fi

echo "==> 创建虚拟环境"
cd "$APP_DIR"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q -r common/requirements.txt

echo "==> 权限"
mkdir -p logs menu/data reader/data
if [ -f common/.env ]; then
  chmod 600 common/.env
else
  echo "⚠️  还没有 common/.env，复制 common/.env.example 后填入真实值："
  echo "    cp common/.env.example common/.env && nano common/.env && chmod 600 common/.env"
fi
chown -R "$RUN_USER":"$RUN_USER" "$APP_DIR"

echo "==> 清理旧版单元"
shopt -s nullglob
if [ -f /etc/systemd/system/school-menu-bot.timer ]; then
  systemctl disable --now school-menu-bot.timer 2>/dev/null || true
  rm -f /etc/systemd/system/school-menu-bot.timer /etc/systemd/system/school-menu-bot.service
  echo "    已移除旧版 school-menu-bot 定时器"
fi
# 先停掉再删，config 里已经去掉的机器人不会留下野定时器
for unit in /etc/systemd/system/campus-bot-*.service /etc/systemd/system/campus-bot-*.timer; do
  systemctl disable --now "$(basename "$unit")" 2>/dev/null || true
done
rm -f /etc/systemd/system/campus-bot-*.service /etc/systemd/system/campus-bot-*.timer

echo "==> 按 config.yaml 生成机器人单元"
UNITS_TMP="$(mktemp -d)"
./.venv/bin/python common/deploy/server/render_units.py "$UNITS_TMP" \
  --app-dir "$APP_DIR" --user "$RUN_USER"
for unit in "$UNITS_TMP"/campus-bot-*.service; do
  install -m 644 "$unit" /etc/systemd/system/
done
for unit in "$UNITS_TMP"/campus-bot-*.timer; do
  install -m 644 "$unit" /etc/systemd/system/
done
rm -rf "$UNITS_TMP"
systemctl daemon-reload
for timer in /etc/systemd/system/campus-bot-*.timer; do
  systemctl enable --now "$(basename "$timer")"
done
# 常驻机器人（handler: reader 这类 Type=simple）没有 timer，需要单独拉起
for unit in /etc/systemd/system/campus-bot-*.service; do
  if grep -q '^Type=simple' "$unit"; then
    systemctl enable --now "$(basename "$unit")"
  fi
done

echo
echo "==> 完成，当前定时器："
systemctl list-timers 'campus-bot-*' --no-pager || true
echo
echo "手动测试食谱（先看预览，不发消息）："
echo "  sudo -u $RUN_USER $APP_DIR/.venv/bin/python $APP_DIR/menu/scripts/run_daily.py --bot menu --preview"
echo "真发一条食谱："
echo "  sudo -u $RUN_USER $APP_DIR/.venv/bin/python $APP_DIR/menu/scripts/run_daily.py --bot menu"
echo "手动发一条通知："
echo "  sudo -u $RUN_USER $APP_DIR/.venv/bin/python $APP_DIR/common/scripts/run_text.py --bot notice --text \"测试通知\""
echo "操前小读（常驻监听，群里 @ 它即可）："
echo "  systemctl status campus-bot-reader.service"
echo "  tail -f $APP_DIR/logs/reader.log"
echo "看日志（每个机器人一份）："
echo "  ls $APP_DIR/logs/"
echo
echo "⚠️ 别忘了把服务器的公网 IP 加进每个 QQ 机器人后台的 IP 白名单，否则发送会 403。"
