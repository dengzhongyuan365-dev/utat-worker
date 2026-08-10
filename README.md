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
- 如果你不想常驻进程，可以让外层调度器按任务调用 `utat worker --once`；
- 多机器通过 `node_id`、`capabilities`、`routing` 配置区分，节点名称由部署时自定义，不在代码里写死；
- 调度器只给允许的节点发任务，不依赖 SSH。

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
REPO_URL="https://github.com/dengzhongyuan365-dev/utat-worker.git" \
INSTALL_DIR="$HOME/WorkSpace/utat-worker" \
bash "$tmp/utat-worker-master/scripts/install-worker.sh"
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

中心调度器不是 Multica Agent，也不应该默认放在个人电脑上。它需要部署在一台稳定、长期在线、所有 worker 都能访问的公共控制节点或内网服务上。

中心节点需要运行：

```bash
utat server
utat orchestrator run
```

70、test 等机器只运行 worker，不运行中心调度器。当前代码不会假设具体机器名；中心节点和 worker 节点由部署配置决定。

如果暂时没有满足网络条件的公共控制节点，不能直接声称多机器调度已经部署完成；需要先提供一个所有 worker 可访问的中心地址，或把 API 部署到现有内网服务中。

## 多机器调度方式

中心调度器不 SSH 到执行机器。流程是：

```text
中心调度器写任务到 queue.db
→ 任务进入 dispatchable
→ 各机器本地 worker 主动请求 /api/v1/tasks/claim
→ 调度器根据 worker 的 node_id/capabilities 和任务 preferred_nodes 判断是否允许领取
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

如果一个任务没有配置 `preferred_nodes`，则任意具备该应用和任务类型能力的 worker 都可以领取。第一版默认全局并发为 1，所以不会多台机器同时跑多个 AT/UT。

## 当前 MVP 能力

- 本地 SQLite 队列
- Multica issue 读取/创建/评论/状态回写
- worker 主动领取任务
- 节点/应用路由控制
- UT 进程托管与日志采集
- 基础结果文件输出
- 全局单任务调度
