# utat-worker

本项目实现 Multica issue 驱动的本地 AT/UT 调度与执行。

## 组件

- `utat server`：中心 API 服务（领取任务、心跳、完成回写）
- `utat orchestrator`：扫描 Multica issue，写入本地队列，推进任务
- `utat worker`：节点常驻 worker，主动领取任务并执行
- `scripts/install-worker.sh`：一键安装脚本

## 快速开始

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
utat init --workspace-id <workspace-id>
utat server
```

另开终端启动：

```bash
utat orchestrator run
utat worker run
```

## 安装到 GitHub 发布仓库后可用方式

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/dengzhongyuan365-dev/utat-worker/main/scripts/install-worker.sh)"
```

## 当前 MVP 能力

- 本地 SQLite 队列
- Multica issue 读取/创建/评论/状态回写
- worker 主动领取任务
- UT 进程托管与日志采集
- 基础结果文件输出
- 全局单任务调度

