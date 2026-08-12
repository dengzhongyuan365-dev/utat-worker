# V2 队列状态和 rerun 设计

## 核心口径

本地队列不再把“执行是否通过”和“队列是否完成”混在一起。

- 队列完成：这个 attempt 已经执行结束，并且 worker 已经生成 result_json，尝试回调，随后出队。
- 测试通过：result_json.status == done。
- 测试失败：result_json.status == failed，但仍然是一个完成结果。

因此编译失败、依赖失败、测试失败都不会让队列卡住；它们都会生成 failed result_json。

## 主键

```text
attempt_key = issue_id + attempt
```

- 普通 submit：如果当前 issue 已有 active attempt，则返回 already_active；否则创建当前 attempt。
- rerun submit：把当前 active attempt 标记 superseded，然后创建 next_attempt，即使 issue 是 done/finalized/result_ready。
- report complete：只看每个 issue 的 current_attempt/result_attempt/result_json.attempt 对应 result_json。

## worker 重启

worker 启动只扫描 active 表中的 queued/running/callback_failed。历史 completed/orphan/deleted 不会再次进入执行。

## issue 删除

- submit 前：若 issue 不存在，拒绝入队。
- 执行前 preflight：若 issue 不存在，标记 deleted/orphan 并出队。
- 回调时：若 issue 不存在，标记 callback_skipped_deleted 并出队。

## rerun

用户明确 rerun 时，agent/skill 必须调用 submit --rerun。旧 result_json 不删除；新 attempt 成为 current_attempt。最终 report 只认 current_attempt 的 result_json。
