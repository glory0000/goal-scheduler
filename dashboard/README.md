# Todo Scheduler Web Dashboard

只读个人任务看板。数据来自 `data/todos.db`，时间段来自 `config/schedule.json`；所有任务修改仍通过飞书中的 Claude 完成。

## 安装

在项目根目录执行：

```bash
python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r dashboard/requirements.txt
```

## 启动

```bash
python dashboard/app.py
```

服务监听 `0.0.0.0:5000`。本机访问 `http://127.0.0.1:5000`；同一局域网设备访问 `http://<本机局域网 IP>:5000`。

页面不会自动刷新。任务数据更新后按 F5 获取最新内容。

## 页面

- `/`：目标列表与进度
- `/goal/<slug>`：目标详情与任务
- `/today`：今日空闲时段与安排
- `/stats`：全局统计与最近完成
- `/health`：返回 `ok`

## 限制

该服务使用 Flask 开发服务器，仅适用于可信局域网中的个人使用。它不提供认证、HTTPS、编辑、自动刷新或公网部署能力。

若启动时提示数据库不存在，请先在项目根目录初始化或恢复 `data/todos.db`。若端口 5000 已占用，请先停止占用该端口的进程，再重新启动看板。
