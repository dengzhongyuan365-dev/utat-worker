# utat-worker

worker 是 AT/UT 研发自测的新本地执行程序。V2 不再兼容旧 worker 双入口，验证时 agent/skill/worker 一起升级。

## 核心能力

- agent submit 后立即返回。
- 同一 node 本地串行执行。
- 多 node 可并发。
- 任务主键 `(issue_id, attempt)`，支持明确 rerun。
- 普通重复 submit 返回 already_active，不重复跑。
- rerun 会 supersede 旧 active attempt 并创建新 attempt。
- 跑完不管成功失败都生成 result_json、归档、回调并出队。
- issue 删除会清理/跳过，不阻塞队列。
- AT：拉代码、依赖、打包、安装、youqu AT。
- UT：拉代码、找脚本、跑脚本，不打包不安装。
- 自带局域网 Web 进度页。

## CLI

```bash
PYTHONPATH=/home/uos/WorkSpace/utat-worker/V2:/home/uos/WorkSpace/utat-worker/src python3 -m utat_worker.cli submit --payload-file payload.json
PYTHONPATH=/home/uos/WorkSpace/utat-worker/V2:/home/uos/WorkSpace/utat-worker/src python3 -m utat_worker.cli submit --payload-file payload.json --rerun
PYTHONPATH=/home/uos/WorkSpace/utat-worker/V2:/home/uos/WorkSpace/utat-worker/src python3 -m utat_worker.cli status
PYTHONPATH=/home/uos/WorkSpace/utat-worker/V2:/home/uos/WorkSpace/utat-worker/src python3 -m utat_worker.cli run-once
PYTHONPATH=/home/uos/WorkSpace/utat-worker/V2:/home/uos/WorkSpace/utat-worker/src python3 -m utat_worker.cli worker
PYTHONPATH=/home/uos/WorkSpace/utat-worker/V2:/home/uos/WorkSpace/utat-worker/src python3 -m utat_worker.cli serve --host 0.0.0.0 --port 8766
```

## Web 进度页

```text
http://<测试机器IP>:8766/
http://<测试机器IP>:8766/api/status
```
