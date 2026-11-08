# 校园机器人

QQ 机器人数量可配置（清单在 `config.yaml` 的 `bots` 段），目前三个：

| 机器人 | 负责 | 触发方式 |
|---|---|---|
| **食谱机器人** `menu` | 每天发当天食谱；@ 它查本周完整食谱 | 服务器每天 09:50 + 群里 @ |
| **通知机器人** `notice` | 其他事项（通用文本通道） | 手动，或给它配 `schedule` |
| **操前小读机器人** `reader` | 生成并回传小读 docx | 群里 @ 它（常驻监听） |

每周放一张截图，服务器每天 9:50 自动把当天食谱发到 QQ 群。

```
你的动作（每周 1 次，30 秒）
   截图 → 丢进 menu/inbox/
        ↓
WorkBuddy 自动化（每周日 20:00，或你随时手动触发）
   读图 → 理解哪天吃什么 → 写入腾讯文档
        ↓
腾讯文档「每日食谱」（唯一数据源，手机上随时可改）
        ↓
阿里云服务器 39.96.63.120（每天 09:50）
   食谱机器人：读当天那行 → 发「午餐 + 晚餐 + 明日早餐」到 QQ 群
   周五额外预告「周日晚餐」（寄宿制周日下午返校）
   当天没午/晚餐（周末、假期）→ 完全不发；周日只发午餐
```

时间轴（寄宿制一周）：

| 哪天 | 发什么 |
|---|---|
| 周一~周四 | 当天午餐 + 晚餐 + 次日早餐 |
| **周五** | 当天午餐 + 「今日没有晚餐（下午离校）」+ **周日晚餐预告** |
| 周六 | 静默（除非调休上课） |
| **周日** | 静默（晚餐已在周五发过） |

### 群里 @ 查本周食谱

@食谱机器人（内容随意）→ 回一条「本周食谱」，从周一到周日逐天列出
已录入的早餐 / 午餐 / 晚餐；每道菜前面带 emoji（主食、荤菜、蔬菜、汤、水果…），
没安排的日期显示「无餐次安排」。走被动回复，不占主动消息额度。

本周表格里还没有数据时，回「本周表格里还没有食谱数据」。
QQ 开放平台需已订阅群聊消息事件（与 reader 相同的 `1 << 25`），否则收不到 @。

本地预览（只打印，不连群）：

```bash
.venv\Scripts\python.exe menu\scripts\serve_menu.py --preview
```

---

## 机器人（可配置多个）

机器人清单在 `config.yaml` 的 `bots` 段，想加几个就加几个，代码不用改：

| 字段 | 作用 |
|---|---|
| `label` | 显示名，只用于日志和提示 |
| `handler` | `menu` = 每日食谱；`text` = 发 inbox 里的稿子；`reader` = 操前小读 |
| `schedule` | 服务器定时，`HH:MM`；留空 = 手动 |
| `listen` | `true` = 常驻监听群 @（menu 用它回本周食谱）；可与 `schedule` 并存 |
| `inbox` | text 机器人取稿目录，默认 `common/inbox/<键名>` |

凭据放 `common/.env`，按 `BOT_<键名大写>_APPID / _SECRET / _GROUP_OPENID` 命名；
config 里不写 `*_env` 时自动按这个约定读取。

当前三个机器人：

| 机器人 | handler | 触发 | 干什么 |
|---|---|---|---|
| 食谱 `menu` | `menu` | 每天 09:50 | 读腾讯文档，发当天食谱 |
| 通知 `notice` | `text` | 手动 | 发 `common/inbox/notice/` 里最早的稿子 |
| 小读 `reader` | `reader` | 群里 @ 它 | 现场生成一期 docx，作为群文件回传 |

⚠️ 即使多个机器人在同一个群，**group_openid 也各自不同**（openid 按机器人区分），
必须分别抓一次：

```bash
.venv\Scripts\python.exe common\scripts\capture_group_openid.py --bot menu
.venv\Scripts\python.exe common\scripts\capture_group_openid.py --bot notice
```

抓完后去群里 @ 对应的那个机器人 说句话即可。

### 新增一个机器人

1. 在 `common/config.yaml` 的 `bots` 下加一段（照注释里的 `help` 示例抄）。
2. 在 `common/.env` 填 `BOT_HELP_APPID` / `BOT_HELP_SECRET` / `BOT_HELP_GROUP_OPENID`。
3. 跑 `common/scripts/capture_group_openid.py --bot help`，然后去群里 @ 它。
4. 服务器上重跑 `sudo bash common/deploy/server/install.sh`，会按 config 自动生成
   `campus-bot-help.service`（`schedule` 非空时再加一个 `.timer`）。

文本机器人是通用发送通道，业务规则可以后续再填：

```bash
# 直接发一段文字
.venv\Scripts\python.exe common\scripts\run_text.py --bot notice --text "明天 15:00 家长会"

# 发该机器人 inbox 里最早的一份 .txt / .md 稿子
.venv\Scripts\python.exe common\scripts\run_text.py --bot notice

# 只看要发什么
.venv\Scripts\python.exe common\scripts\run_text.py --bot notice --preview
```

只配了一个 text 机器人时，`--bot` 可以省略。想定时发，就在 config 里给它填
`schedule`，重跑 install.sh 即可，不需要手写 systemd 文件。

### 操前小读机器人（群里 @ 触发）

它和上面两个不同：不是定时脚本，而是**常驻进程**。
群里 @ 它 → 现场生成一期 docx → 作为群文件回传（被动回复，不占主动消息额度）。

@ 后面要带年级或册名，一条消息只出一份：

| @ 后面说 | 出刊范围 |
|---|---|
| `高一` | 必修一 + 必修二 |
| `高二` | 选择性必修一 + 选择性必修二 |
| `高三` | 选择性必修三 + 高三复习 |
| `必修一` / `必修二` / `选择性必修一` / `选择性必修二` / `选择性必修三` / `高三复习` | 只出这一册 |
| `随机` | 全库轮转 |
| 不带内容，或说了不认识的词 | 回一条用法提示，不出刊 |

也认简写：`必修1`、`必修2`、`选必1`、`选必2`、`选必3`。
同一时刻只处理一份，第二个人会收到「上一份还在生成，稍等一下…」。

QQ 开放平台侧要一次性做好：

1. 创建机器人，把 AppID / AppSecret 填进 `common/.env` 的 `BOT_READER_*`
2. 开通群聊能力，并把机器人拉进目标群
3. 开发设置 → IP 白名单，加上服务器公网 IP `39.96.63.120`
4. 确认已订阅群聊消息事件（本项目的 intents 是 `1 << 25`）

本地三步验证：

```bash
# 1. 抓 group_openid（看到「等待群消息」后去群里 @ 它）
.venv\Scripts\python.exe common\scripts\capture_group_openid.py --bot reader

# 2. 常驻试跑，保持窗口不要关
.venv\Scripts\python.exe reader\scripts\serve_reader.py --bot reader

# 3. 去群里 @ 操前小读机器人（内容随意），应收到一份 docx
```

确认本地能回传后，再部署到服务器：

```bash
sudo bash common/deploy/server/install.sh
sudo systemctl status campus-bot-reader.service
tail -f /opt/campus-bot/logs/reader.log
```

- 出刊逻辑与手动出刊一致（见 `run_reader.py`），出刊记录照常写 `reader/data/reader_history.json`
- 生成期间再次 @，会先回一句「上一份还在生成，稍等一下…」
- 进程异常退出后 systemd 10 秒内自动拉起
- ⚠️ `install.sh` 会同步本地 `common/.env` 覆盖服务器上的，所以本地也要填好 `BOT_READER_*`

---

## 关于「定时任务不能发内容给它」

你的观察是对的，这是定时的本质限制：

**自动化在后台无人值守地跑，你没有对话窗口可交互，所以没法在它运行时"扔"内容给它。**

但反过来它**能读你提前放好的文件**。所以正确的做法是：

| 你想做的 | 正确方式 |
|---|---|
| 让它看食谱图 | 图片存进 `menu/inbox/` 文件夹 |
| 让它读公众号文字 | 正文复制到 `menu/inbox/本周.txt` |
| 当场处理（不等定时） | 直接在 WorkBuddy 对话里把图发给我 / 说「读一下 inbox」 |

**`menu/inbox/` 就是你食谱自动化的"收件箱"** —— 你往里放，它在定时点去读。这是唯一可行的异步交接方式。

---

## 每周你要做什么

**唯一动作：把学校的食谱截图存进 `menu\inbox\`**

然后就没了。周日 20:00 自动化会自己处理。

想立刻处理（不等周日）就在对话里说一句「读一下 inbox 里的食谱」。

处理完的图片会自动移到 `menu\inbox\processed\<时间戳>\`，不会重复处理。

支持的格式：
- 图片 `.png .jpg .jpeg .webp .gif .bmp`
- 文本 `.txt .md`（公众号正文复制进来即可）

---

## 脚本

| 脚本 | 干什么 | 谁用 |
|---|---|---|
| `menu/scripts/save_menu.py` | **JSON → 腾讯文档**，含硬校验 | WorkBuddy 读完图后调用 |
| `menu/scripts/run_daily.py` | **腾讯文档 → QQ 群**（`handler: menu` 的机器人） | 服务器按 `schedule` |
| `menu/scripts/serve_menu.py` | **群里 @ → 回传本周完整食谱**（含 emoji，常驻） | 服务器 systemd |
| `common/scripts/run_text.py` | **文本 → QQ 群**（任意 `handler: text` 的机器人） | 手动，或按 `schedule` |
| `common/scripts/check_setup.py` | 全链路自检，告诉你缺什么 | 出问题时 |
| `reader/scripts/serve_reader.py` | **群里 @ → 生成 docx → 回传群文件**（常驻） | 服务器 systemd |
| `reader/scripts/run_reader.py` | **专题库 → 操前小读 docx** | 手动 |
| `reader/scripts/split_topics.py` | 按教材标题整理知识点并编号（知识点1、知识点2…） | 调整知识库时 |

公共模块在 `common/py/`：`bots.py`（多机器人清单 / 凭据 / token / 按机器人发送）、
`doc_client.py`（腾讯文档 MCP 客户端）、`send_qq.py`（QQ 发送与消息排版）、
`qq_listener.py`（群 @ 事件监听：鉴权 / 心跳 / 重连 / 去重）、
`common.py`（配置、路径、日志）。

写入的最小 JSON 长这样，`weekday`、周起止日期都会自动补：

```json
{"days": [
  {"date": "2026-10-26", "meals": {"午餐": ["大盘鸡拌面","白灼青菜","鸡蛋面汤","馒头"], "晚餐": ["木须肉片","干锅花菜","大米粥","馒头"]}},
  {"date": "2026-10-27", "meals": {"早餐": ["蒸肉卷","玉米","辣土豆丝"], "午餐": ["清炖排骨","家常豆腐","紫菜蛋花汤","米饭"]}}
]}
```

⚠️ **每餐第一位必须是「主角」**（大荤行那道菜；大荤本身是拌面/水饺/炒饭时就是它）。
发送端靠位置识别主打，靠 `馒头/花卷/蒸鸡蛋` 白名单折叠每日标配。细则见
`menu/docs/DATA_FORMAT.md` 的「排列顺序 = 语义契约」。

改完排版想先看效果：

```bash
.venv/Scripts/python.exe menu/scripts/preview_style.py
```

`save_menu.py` 会拦下这些问题：日期格式错、日期重复、餐次名不规范、菜名含顿号。

---

## 服务器在跑什么

```bash
ssh -i "C:/文档/桌面常用临时文件/Interactive-Webpage-for-Geography-Teaching/aliyun2.pem" \
    root@39.96.63.120
```

- 程序在 `/opt/campus-bot`
- 每个机器人一份 `campus-bot-<键名>.service`，由 `common/deploy/server/render_units.py`
  按 `common/config.yaml` 生成；`schedule` 非空的再配一个同名 `.timer`
- `listen: true` 的机器人（menu）额外常驻：`campus-bot-menu.service` 监听群 @，
  定时推送单独用 `campus-bot-menu-push.service`（由 `campus-bot-menu.timer` 触发）
- 日志 `/opt/campus-bot/logs/<键名>.log`（如 `menu.log`、`notice.log`）

```bash
# 看今天要发什么（不发）
sudo -u campusbot /opt/campus-bot/.venv/bin/python /opt/campus-bot/menu/scripts/run_daily.py --bot menu --preview

# 手动补发食谱
sudo -u campusbot /opt/campus-bot/.venv/bin/python /opt/campus-bot/menu/scripts/run_daily.py --bot menu

# 手动发通知
sudo -u campusbot /opt/campus-bot/.venv/bin/python /opt/campus-bot/common/scripts/run_text.py --bot notice --text "测试通知"

# 看所有机器人的定时器
systemctl list-timers 'campus-bot-*'

# 看日志
tail -20 /opt/campus-bot/logs/menu.log
```

> ⚠️ 服务器上那份是**独立的一份代码**，跟这个文件夹不同步。
> 只有你改了 `run_daily.py` / `run_text.py` / `serve_menu.py` / `serve_reader.py` /
> `run_reader.py` / `send_qq.py` / `bots.py` / `doc_client.py` 或增删了机器人，
> 才需要重新上传并重跑 `install.sh`。
> 日常改食谱数据**完全不用碰服务器**（数据在腾讯文档里）。
>
> 从旧版 `school-menu-bot` 升级时，重新跑一次 `install.sh`：
> 它会自动停掉并移除旧定时器，避免两个服务同时发送。

---

## 腾讯文档

[每日食谱](https://docs.qq.com/smartsheet/DTmh1SWZIZGZGc05w)（5 列：日期 / 星期 / 早餐 / 午餐 / 晚餐）

**你可以直接在手机上改表格**，第二天就按你改的发送。这是这个设计最大的好处。

数据格式细节见 [menu/docs/DATA_FORMAT.md](menu/docs/DATA_FORMAT.md)。
腾讯文档 API 的踩坑记录见 [menu/docs/PLAN_TENCENT_DOC.md](menu/docs/PLAN_TENCENT_DOC.md)。

---

## 出问题

先跑自检：

```bash
.venv\Scripts\python.exe common\scripts\check_setup.py
```

它按 6 个环节逐项检查，最后直接告诉你「下一步做什么」。

| 现象 | 原因 |
|---|---|
| 群没收到食谱 | 看服务器 `logs/menu.log`；或当天表格里没有午餐/晚餐（周末正常静默） |
| 某个机器人不说话 | 检查它对应的 `BOT_<键名>_APPID` / `_SECRET` / `_GROUP_OPENID` 是否填了 |
| 新加的机器人不发送 | 确认 config 里有 `handler`、`.env` 有凭据、重跑过 `install.sh` |
| 通知发不出去 | 通知机器人要先建好并拉进群，再跑 `common/scripts/capture_group_openid.py --bot notice` |
| 图片解析不了 | 当前模型不支持读图，换多模态模型；或手工往表格填 5 行 |
| 日期错了 | 打开腾讯文档直接改，改完第二天生效 |
| 上传说 403 | 服务器 IP 不在 QQ 机器人白名单（目前未启用白名单，一般不会遇到） |

---

## 文件说明

按「公用 / 食谱 / 操前小读」分三个顶层目录：

```
校园机器人/
├── common/                     公用
│   ├── config.yaml             机器人清单与业务配置
│   ├── .env                    密钥（勿外传）
│   ├── requirements.txt
│   ├── py/                     公共模块
│   │   ├── common.py           配置 / 路径 / 日志
│   │   ├── bots.py             机器人清单 / 凭据 / token / 发送
│   │   ├── send_qq.py          QQ 发送与排版
│   │   ├── qq_listener.py      群 @ 事件监听（鉴权/心跳/重连/去重）
│   │   └── doc_client.py       腾讯文档 MCP 客户端
│   ├── scripts/
│   │   ├── run_text.py         ★ 文本 → QQ（handler: text 的机器人）
│   │   ├── check_setup.py      自检
│   │   └── capture_group_openid.py  一次性：抓各机器人群 openid
│   ├── inbox/notice/           通知稿（.txt/.md）
│   ├── deploy/server/
│   │   ├── install.sh          部署入口
│   │   └── render_units.py     按 config 生成 systemd service/timer
│   └── docs/                   通用参考文档
├── menu/                       食谱机器人
│   ├── scripts/
│   │   ├── save_menu.py        ★ JSON → 腾讯文档
│   │   ├── run_daily.py        ★ 腾讯文档 → QQ（handler: menu）
│   │   ├── serve_menu.py       ★ 常驻：群里 @ → 回传本周完整食谱
│   │   └── preview_style.py    本地预览消息样式
│   ├── inbox/                  ★ 每周把截图丢这里
│   │   └── processed/          已处理的截图与 JSON，按时间戳归档
│   ├── data/                   运行状态（自动生成）
│   ├── docs/                   DATA_FORMAT / PLAN_TENCENT_DOC
│   └── tests/test_resume.py    返校晚餐回归测试
└── reader/                     操前小读
    ├── scripts/
    │   ├── serve_reader.py     ★ 常驻：群里 @ → docx 回传
    │   ├── run_reader.py       ★ 抽专题 → docx
    │   ├── topics.py           专题库加载 / 抽样 / 出刊历史
    │   ├── docx_builder.py     docx 排版
    │   ├── import_book.py      正文 → topics.json 草稿
    │   ├── extract_docx.py     docx → 文本 + 图片
    │   ├── split_topics.py     按教材标题整理知识点并编号
    │   └── check_topics.py     专题库体检
    ├── knowledge/              知识点库（books / assets / source / work / outline）
    ├── example/                历史样张
    ├── out/                    生成的小读 docx
    └── data/                   出刊历史（自动生成）
```
