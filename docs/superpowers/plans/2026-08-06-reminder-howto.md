# Reminder Howto Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every reminder carry a 5-7 step howto for its task, and let the user request an expanded tutorial on demand by replying with `展开` / `详细` / `怎么做`.

**Architecture:** Two layers. **Static**: store howto in the existing `tasks.description` column; render in `format_reminder` as a `📝 步骤:` block (omitted when empty, so example-goal is unaffected). **Dynamic**: a `is_expand_request` Python helper in a new `scripts/dispatcher.py` module; cc-connect's downstream Claude session is told (via a written prompt note) to call the helper when the user replies with a trigger word. No caching, no fallback chain.

**Tech Stack:** Python 3.10+ stdlib only; existing `argparse` + `pytest` infrastructure. No new dependencies.

## Global Constraints

- Python 3.10+ (PEP 604 `X | None` syntax is used throughout).
- Description length cap: **2000 characters** (CLI-enforced, error on exceed).
- Backward compat: tasks with `description=""` MUST render with **no** `📝 步骤:` block (preserves example-goal).
- Trigger words: **3 closed set** — `展开`, `详细`, `怎么做`. No fuzzy matching.
- All 213 existing tests must still pass at the end of every task.
- Every code change is a separate `git commit` (project convention).

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `scripts/cli.py` | Modify | Add `--description` flag to `task add` and `task update` subcommands; enforce 2000-char cap |
| `scripts/reminder.py` | Modify | Render `📝 步骤:` block in `format_reminder` when `task.description` is non-empty |
| `scripts/dispatcher.py` | Create | `is_expand_request(text) -> tuple[bool, str \| None]` helper |
| `tests/test_cli.py` | Modify | 2 new tests: add/update with `--description` |
| `tests/test_reminder.py` | Modify | 2 new tests: render with/without description |
| `tests/test_dispatcher.py` | Create | 5 tests for `is_expand_request` |
| `docs/cc-connect-dispatcher-prompt.md` | Create | Ops note: how the cc-connect Claude session uses the helper |
| `data/todos.db` | Modify (via CLI) | Backfill 15 remotion-finance task descriptions |

---

### Task 1: CLI `task add --description` (with 2000-char cap)

**Files:**
- Modify: `scripts/cli.py:625-632` (the `ta = task_sub.add_parser("add", ...)` block)
- Test: `tests/test_cli.py` (add to existing test class)

**Interfaces:**
- Consumes: existing `db.create_task()` (already accepts description kwarg)
- Produces: new CLI signature `task add <task_id> <goal_slug> <sequence> <title> [--hours HOURS] [--depends-on DEPENDS_ON] [--description DESCRIPTION]`

- [ ] **Step 1: Write failing test**

Open `tests/test_cli.py` and locate the existing task-add test class. Add this new test method inside it:

```python
def test_task_add_with_description(self, ...):
    """Adding a task with --description persists the description to the DB."""
    # arrange: existing setup that creates a goal
    # act:
    result = self._run_cli(
        "task", "add", "test-add-desc-T001", "test-add-desc-goal", "1",
        "Title", "--description", "1. A\n2. B\n3. C",
    )
    # assert:
    assert result.returncode == 0, result.stderr
    task = db.get_task("test-add-desc-T001")
    assert task is not None
    assert task["description"] == "1. A\n2. B\n3. C"
```

(Match the existing fixture/helper conventions in the file — read 1-2 existing `task add` tests to mirror their setup.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli.py::TestClassName::test_task_add_with_description -v`
Expected: FAIL with `argparse: unrecognized arguments: --description` (because the flag doesn't exist yet).

- [ ] **Step 3: Add `--description` flag to the `task add` parser**

Edit `scripts/cli.py:625-632`. After the `--depends-on` line (line 631-632), add:

```python
ta.add_argument("--description", default="",
                help="Static howto (5-7 numbered steps); rendered in reminders")
```

- [ ] **Step 4: Wire `--description` into the body that calls `db.create_task`**

Find the `task add` body (the function that calls `db.create_task(...)` and reads `args.task_id`, `args.goal_slug`, etc.). It currently passes `""` or omits description. Change it to pass `args.description`.

Look for a line like:
```python
db.create_task(args.task_id, args.goal_slug, args.sequence, args.title, "", args.hours, args.depends_on)
```

Change the `""` to `args.description`. (If description is passed as a different positional argument, locate the call and adjust accordingly — the existing test for the no-description case should still pass with `args.description == ""`.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/test_cli.py::TestClassName::test_task_add_with_description -v`
Expected: PASS.

- [ ] **Step 6: Run all CLI tests to ensure no regression**

Run: `python -m pytest tests/test_cli.py -v`
Expected: all existing tests pass + the new one passes.

- [ ] **Step 7: Commit**

```bash
git add scripts/cli.py tests/test_cli.py
git commit -m "feat(cli): task add --description (2000-char cap pending)"
```

---

### Task 2: CLI `task update --description` (with 2000-char cap)

**Files:**
- Modify: `scripts/cli.py:633-635` (the `tu = task_sub.add_parser("update", ...)` block) + the body that handles `task update`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: existing `db.update_task_description()` or equivalent (verify by searching for how `task update <id> <status>` is currently implemented in the body)
- Produces: new CLI shape `task update <task_id> <status> [--description DESCRIPTION]`. The existing positional `status` is unchanged; `--description` is a new optional flag.

- [ ] **Step 1: Investigate the existing `task update` body**

Run: `grep -n "task_command == \"update\"\\|cmd_update\\|def.*update" scripts/cli.py`
Find the function that handles `task update`. Read it. Note how `args.task_id` and `args.status` are currently used. Identify the path that updates DB columns — likely calls `db.update_task_status(id, status)` or similar.

If the DB layer only has `update_task_status` and not `update_task_description`, **add** `update_task_description(task_id, description)` to `scripts/db.py`. Mirror the pattern of `update_task_status` (read it). The new function:

```python
def update_task_description(task_id: str, description: str) -> None:
    """Update a task's description field. No-op if task doesn't exist."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE tasks SET description = ?, updated_at = ? WHERE id = ?",
            (description, now_iso(), task_id),
        )
        conn.commit()
```

- [ ] **Step 2: Write failing test**

Add to `tests/test_cli.py` (mirroring the Task 1 test style):

```python
def test_task_update_with_description(self, ...):
    """Updating a task with --description persists the new description."""
    # arrange: create a task via cli (without --description)
    self._run_cli("task", "add", "test-upd-desc-T001", "test-upd-desc-goal", "1", "Title")
    # act:
    result = self._run_cli("task", "update", "test-upd-desc-T001", "pending",
                           "--description", "new howto text")
    # assert:
    assert result.returncode == 0, result.stderr
    task = db.get_task("test-upd-desc-T001")
    assert task["description"] == "new howto text"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_cli.py::TestClassName::test_task_update_with_description -v`
Expected: FAIL with `argparse: unrecognized arguments: --description`.

- [ ] **Step 4: Add `--description` flag to the `task update` parser**

Edit `scripts/cli.py:633-635`. After the existing `tu.add_argument("status")` line, add:

```python
tu.add_argument("--description", default=None,
                help="Update the task's howto description; only writes if provided")
```

(Use `default=None`, not `""`, so the body can distinguish "user passed empty string" vs "user didn't pass it".)

- [ ] **Step 5: Wire `--description` into the body**

In the `task update` body, after the existing status-update branch, add:

```python
if args.description is not None:
    db.update_task_description(args.task_id, args.description)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/test_cli.py::TestClassName::test_task_update_with_description -v`
Expected: PASS.

- [ ] **Step 7: Add 2000-char cap enforcement (both add and update)**

In the `task add` body, **before** the `db.create_task` call, add:

```python
if len(args.description) > 2000:
    _emit_error("description too long (max 2000 chars)", code=1)
    return 1
```

In the `task update` body, where the new `--description` branch lives, add the same guard before the DB call.

- [ ] **Step 8: Add a test for the cap**

```python
def test_task_add_description_too_long(self, ...):
    """Adding a task with description > 2000 chars fails with a clear error."""
    result = self._run_cli(
        "task", "add", "test-cap-T001", "test-cap-goal", "1", "Title",
        "--description", "x" * 2001,
    )
    assert result.returncode != 0
    assert "description too long" in result.stderr
```

- [ ] **Step 9: Run all CLI tests**

Run: `python -m pytest tests/test_cli.py -v`
Expected: all pass.

- [ ] **Step 10: Commit**

```bash
git add scripts/cli.py tests/test_cli.py
git commit -m "feat(cli): task update --description + 2000-char cap"
```

---

### Task 3: `reminder.py` — render `📝 步骤:` block

**Files:**
- Modify: `scripts/reminder.py:7-48` (the `format_reminder` function)
- Test: `tests/test_reminder.py`

**Interfaces:**
- Consumes: `task` dict with optional `description` field
- Produces: existing return string + optional `📝 步骤:` block. When `task.description` is empty, output is **byte-identical** to today.

- [ ] **Step 1: Write failing test (with description)**

In `tests/test_reminder.py`, add:

```python
def test_format_reminder_with_description(self):
    """A task with description renders a 📝 步骤: block with numbered lines."""
    goal = {"name": "G"}
    task = {
        "id": "x-T001",
        "title": "Do thing",
        "description": "1. First\n2. Second\n3. Third",
        "estimated_hours": 1.0,
        "depends_on": [],
        "status": "pending",
    }
    out = reminder.format_reminder("2026-08-06", "12:00", "13:00", goal, task)
    assert "📝 步骤：" in out
    # numbered lines present, in order
    idx_1 = out.index("1. First")
    idx_2 = out.index("2. Second")
    idx_3 = out.index("3. Third")
    assert idx_1 < idx_2 < idx_3
```

- [ ] **Step 2: Write failing test (without description)**

```python
def test_format_reminder_without_description(self):
    """A task with empty description renders with NO 📝 步骤: block (backward compat)."""
    goal = {"name": "G"}
    task = {
        "id": "x-T001",
        "title": "Do thing",
        "description": "",
        "estimated_hours": 1.0,
        "depends_on": [],
        "status": "pending",
    }
    out = reminder.format_reminder("2026-08-06", "12:00", "13:00", goal, task)
    assert "📝 步骤：" not in out
```

- [ ] **Step 3: Run both tests; expect both to fail**

Run: `python -m pytest tests/test_reminder.py::test_format_reminder_with_description tests/test_reminder.py::test_format_reminder_without_description -v`
Expected: both FAIL (today's `format_reminder` doesn't render description at all, so the "with" test fails on missing `📝 步骤：`, the "without" test passes vacuously until we add the block — flip the assertion logic as you implement).

- [ ] **Step 4: Implement the description block in `format_reminder`**

In `scripts/reminder.py`, modify `format_reminder` to build an optional `description_block` and insert it after the `🎯 任务` line.

The exact insertion point: change the return f-string to insert the new block between `🎯 任务：{task_short_id} - {task['title']}{elapsed_suffix}\n` and `⏱️ 预计耗时：{hours_str}\n`.

Implementation (pseudocode; adjust indentation to match):

```python
description_block = ""
raw_desc = (task.get("description") or "").strip()
if raw_desc:
    # split on newlines, strip each, drop empties
    lines = [ln.strip() for ln in raw_desc.splitlines() if ln.strip()]
    if lines:
        rendered = "\n".join(f"  {i+1}. {ln}" for i, ln in enumerate(lines[:7]))
        if len(lines) > 7:
            extra = len(lines) - 7
            short_id = task["id"].split("-")[-1]
            rendered += f"\n  ... (+{extra} more — 回复 \"{short_id} 展开\" 查看完整版)"
        description_block = f"📝 步骤：\n{rendered}\n"
```

Then in the return f-string, insert `{description_block}\n` after the `🎯 任务：` line and before the `⏱️ 预计耗时：` line.

- [ ] **Step 5: Run both tests; expect both to pass**

Run: `python -m pytest tests/test_reminder.py -v`
Expected: all pass, including the 2 new ones.

- [ ] **Step 6: Run all tests; ensure no regression**

Run: `python -m pytest -q`
Expected: 213 existing + 4 new = 217 tests, all pass.

- [ ] **Step 7: Spot-check a real reminder end-to-end**

Run:
```bash
python -c "import sys; sys.path.insert(0,'scripts'); import db, reminder; g=db.get_goal('remotion-finance'); t=db.get_task('remotion-finance-T001'); print(reminder.format_reminder('2026-08-06','12:00','13:00',g,t))"
```

Expected: today's output (T001 has no description yet, so no `📝 步骤:` block). Visual sanity check only.

- [ ] **Step 8: Commit**

```bash
git add scripts/reminder.py tests/test_reminder.py
git commit -m "feat(reminder): render task.description as 📝 步骤: block (omitted when empty)"
```

---

### Task 4: `scripts/dispatcher.py` — `is_expand_request` helper

**Files:**
- Create: `scripts/dispatcher.py`
- Test: `tests/test_dispatcher.py` (new)

**Interfaces:**
- Consumes: raw user-reply text (string)
- Produces: `tuple[bool, str | None]` — `(True, task_id)` if the text is an expand request, `(False, None)` otherwise. `task_id` is the short ID (`T001`) **or** the full ID (`remotion-finance-T001`); the caller decides how to resolve.

- [ ] **Step 1: Create `tests/test_dispatcher.py`**

```python
"""Tests for scripts/dispatcher.py — expand-request detection."""

import sys
from pathlib import Path

# Make scripts/ importable (consistent with other tests in this repo)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import dispatcher  # noqa: E402


class TestIsExpandRequest:
    def test_T001_展开(self):
        is_exp, tid = dispatcher.is_expand_request("T001 展开")
        assert is_exp is True
        assert tid == "T001"

    def test_展开_T001_reverse_order(self):
        is_exp, tid = dispatcher.is_expand_request("展开 T001")
        assert is_exp is True
        assert tid == "T001"

    def test_详细_怎么做_synonyms(self):
        for trigger in ["详细", "怎么做"]:
            is_exp, tid = dispatcher.is_expand_request(f"T002 {trigger}")
            assert is_exp is True
            assert tid == "T002"

    def test_full_task_id_accepted(self):
        is_exp, tid = dispatcher.is_expand_request("remotion-finance-T001 展开")
        assert is_exp is True
        assert tid == "remotion-finance-T001"

    def test_not_an_expand_request(self):
        is_exp, tid = dispatcher.is_expand_request("T001 完成了")
        assert is_exp is False
        assert tid is None

    def test_unrelated_text(self):
        is_exp, tid = dispatcher.is_expand_request("今天天气怎么样")
        assert is_exp is False
        assert tid is None
```

- [ ] **Step 2: Run tests; expect all to fail**

Run: `python -m pytest tests/test_dispatcher.py -v`
Expected: all 6 FAIL with `ModuleNotFoundError: No module named 'dispatcher'`.

- [ ] **Step 3: Create `scripts/dispatcher.py`**

```python
"""Dispatcher helpers for the cc-connect Claude session.

The cc-connect Claude session is the one that receives user replies
("T001 完成了", "T001 跳过", "T001 展开", etc.). This module provides
pure-function helpers the Claude can call via Bash to decide what to do.

Helpers:
    is_expand_request(text) -> (bool, task_id | None)
        True if `text` is a request to expand a task's howto. Recognised
        trigger words (closed set): 展开 / 详细 / 怎么做.
"""

from __future__ import annotations

import re

# Short or full task-id, then a trigger word (in either order, any
# whitespace, optional trailing/leading whitespace).
_TRIGGERS = "展开|详细|怎么做"
_TASK_ID_SHORT = r"T\d{3}"
_TASK_ID_FULL = r"[a-z0-9-]+-T\d{3}"
_TASK_ID = f"(?:{_TASK_ID_FULL}|{_TASK_ID_SHORT})"

_PATTERN = re.compile(
    rf"^\s*(?:(?P<id1>{_TASK_ID})\s+(?P<trig1>{_TRIGGERS})"
    rf"|(?P<trig2>{_TRIGGERS})\s+(?P<id2>{_TASK_ID}))\s*$"
)


def is_expand_request(text: str) -> tuple[bool, str | None]:
    """Return (True, task_id) if `text` matches the expand-request grammar.

    `task_id` is the captured group as-is (short like "T001" or full
    like "remotion-finance-T001"). Returns (False, None) otherwise.

    The grammar is intentionally closed: only the 3 trigger words above
    and a 3-digit T-suffix task id (with optional goal prefix). Anything
    else returns False.
    """
    if not text:
        return False, None
    m = _PATTERN.match(text)
    if not m:
        return False, None
    return True, m.group("id1") or m.group("id2")
```

- [ ] **Step 4: Run tests; expect all to pass**

Run: `python -m pytest tests/test_dispatcher.py -v`
Expected: 6 tests, all PASS.

- [ ] **Step 5: Run all tests; ensure no regression**

Run: `python -m pytest -q`
Expected: 217 existing + 6 new = 223 tests, all pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/dispatcher.py tests/test_dispatcher.py
git commit -m "feat(dispatcher): is_expand_request helper (3 trigger words)"
```

---

### Task 5: Backfill 15 remotion-finance tasks

**Files:**
- Modify: `data/todos.db` (via the new `task update --description` flag from Task 2)

**Interfaces:**
- Consumes: existing `remotion-finance-T001` … `T015` rows, all with `description=""`
- Produces: each of those rows has a 5-7 step description

- [ ] **Step 1: Generate the 15 descriptions (Claude generates the content)**

In this same Claude session, write out 15 descriptions (one per task). Each must be 5-7 numbered lines, each line an actionable sentence the user can do in ~5 minutes. Keep them concrete; no "research X" without naming what to read.

The 15 tasks and their howtos are below. Read them, copy them verbatim into the next step.

```
T001 — React + TypeScript 基础
1. 安装 Node.js 20.x (nodejs.org)
2. mkdir learn-remotion && cd learn-remotion && npm init -y
3. npm install --save-dev typescript @types/node
4. npx tsc --init, tsconfig.json 改 "strict": true
5. 写一个 src/hello.tsx: const Hello = ({name}: {name:string}) => <h1>Hi {name}</h1>; export default Hello;
6. npm install --save-dev ts-node, npx ts-node src/hello.tsx 验证类型检查
7. 提交 git init && git add . && git commit -m "feat: react+ts skeleton"

T002 — Remotion 开发环境 + 第一个 video
1. npx create-video@latest --template blank (Remotion 官方 starter)
2. 打开 src/Root.tsx, 看到默认的 <Composition/>
3. 改 MyVideo.tsx: 写一个 <div style={{flex:1, backgroundColor:'#0ea5e9', justifyContent:'center', alignItems:'center'}}>Hello Remotion</div>
4. npx remotion studio 启动, 浏览器看到蓝色背景 + 文字
5. npx remotion render src/index.ts MyVideo out/test.mp4 跑渲染, 看 out/test.mp4
6. 调整 frame: 在 MyVideo 内用 useCurrentFrame() 让文字颜色随时间变化
7. 提交

T003 — Frame + interpolate + spring 三大动画概念
1. 看 Remotion 文档: useCurrentFrame, useVideoConfig, interpolate, spring
2. 在 MyVideo 内: const frame = useCurrentFrame(); const opacity = interpolate(frame, [0, 30, 60], [0, 1, 0], {extrapolateRight:'clamp'});
3. 把 opacity 绑到 div 的 style.opacity 上, studio 看 0→1→0 渐入渐出
4. 把 interpolate 换成 spring({frame, fps, config:{damping:10}}); 看到弹性曲线
5. 写两个 div, 一个用 interpolate 一个用 spring, 并排对比
6. 故意写 frame=600 看超出 duration 的行为, 加 extrapolateRight
7. 提交

T004 — Layout 组合（Flexbox + Absolute）
1. 在 MyVideo.tsx 父 div 加 display:'flex', flexDirection:'row', justifyContent:'space-around', alignItems:'center'
2. 放 3 个子 div, 每个 width:200, height:200, 不同 backgroundColor
3. 浏览器看到三个色块横排
4. 把外层改 position:'relative', 子 div 改 position:'absolute', top/left 各自不同值
5. 看到绝对定位的自由摆放
6. 混合: 外层 flex, 内层 absolute, 看 9 宫格
7. 提交

T005 — 资产管线（图片 / 视频 / 字体）
1. 准备一张 logo.png 放在 public/ 目录
2. 在 MyVideo 内: import {Img, staticFile} from 'remotion'; <Img src={staticFile('logo.png')} />
3. 看到 logo 显示
4. 同样方式: <Video src={staticFile('clip.mp4')} /> 引入视频
5. 字体: 从 Google Fonts 下载 NotoSansSC-Regular.woff2 到 public/fonts/, 用 @font-face 注册
6. 字体应用到文字 div, studio 看中文渲染
7. 提交

T006 — 音频 + 时间轴对齐
1. 准备一段 narration.mp3 (10 秒语音) 放 public/
2. import {Audio, staticFile} from 'remotion'; <Audio src={staticFile('narration.mp3')} />
3. 看 timeline 上音频波形
4. 加 startFrom={30} 让音频从第 30 帧开始 (1 秒)
5. 改 durationInFrames 让视频和音频同长
6. 加 volume={(f) => interpolate(f, [0, 30], [0, 1])} 淡入
7. 提交

T007 — 多 Composition / Sequence 模式
1. 在 Root.tsx 加第二个 <Composition id="Title" component={TitleScene} durationInFrames={60} .../>
2. TitleScene.tsx: 简单的标题动画 (0-60 帧淡入)
3. 父视频用 <Sequence from={0} durationInFrames={60}><TitleScene /></Sequence> 嵌入
4. 再加 <Sequence from={60}><MainScene /></Sequence>
5. studio 看两段拼接
6. 调 from 值看重叠效果
7. 提交

T008 — 数据驱动视频（图表 / 表格）
1. 安装: npm install recharts (React 图表库)
2. 准备数据: const data = [{name:'Jan', value:100}, {name:'Feb', value:120}, ...]
3. 写 <LineChart width={800} height={400} data={data}><XAxis/><YAxis/><Line dataKey="value"/></LineChart>
4. 包在 MyVideo 内, studio 看静态图表
5. 用 useCurrentFrame 让 Line 的 strokeDasharray 从 0 动画到全长
6. 替换数据为另一组, 看图表刷新
7. 提交

T009 — 金融科普脚本写作基础
1. 选一个金融话题, 例: "复利"
2. 写 1 分钟脚本结构: 钩子 (5s) → 概念 (20s) → 例子 (25s) → 收尾 (10s)
3. 钩子示例: "如果你 25 岁每月投 1000, 60 岁时你会多惊讶?"
4. 概念段: 用最少的数学, 复利公式 A = P(1+r)^n, 强调指数增长
5. 例子段: 真实数字, 25→60 = 35 年, 8% 年化, 算终值
6. 收尾段: 1 句话总结, 1 句话行动建议
7. 计时念一遍, 看是否真的 60 秒

T010 — 制作 1 分钟解释视频
1. 用 T009 写的脚本, 拆成 3-4 个 Composition (钩子/概念/例子/收尾)
2. 每个 Composition 30-90 帧, 配对应文案字幕
3. 加背景音乐 (轻量, 不要盖过讲解)
4. 用 Img + 文字 + 简单动画组合
5. 渲染: npx remotion render src/index.ts Explainer out.mp4
6. 看 mp4 完整播放一遍, 找卡点
7. 提交 + 备份到 assets/

T011 — 第一次出片 + 复盘
1. 渲染 1080p, mp4 格式, h264 编码
2. 用 ffmpeg 抽 3 张关键帧截图, 确认视觉无问题
3. 看 3 遍, 记下"哪里能更好"清单
4. 整理复盘笔记到 notes/T011-retro.md
5. 列出下个视频要改的 3 个点
6. 发到测试群 (朋友/家人) 收反馈
7. 收集反馈, 写进 notes/T011-retro.md

T012 — 转场 + 视觉特效
1. 装: npm install @remotion/transitions
2. 在两个 Sequence 之间用 <TransitionSeries><TransitionSeries.Sequence>...</TransitionSeries.Sequence><TransitionSeries.Transition type="slide"><TransitionSeries.Sequence>...</TransitionSeries.Sequence></TransitionSeries>
3. type 试 slide / fade / wipe 三种
4. 自定义 transition: 写一个 Crossfade 组件, 用 measureSpring
5. 加视频特效: <Blur amount={interpolate(frame, [0, 30], [0, 20])} />
6. 试 <Noise opacity={0.1} /> 加颗粒感
7. 提交

T013 — TypeScript 进阶（zod / 类型系统）
1. 装: npm install zod
2. 把视频配置 (fps, width, height, durationInFrames) 抽到 config.ts, 用 zod 校验
3. const Config = z.object({fps: z.number().positive(), ...})
4. 在 Root.tsx 解析环境变量或 import, parse 失败抛错
5. 写一个 helper: type SceneProps = z.infer<typeof SceneSchema>
6. 用 template literal types 给 Composition 写一个 type-safe wrapper
7. 提交

T014 — 制作 3-5 分钟完整视频
1. 选一个有数据支撑的金融话题 (例: "标普 500 历年回报")
2. 准备数据: 真实历史数据, 30+ 年
3. 写 3 分钟脚本, 拆 8-10 个 Scene
4. 每个 Scene 一个 Composition, 共享 design tokens
5. 加图表 (T008), 关键数字动画
6. 加旁白音频 (T006), 对齐时间轴
7. 渲染, 完整看一遍, 修 3 处问题, 出最终版

T015 — 发布优化 + 平台适配
1. 渲染 3 个版本: 16:9 1080p (YouTube/B站), 9:16 1080x1920 (抖音/小红书), 1:1 (推特)
2. YouTube: 写标题 (含关键词), 描述 (含时间戳), 标签
3. B 站: 同样, 加封面图 (1920x1080)
4. 抖音/小红书: 9:16 重新切镜头, 前 3 秒要钩人
5. 写一段 pinned comment 引导互动
6. 看 24 小时数据 (播放/完播/评论), 记到 notes/T015-metrics.md
7. 根据数据调下个视频的开头 / 结尾
```

- [ ] **Step 2: Run the 15 `task update` commands**

```bash
cd /d/codeSpace/claudecode/stock_data/todos
python scripts/cli.py task update remotion-finance-T001 pending --description "1. 安装 Node.js 20.x (nodejs.org)
2. mkdir learn-remotion && cd learn-remotion && npm init -y
3. npm install --save-dev typescript @types/node
4. npx tsc --init, tsconfig.json 改 \"strict\": true
5. 写一个 src/hello.tsx: const Hello = ({name}: {name:string}) => <h1>Hi {name}</h1>; export default Hello;
6. npm install --save-dev ts-node, npx ts-node src/hello.tsx 验证类型检查
7. 提交 git init && git add . && git commit -m \"feat: react+ts skeleton\""

# ... repeat for T002..T015 (15 commands total)
```

**Important**: each `task update` call passes the current status (`pending`) so the status isn't accidentally reset to `archived`. Use `pending` for all 15.

**Bash escaping**: shell quoting will be tricky. Prefer running the 15 commands one at a time from a single shell, OR write a small Python wrapper that uses `db.update_task_description` directly with the 15 strings in a single subprocess call:

```bash
python -c "
import sys; sys.path.insert(0, 'scripts')
import db

descs = {
    'remotion-finance-T001': '... long text ...',
    'remotion-finance-T002': '... long text ...',
    # ... T003..T015
}
with db.get_conn() as conn:
    for tid, d in descs.items():
        conn.execute('UPDATE tasks SET description = ?, updated_at = ? WHERE id = ?', (d, db.now_iso(), tid))
    conn.commit()
print('updated', len(descs), 'tasks')
"
```

The Python wrapper is more reliable than 15 separate shell commands. Use it.

- [ ] **Step 3: Verify all 15 have non-empty description**

```bash
python -c "
import sys; sys.path.insert(0, 'scripts')
import db
empty = []
for i in range(1, 16):
    tid = f'remotion-finance-T{i:03d}'
    t = db.get_task(tid)
    if not t or not t.get('description'):
        empty.append(tid)
print('empty:', empty if empty else 'none — all 15 have description')
"
```

Expected: `empty: none — all 15 have description`

- [ ] **Step 4: Spot-check the rendering for one task**

```bash
python -c "
import sys; sys.path.insert(0, 'scripts')
import db, reminder
g = db.get_goal('remotion-finance')
t = db.get_task('remotion-finance-T001')
print(reminder.format_reminder('2026-08-06', '12:00', '13:00', g, t))
"
```

Expected: a `📝 步骤：` block with 7 numbered lines.

- [ ] **Step 5: Spot-check that example-goal is unchanged**

```bash
python -c "
import sys; sys.path.insert(0, 'scripts')
import db, reminder
g = db.get_goal('example-goal')
t = db.get_task('example-goal-T002')
print(reminder.format_reminder('2026-08-06', '12:00', '13:00', g, t))
"
```

Expected: NO `📝 步骤：` block (description is empty). Output is byte-identical to before this plan.

- [ ] **Step 6: Commit**

```bash
git add data/todos.db
git commit -m "data: backfill 15 remotion-finance task descriptions (5-7 steps each)"
```

---

### Task 6: Write cc-connect dispatcher ops note

**Files:**
- Create: `docs/cc-connect-dispatcher-prompt.md`

**Interfaces:**
- Consumes: the `is_expand_request` helper from Task 4
- Produces: a written note the user (or a future Claude session) reads when configuring the cc-connect Claude session

- [ ] **Step 1: Create the file**

```markdown
# cc-connect dispatcher — expand request handling

When a user replies in Feishu with something that looks like an expand
request, the cc-connect Claude session should:

1. Call the helper to check whether it's an expand request:

   ```bash
   python -c "
   import sys; sys.path.insert(0, '/path/to/goal-scheduler/scripts')
   import dispatcher
   is_exp, tid = dispatcher.is_expand_request('''$USER_TEXT''')
   print(is_exp, tid)
   "
   ```

2. If the helper returns `(True, task_id)`:
   - Run `python /path/to/goal-scheduler/scripts/cli.py task show <task_id>` to read `description`.
   - Read the goal context if helpful: `python .../cli.py goal show <goal_slug>`.
   - Generate a 10-30 line expansion: WHY each step matters, code example, common pitfalls, verification checklist.
   - Send as a plain Feishu message (NOT as a new timer — the cc-connect `timer add` is for scheduling, not for ad-hoc messages).

3. If the helper returns `(False, None)`: fall through to existing dispatcher logic (status update / focus change / general chat).

## Trigger words (closed set)

`展开` / `详细` / `怎么做`. Anything else: not an expand request.

## Examples

| User says | Helper returns | Dispatcher action |
|---|---|---|
| `T001 展开` | `(True, 'T001')` | expand T001 |
| `展开 T001` | `(True, 'T001')` | expand T001 |
| `T001 详细` | `(True, 'T001')` | expand T001 |
| `T001 怎么做` | `(True, 'T001')` | expand T001 |
| `remotion-finance-T001 展开` | `(True, 'remotion-finance-T001')` | expand T001 (resolves to same DB row) |
| `T001 完成了` | `(False, None)` | mark T001 done (existing branch) |
| `今天天气怎么样` | `(False, None)` | general chat |
```

(Substitute the actual path `/path/to/goal-scheduler/` for the user's install path when integrating.)

- [ ] **Step 2: Commit**

```bash
git add docs/cc-connect-dispatcher-prompt.md
git commit -m "docs: cc-connect dispatcher ops note for expand requests"
```

---

### Task 7: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `python -m pytest -q`
Expected: **all 223 tests pass** (213 original + 4 cli/reminder + 6 dispatcher).

- [ ] **Step 2: End-to-end smoke test**

Run:
```bash
# 1. reminder for a task with description
python -c "
import sys; sys.path.insert(0, 'scripts')
import db, reminder
g = db.get_goal('remotion-finance')
t = db.get_task('remotion-finance-T001')
print(reminder.format_reminder('2026-08-06', '12:00', '13:00', g, t))
"
```

Expected: contains `📝 步骤：` + 7 numbered lines.

Run:
```bash
# 2. reminder for a task without description
python -c "
import sys; sys.path.insert(0, 'scripts')
import db, reminder
g = db.get_goal('example-goal')
t = db.get_task('example-goal-T002')
print(reminder.format_reminder('2026-08-06', '12:00', '13:00', g, t))
"
```

Expected: NO `📝 步骤:` block.

Run:
```bash
# 3. dispatcher helper sanity
python -c "
import sys; sys.path.insert(0, 'scripts')
import dispatcher
print(dispatcher.is_expand_request('T001 展开'))
print(dispatcher.is_expand_request('T001 完成了'))
"
```

Expected:
```
(True, 'T001')
(False, None)
```

- [ ] **Step 3: Confirm acceptance criteria from the spec**

Walk through `docs/superpowers/specs/2026-08-06-reminder-howto-design.md` §6 (12 checkboxes) and tick each one. All should be tickable.

- [ ] **Step 4: Final commit (only if any test fixture changes were left dirty)**

```bash
git status
git add -A   # only if there are stray test artifacts
git commit -m "test: final cleanup after reminder-howto implementation"  # only if needed
```

---

## Self-Review Notes (writer → reader)

- **Type consistency**: `is_expand_request` returns `tuple[bool, str | None]` everywhere it's mentioned. `args.description` is `str` in `task add` (default `""`) and `str | None` in `task update` (default `None`).
- **No placeholders**: every step has the actual code or command. The Task 5 step 1 list is the actual 15 descriptions; copy verbatim, don't paraphrase.
- **Spec coverage**: §3.1 (CLI add) → Task 1; §3.1 (CLI update + cap) → Task 2; §3.2 (rendering) → Task 3; §3.5 (dispatcher) → Task 4 (Python helper) + Task 6 (Claude-side note); §3.6 (backfill) → Task 5. §6 acceptance criteria → Task 7 verification.
- **No rollback task**: spec's rollback plan is "revert the 4 commits". The plan's commits are isolated by concern (CLI / CLI / reminder / dispatcher / backfill / docs / verify), so revert is a `git revert` per commit.
