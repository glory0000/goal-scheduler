# Goal Scheduler

> 一个把目标拆解成任务、塞进空闲时段、再由 AI 助理按时提醒你做的个人日程系统。

一个 Claude 驱动的个人目标调度器。把"我想做 X"拆成可执行的任务，按你当天的空闲时段排好，通过 Feishu 在每个时段准时提醒你。日常对话就能改目标、改任务、改焦点；后端自动同步状态。

## What it does

🗣️  **告诉 Claude 一个目标**
    "新目标：学西班牙语，3 个月达到 B1"

🤖  **它拆任务、塞空闲时段**
    12 个有序任务、当天的 18:00 / 21:00 时段自动排好

⏰  **到点 Feishu 提醒**
    你回一句"完成"，下一时段自动接上
    任何一步断了，00:05 cron 第二天兜底补回

## Features

- **目标拆解** — 一个目标拆成 N 个有序任务，任务之间可声明依赖（依赖未完成就不排）。
- **空闲时段感知** — 按 `config/schedule.json` 区分工作日 / 周末，每天给你真实可用的时间段。
- **动态调度** — 任务完成、焦点切换、目标暂停都会重新排。截至当天剩余时段，不浪费。
- **焦点优先** — 一天只做一个目标（`focus set`），调度器只盯那个目标的任务。
- **状态枚举** — Goal: `active / paused / completed / archived`；Task: `pending / in_progress / done / skipped / archived`（archived = 软删除，可恢复）。
- **自动同步** — 任何 CRUD 改完，goals/index.md 自动重渲染；不需要手动跑 sync。
- **WEB 控制台** — Flask 仪表盘 (`dashboard/`) 实时看目标、任务、今日时段、统计数据。
- **崩溃自愈** — 每天 00:05 一次性 cron 重建当天剩下的提醒链；任何一个 timer 漏了，第二天自动补。
- **schema 迁移** — `migrations/NNN_*.sql` 前向 only，runner 自带事务回滚。
- **统一 CLI** — `python scripts/cli.py {status,today,goal,task,focus,rebuild-timers,sync-md}`，所有命令都支持 `--json`。

## Architecture

数据流一句话：**用户/Feishu → cc-connect → Claude(脑) → CLI(手) → SQLite+文件系统(记忆)**。

```
┌─────────────────────────────────────────────────────────────────┐
│                  cc-connect 桥接层                              │
│  · cron 0 5 * * *  每天重建倒计时链                            │
│  · timer 下一个空闲时段自动 fire                                │
│  · 把 Feishu/Lark/Telegram 消息转给 Claude                       │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                       Claude (调度大脑)                         │
│  · 听用户说 "新目标 X" → brainstorm 拆任务 → 调 CLI 写入 DB    │
│  · 听 timer 唤醒 → 调 reminder.py 拼消息 → 回复给 cc-connect    │
│  · 听用户说 "完成了" → 调 CLI 改状态 → 触发 autosync            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│             scripts/cli.py  统一入口 (argparse)                 │
│     status  today  goal {add,list,show,update,delete,restore}   │
│     task  {add,list,show,update,delete,restore}                 │
│     focus {set,clear}  rebuild-timers  sync-md                  │
│  · --json 输出单行 JSON                                         │
│  · 错误 → stderr, 退出码 0/1/2/3                              │
│  · 改完自动 _autosync_index_md()                                │
└─────────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────┬───────────────┬────────────────┐
        ▼             ▼               ▼                ▼
   ┌─────────┐  ┌──────────┐   ┌──────────┐   ┌───────────┐
   │ db.py   │  │scheduler │   │ sync_md  │   │cc_timers  │
   │ SQLite  │  │  .py     │   │  .py     │   │  .py      │
   │ CRUD    │  │ 选任务   │   │ MD 渲染  │   │ cc-connect│
   │         │  │ 塞时段   │   │          │   │ API 包装  │
   └─────────┘  └──────────┘   └──────────┘   └───────────┘
        │             │              │               │
        ▼             ▼              ▼               ▼
   ┌─────────┐  ┌──────────┐   ┌──────────┐   ┌───────────┐
   │todos.db │  │schedule  │   │goals/    │   │cc-connect │
   │SQLite   │  │  .json   │   │  index.md│   │  timers   │
   │goals,   │  │空闲时段  │   │+ 每个目标│   │(由 cc-mgmnt│
   │tasks,   │  │定义      │   │  goal.md │   │  维护)    │
   │settings │  │          │   │          │   │           │
   └─────────┘  └──────────┘   └──────────┘   └───────────┘
```

**核心模块职责（一个文件 = 一个职责）:**

| 文件 | 职责 |
|---|---|
| `scripts/db.py` | SQLite CRUD：`goals` / `tasks` / `settings` 表 + `archive_*` / `restore_*` 软删除 |
| `scripts/migrate.py` | 向前 only 迁移 runner：`init` 初始化 + `schema_version`，`upgrade` 按序号 apply |
| `scripts/scheduler.py` | 纯函数：给定 (focus, date, time)，算出 (task, slot) 配对 |
| `scripts/reminder.py` | 纯函数：把 (goal, task, slot) 渲染成 Feishu 消息文本（中文模板） |
| `scripts/format_utils.py` | 工具：elapsed time 格式化、Chinese number words 等 |
| `scripts/cli_output.py` | CLI 专用的人/机两种输出格式 |
| `scripts/sync_md.py` | 渲染 `goals/index.md`；纯函数 + 文件 I/O 分两层 |
| `scripts/cc_timers.py` | 包装 cc-connect CLI；生产用 subprocess，测试用文件 backend |
| `scripts/cli.py` | argparse 总入口；mod dispatch；--json；autosync 钩子 |
| `dashboard/app.py` | Flask 只读控制台 (5 个路由) |

## Project structure

```
todos/
├── README.md                  ← 本文件
├── config/
│   └── schedule.json          ← 你的空闲时段定义（工作日 / 周末）
├── data/
│   ├── todos.db               ← SQLite：goals / tasks / settings
│   └── schema.sql             ← 初始 schema（被 db.py init 用）
├── migrations/
│   └── 002_add_started_at.sql ← 后续 schema 变更按 NNN_xxx.sql 顺序 apply
├── goals/                     ← 每个目标 = 一个子目录 + goal.md
│   ├── index.md               ← 自动渲染的索引（按状态分组 + 完成率）
│   └── example-goal/
│       └── goal.md
├── scripts/
│   ├── db.py                  ← SQLite CRUD（被 cli.py / scheduler.py 调）
│   ├── scheduler.py           ← 选任务、塞时段（纯函数）
│   ├── reminder.py            ← 拼消息（纯函数）
│   ├── cli.py                 ← 统一 CLI 入口
│   ├── sync_md.py             ← 渲染 goals/index.md
│   ├── cc_timers.py           ← cc-connect timer API 包装
│   ├── migrate.py             ← DB migration runner
│   ├── cli_output.py          ← CLI 输出格式
│   └── format_utils.py        ← 字符串/时间工具
├── dashboard/                 ← Flask 只读 WEB 控制台
│   ├── app.py
│   ├── templates/
│   ├── static/
│   └── README.md
├── tests/                     ← 213 个 pytest，覆盖所有层
└── docs/superpowers/          ← 历史设计/spec/plan
    ├── specs/                 ← 设计文档
    └── plans/                 ← 实施计划
```

## Installation

### 依赖

- **Python 3.10+** — 代码用了 PEP 604 union (`X | None`) 和 `match`/`case`，3.10 以下跑不动。
- **Git** — DB / `goals/*.md` / `goals/index.md` 每次写都 `git commit`，克隆即可。
- **Flask** — 看板唯一 Python 第三方依赖（见 `dashboard/requirements.txt`）。
- **cc-connect** — 外部 CLI 工具（**不在 PyPI**，独立安装）。负责把飞书消息转给 Claude、托管所有 timer / cron。本项目假定它已装好且 `cc-connect --version` 能跑通。

### 步骤

```bash
# 1. 克隆
git clone https://github.com/glory0000/goal-scheduler.git
cd goal-scheduler

# 2. 装 Python 依赖（清华源；境外可去掉 -i）
python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r dashboard/requirements.txt

# 3.（可选）装测试依赖
python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements-dev.txt

# 4. 装 cc-connect，按其官方文档把 app_id / app_secret 配好、连上飞书
#    验证：cc-connect --version   应能打印版本号
#    验证：cc-connect cron list   应能列出已有 cron（首次为空）

# 5. 跑测试看一切正常
python -m pytest -q
```

### 跟飞书机器人的绑定（哪条链路卡在哪）

- **不在本项目**。`scripts/cc_timers.py` 只包装 `cc-connect timer list/add/del` 三个子命令，**不持有 app_id、不路由 chat_id、不区分多个 bot**。
- **配置在 cc-connect 那一层**。Feishu app 的 app_id / app_secret / 事件订阅 URL 全部由 cc-connect 自己管（不在本仓库里）。本项目是 cc-connect 的**下游**——它看到的是 cc-connect 转过来的消息文本和它返回给 cc-connect 的回复文本。
- **一对一假设**。项目按"一个 cc-connect 实例 = 一个飞书 bot = 一个用户"设计：所有状态集中在一个 `data/todos.db`、一份 `config/schedule.json`、一个 `settings.today_focus`。
- **想绑多个 bot**？可以在同一台机器跑多个 cc-connect 进程（每个绑一个 bot），但**所有 cc-connect 仍然读写同一个本地 DB**——同一个提醒会从多个 bot 重复发出去，没有用户路由。要真正支持多用户，必须先给项目加 `user_id` 概念（`goals` / `tasks` / `settings` / `schedule` 全部加命名空间），那是另一个 spec，不在本仓库当前作用域。

完成安装后，下一步走 Quick start。

## Quick start

```bash
# 1. 初始化 DB（一个命令就够了，不需要 migrate.py init）
python scripts/db.py init

# 2. 启动 dashboard（可选）
cd dashboard && python app.py    # 浏览器打开 http://localhost:5000

# 3. 通过 Feishu 告诉 Claude 你想做什么
# 例: "新目标：学西班牙语，3 个月达到 B1"
# Claude 会 brainstorm → 调 CLI 写 DB → sync index.md

# 4. 设定今日焦点
python scripts/cli.py focus set learn-spanish

# 5. 重建今天的提醒链 (cron 也会自动跑)
python scripts/cli.py rebuild-timers
```

完整日常命令见下方 CLI 部分。

## Common commands

```bash
# Dump current state
bash scripts/dump_state.sh

# Simulate a reminder at a given time
bash scripts/simulate_reminder.sh "2026-08-04 21:00"

# Simulate session crash to test fallback cron
bash scripts/break_session.sh
```

## CLI

`scripts/cli.py` 是 Claude 和用户读写状态的统一入口。人读默认走可读文本；加 `--json` 输出单行 JSON。所有错误 → stderr。

```bash
# View state
python scripts/cli.py status
python scripts/cli.py today

# Add a goal / task
python scripts/cli.py goal add learn-spanish "学西班牙语" --description "B1 三个月"
python scripts/cli.py task add learn-spanish-T013 learn-spanish 13 "背诵 100 词" --hours 1.0

# Update progress
python scripts/cli.py task update learn-spanish-T013 in_progress
python scripts/cli.py task update learn-spanish-T013 done

# Change today's focus
python scripts/cli.py focus set learn-spanish
python scripts/cli.py focus clear

# Rebuild today's reminder timers
python scripts/cli.py rebuild-timers
# Output: Rebuilt timers for 2026-08-04:
#           added   2  (18:00 evening → T002, 21:00 night → T003)
#           removed 0
#           kept    0

python scripts/cli.py rebuild-timers --json
# Output: {"date": "2026-08-04", "added": [...], ...}

# Regenerate goals/index.md from current DB state (manual)
python scripts/cli.py sync-md
# Output: Synced 3 goals to goals/index.md (active=1, paused=1, completed=1)
#           - +example-goal      (进行中 50%)
#           - ~paused-goal       (已暂停 33%)
#           - ~old-completed     (已完成 100%)

python scripts/cli.py sync-md --json
# Output: {"path": "goals/index.md", "synced_count": 3, ...}

# Auto-fires after goal add / task add / task update — no need to run manually.
```

```bash
# List goals (default hides archived)
python scripts/cli.py goal list
python scripts/cli.py goal list --all
python scripts/cli.py goal list --status archived
python scripts/cli.py goal list --json

# Show one goal
python scripts/cli.py goal show example-goal
python scripts/cli.py goal show example-goal --json

# Change a goal's status (use 'paused' / 'completed'; not 'archived')
python scripts/cli.py goal update example-goal --status paused
python scripts/cli.py goal update example-goal --status completed --json

# Soft-delete (archive) and restore
python scripts/cli.py goal delete example-goal
python scripts/cli.py goal restore example-goal

# Same surface for tasks
python scripts/cli.py task list
python scripts/cli.py task list --goal example-goal
python scripts/cli.py task list --status done --all
python scripts/cli.py task show example-goal-T001
python scripts/cli.py task delete example-goal-T001
python scripts/cli.py task restore example-goal-T001
```

Exit codes: `0` success, `1` input error, `2` database not initialized,
`3` resource not found.

## Migrations

Schema 变更向前 only，按 `migrations/NNN_xxx.sql` 顺序 apply：

```bash
python scripts/migrate.py init      # 已由 db.py init 完成，不必再跑
python scripts/migrate.py upgrade   # apply 任何挂起的 migrations/
```

加新 migration：创建 `migrations/NNN_description.sql`，NNN 是下一个三位版本号。Runner 按文件名字典序 apply；任一文件失败则回滚，`schema_version` 停在之前值。

## Fallback cron

依赖唯一一个 cc-connect cron 在每天 00:05 重建 timer 链。验证：

```bash
cc-connect cron list
```

期望看到 1 个 job `5 0 * * *` (`00:05` 每天)。如果丢了，重建：

```bash
cc-connect cron add --cron "5 0 * * *" --prompt "Daily fallback for goal-scheduler. Read data/todos.db and config/schedule.json. For each remaining free slot today, ensure a cc-connect timer exists pointing at a pending task. Cancel stale timers (pointing at done/skipped tasks or past slots). Commit any DB or file changes. If everything is in order, no commit needed." --desc "Goal scheduler: daily reminder chain rebuild"
```

## Shadow period (1-2 周)

正式信任调度器前，并行跑 1-2 周人肉计划，每天对比：

1. 早上跑 `bash scripts/dump_state.sh` dump 当前状态。
2. 对比 Claude 排的计划 vs 你的手排计划。
3. 记偏差（Claude 漏了 X / 多排了 Y）。
4. 调 `config/schedule.json` 或 `scheduler.py` 规则。
5. 连续 7+ 天匹配后，删 example-goal 上线。

## When to engage Claude

任何下面这些都可以直接告诉 Claude（中文 / Feishu）：

- "新目标：<描述>" — 开新目标（Claude brainstorm）。
- "Txxx 完成了" / "Txxx 进度 50%" — 改任务状态。
- "今日重点 = <slug>" — 改焦点。
- "跳过 <时段>" / "暂停 <slug>" — 跳时段 / 暂停目标。
- "<目标> 增加任务：<描述>" — 加任务。
- "删除 Txxx" / "改 Txxx 为先做 Tyyy" — 改任务。

## Tests

```bash
python -m pytest -q          # 213 tests, 全部通过
```

覆盖每一层：db CRUD、scheduler 选任务、reminder 消息、CLI 所有子命令 + `--json`、sync_md 渲染、cc_timers reconcile、migrate 迁移、dashboard 路由。

## License

MIT
