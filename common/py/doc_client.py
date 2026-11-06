"""腾讯文档 MCP 客户端 —— 读写智能表格的统一入口。

## 为什么用 MCP 而不是 OpenAPI v2

实测（2026-10-21）证明 MCP 是更省事的路径：

    OpenAPI v2:  POST /openapi/smartbook/v2/...  需要三个 Header
                 (Access-Token + Client-Id + Open-Id)，且应用要有 scope.smartsheet，
                 否则一律返回 ret=200063（msg 为空，官方文档都查不到这个码）

    MCP:         POST https://docs.qq.com/openapi/mcp   只需要一个 Authorization 头
                 标准 JSON-RPC，无需 initialize 握手，官方推荐给个人开发者用

所以本项目统一走 MCP，PC 和服务器用同一套代码，只是 token 来源不同。

## Token 从哪来（两种，按优先级自动选）

1. 环境变量 `TENCENT_MCP_TOKEN`
   —— 去 https://docs.qq.com/open/auth/mcp.html 生成，填进 .env。
   **服务器上必须用这个**，因为它不依赖 WorkBuddy。

2. WorkBuddy 宿主票据
   —— 本机跑自动化时，从 tencent-docs 插件的凭据网关自动取，不用手动配。
   PC 上做完授权就能直接用，省掉配置。

## 用法

    import doc_client
    doc_client.list_records()                    # 读全表
    doc_client.get_day(date(2026,10,21))         # 读某天
    doc_client.upsert_days([{...}, {...}])       # 按日期 upsert

CLI：
    python common/py/doc_client.py ping            # 测连通
    python common/py/doc_client.py tables
    python common/py/doc_client.py fields
    python common/py/doc_client.py records
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import load_env, log  # noqa: E402

MCP_URL = "https://docs.qq.com/openapi/mcp"
TIMEOUT = 25

# 表格列（与腾讯文档里的列标题逐字一致）
CORE_COLUMNS = ["日期", "星期", "早餐", "午餐", "晚餐"]
OPTIONAL_COLUMNS = ["更新时间"]
MEAL_COLUMNS = ["早餐", "午餐", "晚餐"]
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

HOST_SKILL_DIR = Path(
    r"C:/Users/Deledy_02/.workbuddy/plugins/cache/workbuddy-builtin/"
    r"tencent-docs-plugin/5.8.3-wb.41334564.gdb0e9b51.h4da087921e4a/skills/tencent-docs"
)

_token_cache: dict[str, str] = {"value": "", "source": ""}


class DocError(RuntimeError):
    pass


# ── 认证 ──────────────────────────────────────────────
def _token() -> tuple[str, str]:
    """返回 (token, 来源说明)。"""
    if _token_cache["value"]:
        return _token_cache["value"], _token_cache["source"]

    env_tok = os.environ.get("TENCENT_MCP_TOKEN", "").strip()
    if env_tok:
        _token_cache.update(value=env_tok, source="TENCENT_MCP_TOKEN")
        return env_tok, "TENCENT_MCP_TOKEN"

    host = _host_token()
    if host:
        _token_cache.update(value=host, source="WorkBuddy 宿主票据")
        return host, "WorkBuddy 宿主票据"

    raise DocError(
        "没有可用的腾讯文档 token。两种解决方式：\n"
        "  ① 打开 https://docs.qq.com/open/auth/mcp.html 生成个人 Token，\n"
        "     填进 .env 的 TENCENT_MCP_TOKEN（服务器上必须这样做）\n"
        "  ② 在本机 WorkBuddy 里跑（会自动用宿主票据，无需配置）"
    )


def _host_token() -> str:
    """从 tencent-docs 插件的凭据网关取宿主票据（仅本机 WorkBuddy 环境有效）。"""
    if not HOST_SKILL_DIR.exists():
        return ""
    if str(HOST_SKILL_DIR) not in sys.path:
        sys.path.insert(0, str(HOST_SKILL_DIR))
    try:
        import tencentdocs  # type: ignore

        oauth, _oneid, _base = tencentdocs._load_tokens()
        return (oauth or "").strip()
    except Exception:  # noqa: BLE001
        return ""


# ── JSON-RPC 基础 ─────────────────────────────────────
def call(tool: str, arguments: dict) -> dict:
    """调用一个 MCP 工具，返回 structuredContent（或解析后的 text）。"""
    token, source = _token()
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    try:
        resp = requests.post(
            MCP_URL,
            headers={"Authorization": token, "Content-Type": "application/json"},
            json=body,
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise DocError(f"连不上腾讯文档 MCP（{source}）：{exc}") from exc

    if resp.status_code == 401:
        raise DocError(
            f"MCP token 无效或过期（来源：{source}）。\n"
            "  重新去 https://docs.qq.com/open/auth/mcp.html 生成，更新 .env 的 TENCENT_MCP_TOKEN"
        )
    if resp.status_code != 200:
        raise DocError(f"MCP 返回 HTTP {resp.status_code}：{resp.text[:300]}")

    data = resp.json()
    if "error" in data:
        err = data["error"]
        raise DocError(f"MCP 错误 {err.get('code')}：{err.get('message')}")

    result = data.get("result") or {}
    if result.get("isError"):
        raise DocError(f"工具 {tool} 执行失败：{_text_of(result)[:300]}")

    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        if structured.get("error"):
            raise DocError(f"工具 {tool} 返回错误：{structured['error']}")
        return structured

    text = _text_of(result)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"_raw": text}
    if isinstance(parsed, dict) and parsed.get("error"):
        raise DocError(f"工具 {tool} 返回错误：{parsed['error']}")
    return parsed if isinstance(parsed, dict) else {"_raw": parsed}


def _text_of(result: dict) -> str:
    parts = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
    return "".join(parts)


# ── 配置 ──────────────────────────────────────────────
def _file_id() -> str:
    fid = os.environ.get("TENCENT_DOC_FILE_ID", "").strip()
    if not fid:
        raise DocError("缺少 TENCENT_DOC_FILE_ID（腾讯文档 URL 里 /smartsheet/ 后面那串不是它，"
                       "要用 query_file_info 拿到的真实 file_id）")
    return fid


def _sheet_id() -> str:
    sid = os.environ.get("TENCENT_DOC_SHEET_ID", "").strip()
    if sid:
        return sid
    return resolve_sheet_id()


def resolve_sheet_id(name: str = "") -> str:
    want = name or os.environ.get("TENCENT_DOC_SHEET_NAME", "").strip()
    sheets = list_tables()
    if not sheets:
        raise DocError("该文档没有子表")
    if not want:
        sid = sheets[0]["sheet_id"]
        log(f"使用子表「{sheets[0].get('title')}」（{sid}）")
        return sid
    for s in sheets:
        if s.get("title") == want:
            return s["sheet_id"]
    raise DocError(f"找不到子表「{want}」，现有：{[s.get('title') for s in sheets]}")


# ── 读 ────────────────────────────────────────────────
def list_tables() -> list[dict]:
    data = call("smartsheet.list_tables", {"file_id": _file_id()})
    return data.get("sheets") or []


def list_fields() -> list[str]:
    data = call("smartsheet.list_fields", {"file_id": _file_id(), "sheet_id": _sheet_id()})
    return [f.get("field_title", "") for f in (data.get("fields") or [])]


def list_records() -> list[dict]:
    """返回 [{"record_id":..., "values": {列名: 文本}}]。"""
    data = call("smartsheet.list_records", {"file_id": _file_id(), "sheet_id": _sheet_id()})
    out = []
    for rec in data.get("records") or []:
        out.append({
            "record_id": rec.get("record_id", ""),
            "values": _flatten(rec.get("field_values") or []),
        })
    return out


def _flatten(field_values: list) -> dict:
    """把 [{field, text_value:{items:[{text}]}}] 拍平成 {列名: 文本}。"""
    vals: dict[str, str] = {}
    for fv in field_values:
        name = fv.get("field") or ""
        if not name:
            continue
        tv = fv.get("text_value") or {}
        items = tv.get("items") or []
        txt = "".join(str(i.get("text", "")) for i in items if isinstance(i, dict))
        if not txt:
            sv = fv.get("string_value")
            if isinstance(sv, str):
                txt = sv
        vals[name] = txt
    return vals


def get_day(target: dt.date) -> dict | None:
    """取某天的食谱，转成内部结构 {date, weekday, meals, note}。"""
    iso = target.isoformat()
    for rec in list_records():
        v = rec["values"]
        if v.get("日期") != iso:
            continue
        meals: dict[str, list[str]] = {}
        for meal in MEAL_COLUMNS:
            dishes = [d.strip() for d in v.get(meal, "").split("、") if d.strip()]
            if dishes:
                meals[meal] = dishes
        if not meals:
            log(f"表格里 {iso} 这一行没有菜品")
            return None
        return {
            "date": iso,
            "weekday": v.get("星期") or WEEKDAYS[target.weekday()],
            "meals": meals,
            "note": "",
        }
    return None


def get_week() -> dict | None:
    """整表转回内部 schema_version=1 结构。"""
    days = []
    for rec in list_records():
        v = rec["values"]
        date = v.get("日期", "").strip()
        if not date:
            continue
        try:
            dt.date.fromisoformat(date)
        except ValueError:
            continue
        meals = {}
        for meal in MEAL_COLUMNS:
            dishes = [d.strip() for d in v.get(meal, "").split("、") if d.strip()]
            if dishes:
                meals[meal] = dishes
        if meals:
            days.append({
                "date": date,
                "weekday": v.get("星期", ""),
                "meals": meals,
                "note": "",
            })
    if not days:
        return None
    days.sort(key=lambda d: d["date"])
    return {
        "schema_version": 1,
        "week_start": days[0]["date"],
        "week_end": days[-1]["date"],
        "days": days,
        "parsed_at": dt.datetime.now().isoformat(timespec="seconds"),
    }


# ── 写 ────────────────────────────────────────────────
def _text_cell(text: str) -> dict:
    return {"text_value": {"items": [{"text": text, "type": "text"}]}}


def _row_field_values(day: dict, writable: list[str], stamp: str | None = None) -> list[dict]:
    date = day["date"]
    weekday = day.get("weekday") or WEEKDAYS[dt.date.fromisoformat(date).weekday()]
    row = {"日期": date, "星期": weekday}
    for meal in MEAL_COLUMNS:
        dishes = day.get("meals", {}).get(meal) or []
        # 顿号是单元格分隔符，菜名里的顿号换成逗号，避免读回来拆错
        row[meal] = "、".join(d.replace("、", "，") for d in dishes)
    row["更新时间"] = stamp or dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    return [
        {"field": col, **_text_cell(row[col])}
        for col in writable
        if col in row
    ]


def upsert_days(days: list[dict]) -> dict:
    """按「日期」upsert。已存在则 update，不存在则 add。重复跑不会产生重复行。"""
    fields = list_fields()
    missing = [c for c in CORE_COLUMNS if c not in fields]
    if missing:
        raise DocError(
            f"表格缺少必需列：{missing}\n  当前列：{fields}\n"
            "  去腾讯文档里补上（全部选「文本」类型），或在表格里手动改名"
        )
    writable = [c for c in CORE_COLUMNS + OPTIONAL_COLUMNS if c in fields]
    if "更新时间" not in fields:
        log("表里没有「更新时间」列，跳过该字段")

    existing = {r["values"].get("日期", "").strip(): r["record_id"] for r in list_records()}

    to_add, to_update = [], []
    for day in days:
        fvs = _row_field_values(day, writable)
        rid = existing.get(day["date"])
        if rid:
            to_update.append({"record_id": rid, "field_values": fvs})
        else:
            to_add.append({"field_values": fvs})

    result = {"added": 0, "updated": 0}
    fid, sid = _file_id(), _sheet_id()

    if to_update:
        call("smartsheet.update_records",
             {"file_id": fid, "sheet_id": sid, "records": to_update})
        result["updated"] = len(to_update)
    if to_add:
        call("smartsheet.add_records",
             {"file_id": fid, "sheet_id": sid, "records": to_add})
        result["added"] = len(to_add)

    log(f"腾讯文档同步完成：新增 {result['added']} 行，更新 {result['updated']} 行")
    return result


# ── CLI ───────────────────────────────────────────────
def main() -> int:
    load_env()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "ping"
    try:
        if cmd == "ping":
            tok, src = _token()
            print(f"token 来源：{src}（长度 {len(tok)}）")
            sheets = list_tables()
            print(f"连通正常，{len(sheets)} 个子表")
            for s in sheets:
                print(f"  {s['sheet_id']}  {s.get('title')}")
        elif cmd == "tables":
            for s in list_tables():
                print(f"  {s['sheet_id']}  {s.get('title')}")
        elif cmd == "fields":
            print(" / ".join(list_fields()))
        elif cmd == "records":
            for r in list_records():
                v = r["values"]
                print(f"  {v.get('日期','?'):<12} {v.get('星期',''):<4} "
                      f"早:{v.get('早餐','')[:16]:<18} 午:{v.get('午餐','')[:16]:<18} "
                      f"晚:{v.get('晚餐','')[:16]}")
        elif cmd == "week":
            w = get_week()
            print(json.dumps(w, ensure_ascii=False, indent=2) if w else "表里没有数据")
        elif cmd == "today":
            d = get_day(dt.date.today())
            print(json.dumps(d, ensure_ascii=False, indent=2) if d else "当天没有数据")
        else:
            print(__doc__)
    except DocError as exc:
        log(f"❌ {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
