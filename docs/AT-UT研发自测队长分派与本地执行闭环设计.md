# AT/UT 研发自测队长分派与本地执行闭环设计

## 1. 目的

本文档定义新的 AT/UT 研发自测流程。目标是把长时间测试从 Multica Agent 会话中解耦出来，同时保留队长、AT Agent、UT Agent 和邮件 Agent 的身份化反馈能力。

本方案解决以下问题：

- Agent 不再长时间阻塞等待 AT/UT 终端执行；
- 不设置固定的中心 HTTP 调度服务；
- 不要求公共测试机器开放入站端口；
- 不要求调度器 SSH 到测试机器；
- 同一台机器同一时间只运行一个 AT/UT；
- 不同机器可以并行运行不同应用；
- 同一应用严格先 AT、后 UT；
- AT/UT 结果由对应 Agent 身份评论；
- 每次有结果都唤醒队长并更新总入口进度；
- 全部任务完成后只在总入口生成最终报表并发送邮件。

---

## 2. 核心原则

### 2.1 Agent 不承担长时间执行

队长、AT Agent、UT Agent 的每次运行都必须是短任务：

```text
读取 issue 和 metadata
→ 做一次调度或提交动作
→ 写一条状态评论
→ 结束本次运行
```

Agent 不允许：

- 前台等待几个小时的 AT/UT；
- 启动后台命令后声称会持续轮询，但没有持久化执行程序；
- 通过提示词承担完整状态机；
- 通过固定总超时杀死仍在运行的测试。

### 2.2 长任务由本地执行程序托管

具体测试由目标机器上的 `utat-node` 完成。Agent 只调用提交命令，命令快速返回；本地程序负责后台执行、日志采集、结果落盘和结果回写。

### 2.3 不设置固定中心调度机器

本方案不部署 `utat server`，不依赖 HTTP 端口，也不要求某台机器作为所有节点都能访问的中心服务器。

队长负责短调度，目标机器上的本地程序负责本机执行队列。Multica issue 和 metadata 是跨机器的控制面和状态存储。

### 2.4 身份和执行分离

| 角色 | 负责内容 | 用户可见评论身份 |
|---|---|---|
| 队长 Agent | 创建结构、分派任务、遍历状态、推进流程、最终汇总 | 队长 Agent |
| AT Agent | 提交 AT、读取 AT 结果、发布 AT 结果 | AT Agent |
| UT Agent | 提交 UT、读取 UT 结果、发布 UT 结果 | UT Agent |
| 本地 `utat-node` | 执行测试、采集日志、回写结果、触发 Agent | 不发布最终结果评论 |
| 邮件 Agent | 读取总报表、发送邮件 | 邮件 Agent |

普通 `multica issue comment add` 的作者是当前 CLI 登录用户，不能伪装成指定 Agent。因此本地程序不直接发布最终 AT/UT/总汇总评论；它通过 metadata 和 `issue rerun` 唤醒对应 Agent，由 Agent 以自身身份评论。

---

## 3. Multica issue 层级

总入口 issue 标题格式：

```text
AT-UT-YYYYMMDDHHMM
```

标准层级：

```text
总入口：AT-UT-202608101800
  ├── 应用：AT-UT-202608101800-deepin-mail
  │     ├── AT：AT-202608101800-deepin-mail
  │     └── UT：UT-202608101800-deepin-mail
  └── 应用：AT-UT-202608101800-dde-file-manager
        ├── AT：AT-202608101800-dde-file-manager
        └── UT：UT-202608101800-dde-file-manager
```

职责边界：

- AT issue：只记录 AT 过程、AT 日志、AT 结果；
- UT issue：只记录 UT 过程、UT 日志、UT 结果；
- 应用 issue：汇总本应用 AT/UT；
- 总入口 issue：汇总全部应用、生成总 HTML、触发邮件；
- 子 issue 不发送最终邮件。

---

## 4. 节点路由和 Agent 配置

节点名称使用逻辑名称，不写死物理机器 IP。例如：

```json
{
  "routes": {
    "deepin-mail": {
      "node": "local",
      "at_agent_id": "<AT_LOCAL_AGENT_ID>",
      "ut_agent_id": "<UT_LOCAL_AGENT_ID>"
    },
    "dde-file-manager": {
      "node": "local",
      "at_agent_id": "<AT_LOCAL_AGENT_ID>",
      "ut_agent_id": "<UT_LOCAL_AGENT_ID>"
    },
    "deepin-editor": {
      "node": "public-test",
      "at_agent_id": "<AT_PUBLIC_TEST_AGENT_ID>",
      "ut_agent_id": "<UT_PUBLIC_TEST_AGENT_ID>"
    }
  }
}
```

每个节点的 AT/UT Agent 运行时必须和本地执行程序在同一台机器，或者能够访问同一个产物目录。这样 Agent 才能读取本地生成的日志、XML、HTML 和覆盖率文件并作为附件发布。

例如，70 机器不在提示词中写死为“70”，而是部署成逻辑节点：

```text
node_id = local
AT Agent = AT-研发自测-local
UT Agent = UT-研发自测-local
```

---

## 5. Metadata 状态模型

### 5.1 AT/UT issue metadata

每个执行 issue 至少维护：

```text
utat.task_type = AT / UT
utat.app_name = deepin-mail
utat.root_issue_id = <root_issue_id>
utat.app_issue_id = <app_issue_id>
utat.node = local
utat.role_agent_id = <agent_id>

utat.task_state = planned
```

任务状态：

```text
planned       已创建，等待队长分派
assigned      已分配给目标 Agent/节点
submitted     Agent 已提交本地执行队列
queued        本地队列等待
running       本地程序正在执行
result_ready  测试完成，结果和产物已写回
finalizing    对应 Agent 正在读取并评论结果
finalized     对应 Agent 已完成最终评论
failed        本地执行已结束但结果失败（测试不通过、编译失败、依赖失败、安装失败、脚本失败等）；这不是流程阻塞，必须进入汇总
blocked       未能启动执行的阻塞条件（缺少必需账号/环境变量、payload 无效、issue 不存在、人工确认缺失等）
```

### 5.2 结果 metadata

本地程序完成后写入：

```text
utat.result_json = <结构化结果>
utat.artifact_manifest = <产物清单>
utat.artifact_dir = <本机产物目录>
utat.exit_code = <退出码>
utat.started_at = <时间>
utat.finished_at = <时间>
utat.final_comment_done = true/false
```

### 5.3 节点状态 metadata

总入口 issue 或专用节点注册 issue 维护：

```text
utat.node.local.active_task = <task_issue_id 或空>
utat.node.local.state = idle / busy / unknown
utat.node.local.last_result = <task_issue_id>
```

`active_task` 只表示该机器当前正在执行的 AT/UT，不包括短暂的结果汇总 Agent。

---

## 6. 队长 Agent 的调度流程

队长每次被唤醒后都必须重新遍历完整 issue 树，不能只根据上一条评论做局部判断。

### 6.1 初始化

队长首次处理总入口时：

1. 读取总入口描述和应用列表；
2. 创建或修正应用 issue；
3. 创建 AT/UT 子 issue；
4. 写入应用、节点和任务 metadata；
5. 根据节点路由找到可执行的 AT 任务；
6. 每个空闲节点最多分派一个任务；
7. 结束本次运行，不等待测试。

### 6.2 AT 可分派条件

```text
AT task_state = planned
目标 node.active_task 为空
同应用没有其他 assigned/running/result_ready/finalizing 任务
```

### 6.3 UT 可分派条件

```text
UT task_state = planned
同应用 AT task_state = finalized
目标 node.active_task 为空
同应用没有其他 assigned/running/result_ready/finalizing 任务
```

### 6.4 分派动作

队长不直接调用普通评论模拟 Agent 身份，而是：

```bash
multica issue assign <task_issue_id> --to-id <role_agent_id>
multica issue rerun <task_issue_id>
```

同时写入：

```text
task_state = assigned
node.<node_id>.active_task = <task_issue_id>
role_agent_id = <AT/UT agent_id>
```

### 6.5 每次结果后的动作

AT/UT Agent 完成最终评论后，必须唤醒队长：

```bash
multica issue assign <root_issue_id> --to-id <CAPTAIN_AGENT_ID>
multica issue rerun <root_issue_id>
```

队长被唤醒后：

1. 遍历总入口、应用 issue、AT/UT issue；
2. 更新总入口进度评论；
3. 释放已完成任务对应的节点；
4. 判断同应用 UT 是否 ready；
5. 判断其他应用是否可以分派；
6. 给每个空闲节点最多分派一个任务；
7. 如果全部完成，进入最终汇总流程。

---

## 7. AT/UT Agent 流程

### 7.1 首次唤醒：提交任务

当任务状态为 `assigned` 或 `planned` 时，AT/UT Agent：

1. 读取 issue metadata；
2. 确认目标 node 和任务参数；
3. 调用本地 `utat-node submit`；
4. 确认提交成功；
5. 写 `submitted`；
6. 在自己的 issue 中评论已提交；
7. 立即结束。

不得等待测试进程结束。

示例：

```bash
utat-node submit \
  --issue-id <task_issue_id> \
  --root-issue-id <root_issue_id> \
  --app-issue-id <app_issue_id> \
  --task-type AT \
  --app-name deepin-mail \
  --node local
```

### 7.2 第二次唤醒：发布结果

本地程序完成后写 `result_ready` 并重新唤醒原 AT/UT Agent。Agent：

1. 读取 `result_json`；
2. 读取 `artifact_manifest`；
3. 检查日志、报告和退出码；
4. 在当前 AT/UT issue 中发布最终结果；
5. 上传对应产物；
6. 标记 `finalized`；
7. 修改当前 AT/UT issue 为 `done`：只要本地任务已经产生 result_ready，无论结果是 passed/failed、编译失败、依赖失败、安装失败或测试失败，都必须视为“本任务执行完成”，不得改为 blocked，不得阻断后续汇总；
8. 仅当任务根本无法启动（必需账号/环境变量缺失、payload 无效、issue 已不存在、人工确认缺失）时才允许标记 `blocked`；
9. 唤醒队长。

最终评论作者是 AT/UT Agent，不是运行机器的 CLI 登录用户。

### 7.3 执行失败不是流程阻塞

AT/UT Agent 和队长必须区分“测试结果失败”和“流程阻塞”：

- 测试用例不通过、通过率不是 100%、编译失败、依赖安装失败、打包失败、安装失败、测试脚本返回非 0，均属于“执行已完成但结果失败”；
- 这类任务必须在对应 AT/UT issue 中写清楚失败原因、上传日志/产物、设置 metadata.task_state=finalized，并把 issue 状态置为 done；
- 队长必须继续检查其他 AT/UT 子 issue，直到所有子 issue 均 finalized 或 blocked；
- 最终报表必须汇总 passed/failed/blocked 全部结果，并且无论是否存在失败项，都要触发报表邮件 Agent；
- 只有任务未能启动或无法取得必要输入时才是 blocked，例如缺少必需账号环境变量、payload 无效、目标 issue 不存在、需要人工确认。

---

## 8. 本地 `utat-node` 程序

### 8.1 submit 命令

```bash
utat-node submit \
  --issue-id <issue_id> \
  --task-type AT|UT \
  --app-name <app> \
  --repo <repo> \
  --branch <branch> \
  --node <node_id>
```

要求：

- 立即返回；
- 不等待编译或测试结束；
- 写入本机 SQLite 队列；
- 如果本机执行循环未启动，则启动它；
- 返回 `task_id`、队列位置和当前状态。

### 8.2 本机执行循环

```bash
utat-node worker run --node-id local
```

执行循环：

```text
读取本机队列
→ 获取 node.lock
→ 取一个任务
→ 写 running
→ 执行 AT/UT
→ 采集日志和产物
→ 写 result_ready
→ 触发当前 issue rerun
→ 释放 node.lock
```

### 8.3 同机串行

本地程序必须同时使用：

```text
本机 SQLite 队列
本机文件锁 ~/.utat-node/locks/node.lock
Multica node.active_task
```

即使队长误分派两个任务到同一 node，第二个任务也只能保持 `queued`，不能并发执行。

### 8.4 多机并发

不同 node 使用不同本机锁：

```text
local       -> ~/.utat-node/locks/node.lock
public-test -> ~/.utat-node/locks/node.lock
build       -> ~/.utat-node/locks/node.lock
```

由于锁位于不同机器，以下场景允许同时发生：

```text
local       执行 deepin-mail AT
public-test 执行 deepin-editor AT
```

同一机器不允许：

```text
local 同时执行 deepin-mail AT 和 dde-file-manager AT
```

---

## 9. 结果回调链路

以邮箱 AT 为例：

```text
队长分派邮箱 AT
  ↓
AT-研发自测-local 被唤醒
  ↓
调用 utat-node submit，立即返回
  ↓
本地程序后台执行邮箱 AT
  ↓
本地程序写 result_ready、结果和产物清单
  ↓
本地程序 rerun 邮箱 AT issue
  ↓
AT Agent 读取结果并发布 AT_FINAL_RESULT
  ↓
AT Agent 标记 AT finalized
  ↓
AT Agent 唤醒队长
  ↓
队长遍历完整 issue 树
  ↓
队长在总入口写当前进度
  ↓
队长分派邮箱 UT 或其他节点任务
```

这里的“mention”需要分为两层：

1. 评论正文可以包含可读的 `@队长`、`@AT Agent`；
2. 真正触发后续 Agent 应使用 `issue assign --to-id` + `issue rerun`，不能只依赖文本 mention。

---

## 10. 总入口进度和最终汇总

每次 AT/UT Agent 完成结果后，队长必须在总入口 issue 写进度快照。

示例：

```text
[AT_UT_PROGRESS]

当前进度：
- deepin-mail：AT 已完成，UT 待执行
- dde-file-manager：AT 执行中
- deepin-editor：未开始

节点状态：
- local：空闲
- public-test：执行 dde-file-manager AT

下一步：
- 分派 deepin-mail UT 到 local
```

所有应用的 AT/UT 都 `finalized` 后：

1. 队长遍历整棵 issue 树；
2. 生成固定模板 HTML；
3. 把 AT/UT issue 链接写入报表；
4. 写入通过数、失败数、通过率；
5. 失败原因放在对应子 issue，报表只放链接；
6. 把 AT/UT 日志和报告作为附件；
7. 在总入口 issue 发布最终汇总；
8. 唤醒邮件 Agent；
9. 邮件 Agent 读取总入口 HTML 并发送邮件。

只有总入口 issue 允许发送最终邮件。

---

## 11. 异常和恢复

### 11.1 Agent 运行结束

Agent 结束不代表 AT/UT 失败。只要本地任务状态为：

```text
queued / running
```

队长不应重新启动任务，也不能因 Agent run completed 就判定失败。

### 11.2 本地程序异常退出

本地程序启动时必须检查本机 state：

```text
是否存在未完成任务
测试进程是否仍然存在
结果文件是否已经生成
```

如果测试进程仍在，继续托管；如果进程已退出，则根据退出码和日志写 `failed` 或 `interrupted`，再唤醒对应 Agent。

### 11.3 长时间任务

不使用固定总超时杀死任务。长任务必须有：

```text
进程真实状态
阶段状态
heartbeat
stdout/stderr 日志
退出码
```

heartbeat 失效只能标记 `unknown` 或 `blocked`，不能直接 kill，也不能直接重新执行导致重复测试。

### 11.4 邮件失败

邮件失败只影响邮件状态，不得篡改测试结果。邮件 Agent 必须在总入口写明：

```text
报表生成成功
邮件发送成功/失败
失败原因
```

---

## 12. 新测试小队

现有生产小队不直接修改，先创建新的验证小队。

建议名称：

```text
AT/UT研发自测流程验证小队
```

成员建议：

```text
AT/UT研发自测验证队长
AT研发自测-local验证
UT研发自测-local验证
AT研发自测-public-test验证
UT研发自测-public-test验证
研发自测报表邮件验证
```

验证阶段：

### 阶段一：单节点完整闭环

```text
deepin-mail
local 节点
AT -> UT -> 队长汇总
```

验证：

- Agent 是否立即返回；
- 本地程序是否继续执行；
- AT 结果是否由 AT Agent 评论；
- 队长是否被唤醒；
- 总入口是否每次更新进度；
- UT 是否在 AT finalized 后才开始；
- 最终 HTML 和邮件是否只生成一次。

### 阶段二：同节点串行

```text
deepin-mail
dde-file-manager
都路由到 local
```

验证：

- local 同一时间只有一个 AT/UT；
- 第二个任务进入 queued；
- 第一个任务完成后，队长继续分派下一个。

### 阶段三：多节点并发

```text
deepin-mail -> local
deepin-editor -> public-test
```

验证：

- 两台机器可以同时执行；
- 每台机器内部仍然只有一个任务；
- 结果分别唤醒对应节点的 AT/UT Agent；
- 队长能够合并所有应用结果。

---

## 13. 不采用的方案

以下方案不作为最终实现：

```text
1. Agent 前台等待完整 AT/UT；
2. 依赖 Agent 自己记住并持续轮询；
3. 依赖固定中心 HTTP server；
4. 中心程序 SSH 到公共测试机器；
5. 所有机器全局只允许一个任务；
6. 本地 worker 直接以运行时用户发布最终结果；
7. 只通过评论正文 @xxx 触发后续流程；
8. 子 issue 单独生成最终邮件；
9. 用固定总超时杀死长时间测试；
10. 修改业务源码绕过编译失败。
```

---

## 14. 最终闭环

```text
用户创建总入口 issue
  ↓
队长创建完整 issue 树并按 node 路由分派 AT
  ↓
AT Agent 提交本地任务后立即返回
  ↓
本地 utat-node 后台串行执行
  ↓
执行完成，写 result_ready 并 rerun AT Agent
  ↓
AT Agent 以 AT 身份评论，并唤醒队长
  ↓
队长遍历整棵树、更新总进度、分派 UT/其他应用
  ↓
UT Agent 提交本地任务后立即返回
  ↓
本地 utat-node 执行 UT
  ↓
UT Agent 以 UT 身份评论，并唤醒队长
  ↓
所有应用 AT/UT finalized
  ↓
队长在总入口生成最终 HTML
  ↓
队长唤醒邮件 Agent
  ↓
邮件 Agent 发送总报表
```

该流程的核心是：

```text
队长负责分派
Agent 负责身份化反馈
本地程序负责长时间执行
节点本地串行
不同节点并发
总入口统一汇总和发邮件
```
