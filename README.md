# utat-worker

本项目实现 Multica issue 驱动的本地 AT/UT 调度与执行。

## 组件

- `utat server`：旧版中心 API 原型（当前不作为多机器部署前提）
- `utat orchestrator`：扫描 Multica issue，写入本地队列，推进任务
- `utat worker`：旧版 HTTP 队列 worker（兼容保留）
- `utat-node`：实验版本地异步执行器，负责入队、本机串行执行、结果回调
- `scripts/install-worker.sh`：一键安装脚本

## 快速开始

实验节点安装仍使用一条命令：

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/dengzhongyuan365-dev/utat-worker/master/scripts/install-worker.sh)"
```

初始化节点：

```bash
utat-node init --node-id local
```

提交任务（Agent 调用，命令快速返回）：

```bash
utat-node submit \
  --issue-id <issue-id> \
  --task-type AT \
  --app-name deepin-mail \
  --node local
```

查看本机队列：

```bash
utat-node status
```

`submit` 会自动启动本机后台 worker；也可以手动启动：

```bash
utat-node worker run --node-id local
```

旧版 `utat server/orchestrator/worker` 仅为兼容保留，不作为本实验流程的主路径。

说明：

实验版本地执行器使用同一个程序的两个模式：

```bash
utat-node submit --issue-id <id> --task-type AT --app-name <app> --node local
utat-node worker run --node-id local
utat-node status
```

`submit` 只写入本机队列并立即返回；`worker run` 在后台消费队列并执行真实 AT/UT。两者是同一代码包的不同命令和不同进程。

- **AT/UT 本体不是 service**，就是普通 CLI 程序；
- `submit` 可以自动启动后台 worker，不需要 Agent 前台等待；
- 每台机器使用自己的 `node_id`、本地 SQLite 和文件锁；
- 同一机器最多执行一个 AT/UT，不同机器可以并发；
- 任务完成后写 Multica metadata 并 rerun 对应 AT/UT Agent。

## 安装方式

如果仓库是 public，可以直接：

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/dengzhongyuan365-dev/utat-worker/master/scripts/install-worker.sh)"
```

如果目标机器访问 `raw.githubusercontent.com` 出现 GnuTLS/TLS 错误，可以改用 GitHub codeload（不经过 raw 域名）：

```bash
tmp="$(mktemp -d)"
curl --http1.1 --retry 5 --retry-all-errors -fL \
  https://codeload.github.com/dengzhongyuan365-dev/utat-worker/tar.gz/refs/heads/master \
  -o "$tmp/utat-worker.tar.gz"
tar -xzf "$tmp/utat-worker.tar.gz" -C "$tmp"
mkdir -p "$HOME/WorkSpace/utat-worker-archive"
cp -a "$tmp/utat-worker-master/." "$HOME/WorkSpace/utat-worker-archive/"
SKIP_REPO_FETCH=1 \
INSTALL_DIR="$HOME/WorkSpace/utat-worker-archive" \
bash "$HOME/WorkSpace/utat-worker-archive/scripts/install-worker.sh"
```

如果仓库是 private，匿名 raw URL 会返回 404。需要二选一：

1. 将仓库改为 public；
2. 使用 GitHub token 拉取：

```bash
export GITHUB_TOKEN=<your-token>
bash -c "$(curl -fsSL -H "Authorization: Bearer ${GITHUB_TOKEN}" https://raw.githubusercontent.com/dengzhongyuan365-dev/utat-worker/master/scripts/install-worker.sh)"
```

安装脚本内部默认用 `git clone https://github.com/dengzhongyuan365-dev/utat-worker.git`。private 仓库场景下，目标机器也需要具备 GitHub 认证能力。

## 中心调度器部署位置

中心调度器不是 Multica Agent，也不应该默认放在个人电脑上。最终形态应部署在一台稳定、长期在线、能够访问 Multica 的公共控制节点或内网运行环境上。

中心节点需要运行：

```bash
utat server
utat orchestrator run
```

70、test 等机器只运行 worker，不运行中心调度器。当前代码不会假设具体机器名；中心节点和 worker 节点由部署配置决定。

如果暂时没有满足条件的公共控制节点，不能直接声称多机器调度已经部署完成；需要先明确调度器运行位置。后续 Multica-only 模式下，worker 主动轮询 Multica，不要求公共机器开放入站端口。

## 多机器调度方式

中心调度器不 SSH 到执行机器。当前 HTTP 原型流程是：

```text
中心调度器写任务到 queue.db
→ 任务进入 dispatchable
→ 各机器本地 worker 主动请求 /api/v1/tasks/claim
→ 队列根据 worker 的 node_id/capabilities 和任务 preferred_nodes 判断是否允许领取
→ 领取成功的 worker 本机执行 AT/UT
→ worker 上传结果到 Multica，并回写中心调度器
```

示例：两台机器分别配置为 `node-a` 和 `node-b`。

中心调度器 `routing`：

```json
{
  "routing": {
    "deepin-mail": {"preferred_nodes": ["node-a"]},
    "dde-file-manager": {"preferred_nodes": ["node-a"]},
    "deepin-editor": {"preferred_nodes": ["node-b"]},
    "deepin-reader": {"preferred_nodes": ["node-b"]}
  }
}
```

`node-a` worker：

```json
{
  "worker": {
    "node_id": "node-a",
    "server_url": "http://<orchestrator-host>:8765",
    "capabilities": {
      "apps": ["deepin-mail", "dde-file-manager"],
      "task_types": ["AT", "UT"]
    }
  }
}
```

`node-b` worker：

```json
{
  "worker": {
    "node_id": "node-b",
    "server_url": "http://<orchestrator-host>:8765",
    "capabilities": {
      "apps": ["deepin-editor", "deepin-reader"],
      "task_types": ["AT", "UT"]
    }
  }
}
```

如果一个任务没有配置 `preferred_nodes`，则任意具备该应用和任务类型能力的 worker 都可以领取。

当前并发约束不是“全局只能跑一个”：

- 每个 `node_id` 同时最多运行一个 AT/UT；
- 不同 `node_id` 可以同时各运行一个 AT/UT；
- 同一应用的 UT 必须等待该应用 AT 结束后才能进入可执行状态；
- 不同应用之间不互相阻塞，因此可以被分配到不同机器并行；
- 同一个节点即使启动多个 worker 进程，队列的原子领取也会拒绝第二个任务。

例如：

```text
local：deepin-mail AT（运行中）
node-70：dde-file-manager AT（运行中）
node-build：deepin-editor UT（运行中）
```

以上是允许的；但 `local` 上不会同时出现第二个 AT/UT。

## 当前 MVP 能力

- 本地 SQLite 队列
- Multica issue 读取/创建/评论/状态回写
- worker 主动领取任务
- 节点/应用路由控制
- UT 进程托管与日志采集
- 基础结果文件输出
- 每节点单任务执行
- 多节点并发执行
- result_ready 后通过 issue rerun 唤醒对应 AT/UT Agent
- 同一应用 AT→UT 顺序约束
