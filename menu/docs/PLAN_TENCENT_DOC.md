# 腾讯文档方案（已实测跑通）

> 2026-10-21 更新：**已用你的真实文档端到端验证通过**——列名改好、5 天数据写入、读回无误、
> 群消息成功渲染。本文记录最终方案和踩过的坑。

---

## 结论：走 MCP，不走 OpenAPI

我先按官方 OpenAPI v2 文档实现了完整读写（`/openapi/smartbook/v2/...` + 三个 Header），
结果一路撞墙：

| 尝试 | 结果 | 说明 |
|---|---|---|
| URL 里的 `DTmh1SWZIZGZGc05w` 当 fileID | `ret=10202 Folder not exists` | **URL 里那串是编码形式，不是真实 id** |
| 换成真实 fileID `NhuIfHdfFsNp` | `ret=200063`（msg 为空） | 官方文档查不到这个码 |
| `smartsheet` 路径 | 404 | 路径必须叫 `smartbook` |
| 用宿主票据调 OpenAPI | `ret=10301 Token check error` | 票据体系不通用 |

**换 MCP 后一次通过：**

```
POST https://docs.qq.com/openapi/mcp
Authorization: <token>
Content-Type: application/json

{"jsonrpc":"2.0","id":1,"method":"tools/call",
 "params":{"name":"smartsheet.list_records",
           "arguments":{"file_id":"NhuIfHdfFsNp","sheet_id":"t00i2h"}}}
```

一个头、标准 JSON-RPC、不需要 initialize 握手。这是官方给个人开发者准备的路径
（https://docs.qq.com/open/auth/mcp.html 自助生成 Token），比 OpenAPI 简单一个数量级。

所以 `scripts/tencent_doc.py`（OpenAPI 版）已删除，改为 `common/py/doc_client.py`（MCP 版）。

---

## 两个必须记住的坑

### 坑 1：URL 里的 id ≠ file_id

```
浏览器地址栏：https://docs.qq.com/smartsheet/DTmh1SWZIZGZGc05w?tab=t00i2h&viewId=v2JKhc
                                                  ↑ 这个不是 file_id

真实 file_id：NhuIfHdfFsNp        ← 只有 API 才查得到
真实 sheet_id：t00i2h             ← tab 参数正好是它（运气好）
```

如果 API 报 `10202 Folder not exists`，99% 是 fileID 用错了，不是权限问题。

### 坑 2：空白智能表格自带空行

新建的智能表格模板自带 9 行空记录，会污染 `list_records` 结果
（读出来 14 行，其中 9 行全空）。

处理：`doc_client.upsert_days()` 按「日期」建索引，空行日期为空所以永远匹配不到，
不会被覆盖也不干扰查找。但**建议先清一次**，否则你手机上会看到一堆空行：

```python
import doc_client as dc
empty = [r["record_id"] for r in dc.list_records() if not r["values"].get("日期")]
dc.call("smartsheet.delete_records",
        {"file_id": dc._file_id(), "sheet_id": dc._sheet_id(), "record_ids": empty})
```

已帮你清过了，现在表里正好 5 行。

---

## 数据格式

### 表格结构（已按你的要求建好）

子表「智能表1」（sheetID `t00i2h`），只有 5 列，全部「文本」类型：

| 日期 | 星期 | 早餐 | 午餐 | 晚餐 |
|---|---|---|---|---|
| 2026-10-19 | 周一 | 小米粥、水煮蛋、牛奶 | 米饭、番茄牛腩、清炒时蔬、紫菜蛋花汤 | |
| 2026-10-20 | 周二 | 燕麦牛奶、全麦面包、苹果 | 米饭、香菇滑鸡、蒜蓉西兰花、冬瓜排骨汤 | |
| 2026-10-21 | 周三 | 南瓜粥、肉包、豆浆 | 米饭、红烧带鱼、蚝油生菜、萝卜粉丝汤 | |

规则见 [DATA_FORMAT.md](DATA_FORMAT.md)。核心三条：

1. 一行一天，日期是唯一键
2. 日期用**文本** `YYYY-MM-DD`，不用日期类型（避开时间戳时区坑）
3. 多道菜用顿号 `、` 分隔

程序会先读你的实际列名，只写存在的列。加「更新时间」列就自动写，删了也不报错。

### 中间 JSON

```json
{"schema_version": 1, "week_start": "2026-10-26", "week_end": "2026-10-30",
 "days": [{"date": "2026-10-26", "weekday": "周一",
           "meals": {"早餐": ["小米粥"], "午餐": ["米饭","红烧肉"]}}]}
```

只是 PC 端的中间产物（留档用），腾讯文档才是唯一真相。

---

## 每天发什么

固定三块：**当日午餐 + 当日晚餐 + 次日早餐**。

```
10月21日（周三）
【午餐】
· 米饭
· 红烧带鱼
· 蚝油生菜
· 萝卜粉丝汤

—— 明日早餐 · 10月22日 ——
· 八宝粥
· 茶叶蛋
· 牛奶
```

| 情况 | 行为 |
|---|---|
| 当天有午餐/晚餐 | 发送 |
| 次日有早餐 | 附末尾 |
| 次日无早餐（周五） | 只发当天 |
| 当天只有早餐或全无（周末/假期） | **完全不发** |

周末假期自动静默，不用手动停任务。

---

## Token 的两种来源（部署关键）

`doc_client.py` 按优先级自动选：

| 优先级 | 来源 | 适用 | 怎么来 |
|---|---|---|---|
| 1 | `TENCENT_MCP_TOKEN` 环境变量 | **服务器必须用这个** | https://docs.qq.com/open/auth/mcp.html 生成 |
| 2 | WorkBuddy 宿主票据 | 本机跑自动化 | 自动取，无需配置 |

PC 上现在用的是宿主票据，所以能立刻跑；**但服务器上没有 WorkBuddy，必须配 `TENCENT_MCP_TOKEN`**。

**这是部署前唯一的拦路石**：去那个页面点一下生成 Token（免费、即时），填进服务器 `.env` 即可。

---

## 命令速查

```bash
python common/py/doc_client.py ping       # 测连通 + 看 token 来源
python common/py/doc_client.py tables     # 子表列表
python common/py/doc_client.py fields     # 列名
python common/py/doc_client.py records    # 读全表
python common/py/doc_client.py today      # 今天
python common/py/doc_client.py week       # 整周 JSON

python menu/scripts/run_daily.py --preview   # 预览要发的消息（不发送）
python menu/scripts/run_daily.py             # 真发
python common/scripts/check_setup.py         # 全链路自检
```

---

## 实测记录（2026-10-21 14:36）

```
✅ doc_client ping      token 来源：WorkBuddy 宿主票据（32 字符）
                        1 个子表 → t00i2h 智能表1
✅ fields               日期 / 星期 / 早餐 / 午餐 / 晚餐
✅ 写入 5 天数据         2026-10-19 ~ 2026-10-23
✅ 清理 9 行模板空行     剩余 5 行
✅ records 读回          日期、星期、三餐内容全部正确
✅ run_daily --preview  从腾讯文档读到 10/21 → 渲染「午餐 + 明日早餐」
```

整条链路已经通了，只差 QQ 群的 `group_openid` 和服务器部署。
