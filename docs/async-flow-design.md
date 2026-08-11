
### 执行前 orphan 清理

worker 在 `claim_next()` 取到 queued 任务后、真正拉代码/构建/测试前，会先检查：

1. `root_issue_id` 是否仍存在；
2. 当前 AT/UT `issue_id` 是否仍存在。

如果 root issue 已删除：

- 写入 `~/.utat-node/tasks/<task_id>/orphaned-root-issue-deleted.json`；
- 本地状态写为 `orphaned`；
- 从 `~/.utat-node/queue.db` 删除该任务；
- 不执行源码同步、编译、AT/UT。

如果当前子 issue 已删除：

- 写入 `~/.utat-node/tasks/<task_id>/orphaned-issue-deleted.json`；
- 本地状态写为 `orphaned`；
- 从 `~/.utat-node/queue.db` 删除该任务；
- 不执行源码同步、编译、AT/UT。

手动清理仍可使用：

```bash
/home/uos/.utat-worker/venv/bin/utat-node --node-id local cleanup --root-issue-id <root_issue_id>
```
