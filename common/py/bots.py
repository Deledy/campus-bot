"""多机器人注册表：按名字取凭据、拿 token、发消息。

机器人清单写在 config.yaml 的 bots 段，想加几个都可以：

    bots:
      menu:
        label: "食谱机器人"
        handler: menu          # menu = 每日食谱；text = 发 common/inbox/<键名>/ 的稿子
        schedule: "09:50"      # 服务器定时；留空 = 手动
        appid_env: BOT_MENU_APPID
        secret_env: BOT_MENU_SECRET
        group_env: BOT_MENU_GROUP_OPENID

凭据的环境变量名可以省略，默认按约定取：
    BOT_<键名大写>_APPID / BOT_<键名大写>_SECRET / BOT_<键名大写>_GROUP_OPENID

用法：

    import bots
    bot = bots.get_bot("menu")
    bot.send_text("今天吃什么")

    bots.get_token("notice")     # 只要 access_token 时
    bots.bot_handler("notice")   # menu / text
    bots.bot_inbox("notice")     # 这个文本机器人去哪取稿

注意：即使多个机器人在同一个群，group_openid 也各自不同——
openid 是按机器人区分的，不能共用，要各自抓一次。
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from pathlib import Path

import requests

from common import COMMON_DIR, load_config, load_env

API = "https://api.sgroup.qq.com"
TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
DEFAULT_BOT = "menu"
HANDLERS = ("menu", "text", "reader")

# 字段 -> config.yaml 里声明环境变量名的键
FIELDS = {
    "appid": "appid_env",
    "secret": "secret_env",
    "group_openid": "group_env",
}

# 旧版单机器人项目的变量名，迁移期给 menu 兜底
LEGACY_ENV = {
    "appid_env": "QQ_APPID",
    "secret_env": "QQ_SECRET",
    "group_env": "QQ_GROUP_OPENID",
}

_token_cache: dict[str, dict[str, object]] = {}


class BotConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Bot:
    name: str
    label: str
    appid: str
    secret: str
    group_openid: str

    def token(self, force: bool = False) -> str:
        """取 access_token，按机器人分别缓存，提前 60 秒过期。"""
        now = dt.datetime.now()
        cache = _token_cache.setdefault(
            self.name, {"value": "", "expire_at": dt.datetime.min}
        )
        if not force and cache["value"] and now < cache["expire_at"]:  # type: ignore[operator]
            return str(cache["value"])

        resp = requests.post(
            TOKEN_URL,
            json={"appId": self.appid, "clientSecret": self.secret},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if "access_token" not in data:
            raise BotConfigError(f"机器人「{self.label}」鉴权失败：{data}")

        ttl = int(data.get("expires_in", 7200))
        cache["value"] = data["access_token"]
        cache["expire_at"] = now + dt.timedelta(seconds=max(ttl - 60, 60))
        return str(data["access_token"])

    def send_text(self, content: str) -> dict:
        from send_qq import send_group_text  # 延迟导入，避免循环依赖

        return send_group_text(self.group_openid, content, bot=self.name)


# ── 配置读取 ──────────────────────────────────────────
def list_bots() -> list[str]:
    load_env()
    return list((load_config().get("bots") or {}).keys())


def bot_config(name: str) -> dict:
    load_env()
    entry = (load_config().get("bots") or {}).get(name)
    if not isinstance(entry, dict):
        raise BotConfigError(f"config.yaml 的 bots 段里没有机器人「{name}」")
    return entry


def bot_env_names(name: str) -> dict[str, str]:
    """返回 {appid/secret/group_openid: 对应的环境变量名}。

    配置里没写 *_env 时按命名约定补：BOT_<NAME>_APPID / _SECRET / _GROUP_OPENID。
    """
    entry = bot_config(name)
    prefix = "BOT_" + name.upper().replace("-", "_")
    convention = {
        "appid_env": f"{prefix}_APPID",
        "secret_env": f"{prefix}_SECRET",
        "group_env": f"{prefix}_GROUP_OPENID",
    }
    return {
        field: str(entry.get(key) or convention[key]).strip()
        for field, key in FIELDS.items()
    }


def bot_handler(name: str) -> str:
    """这个机器人发什么：menu（食谱）或 text（文本稿）。"""
    handler = str(bot_config(name).get("handler") or "text").strip()
    if handler not in HANDLERS:
        raise BotConfigError(
            f"机器人「{name}」的 handler={handler!r} 不认识（只支持 {' / '.join(HANDLERS)}）"
        )
    return handler


def bot_inbox(name: str) -> Path:
    """text 机器人的稿子目录；默认 common/inbox/<键名>。"""
    raw = str(bot_config(name).get("inbox") or "").strip()
    path = Path(raw) if raw else Path("inbox") / name
    return path if path.is_absolute() else COMMON_DIR / path


def bot_schedule(name: str) -> str:
    """服务器定时时间（HH:MM）；空字符串表示不定时。"""
    return str(bot_config(name).get("schedule") or "").strip()


def _value(env_name: str, field: str, name: str) -> str:
    val = os.environ.get(env_name, "").strip() if env_name else ""
    # 迁移期：旧变量名只给默认机器人兜底
    if not val and name == DEFAULT_BOT:
        val = os.environ.get(LEGACY_ENV[FIELDS[field]], "").strip()
    return val


def get_bot(name: str = DEFAULT_BOT, strict: bool = True) -> Bot:
    """取机器人。strict=True 时缺凭据直接报错，便于发送前失败得早。"""
    entry = bot_config(name)
    names = bot_env_names(name)
    bot = Bot(
        name=name,
        label=str(entry.get("label") or name),
        appid=_value(names["appid"], "appid", name),
        secret=_value(names["secret"], "secret", name),
        group_openid=_value(names["group_openid"], "group_openid", name),
    )
    if strict:
        missing = [field for field in FIELDS if not getattr(bot, field)]
        if missing:
            env_names = bot_env_names(name)
            hint = "、".join(env_names[field] or field for field in missing)
            raise BotConfigError(
                f"机器人「{bot.label}」缺少配置：{hint}（写进 .env）"
            )
    return bot


def get_token(name: str = DEFAULT_BOT, force: bool = False) -> str:
    # 取 token 只需要 AppID/Secret；group_openid 是发送时才用到的
    return get_bot(name, strict=False).token(force=force)
