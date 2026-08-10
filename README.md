# utat-worker

本项目实现 Multica issue 驱动的本地 AT/UT 调度与执行。

## 组件

- `utat server`：中心 API 服务（领取任务、心跳、完成回写）
- `utat orchestrator`：扫描 Multica issue，写入本地队列，推进任务
- `utat worker`：节点任务执行器，默认前台轮询；也可 `--once` 单次执行
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

说明：

- **AT/UT 本体不是 service**，就是普通 CLI 程序；
- `systemd` 仅作为可选包装，不是主方案；
- 如果你不想常驻进程，可以让外层调度器按任务调用 `utat worker --once`；
- 多机器通过 `routing` 配置区分，例如 `local` 节点跑邮箱/文件管理器，`public-test` 节点跑其它应用；
- 调度器只给允许的节点发任务，不依赖 SSH。

## 安装到 GitHub 发布仓库后可用方式

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/dengzhongyuan365-dev/utat-worker/main/scripts/install-worker.sh)"
```

## 当前 MVP 能力

- 本地 SQLite 队列
- Multica issue 读取/创建/评论/状态回写
- worker 主动领取任务
- 节点/应用路由控制
- UT 进程托管与日志采集
- 基础结果文件输出
- 全局单任务调度
