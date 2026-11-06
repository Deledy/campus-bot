"""公共工具：配置加载、路径、日志。"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

# common/py/common.py -> 项目根
ROOT = Path(__file__).resolve().parents[2]
COMMON_DIR = ROOT / "common"
MENU_DIR = ROOT / "menu"
READER_DIR = ROOT / "reader"
MENU_DATA_DIR = MENU_DIR / "data"
READER_DATA_DIR = READER_DIR / "data"

# Windows 上如果没有装 tzdata 包，ZoneInfo 会失败；退化成固定 UTC+8（中国无夏令时，等价）
try:
    TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # noqa: BLE001
    TZ = dt.timezone(dt.timedelta(hours=8), name="Asia/Shanghai")


def load_env() -> None:
    """读 common/.env（没有就跳过，云函数里直接用环境变量）。"""
    env_path = COMMON_DIR / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def load_config() -> dict:
    import yaml

    load_env()
    with open(COMMON_DIR / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def today() -> dt.date:
    """按北京时间取今天，避免云函数跑在 UTC 时差一天。"""
    return dt.datetime.now(TZ).date()


def log(msg: str) -> None:
    print(f"[{dt.datetime.now(TZ):%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def must_env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        sys.exit(f"缺少环境变量 {name}，请检查 common/.env")
    return val


def read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log(f"警告：{path.name} 解析失败（{exc}），使用默认值")
        return default


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
