# V2 远程进度页

## 启动

```bash
PYTHONPATH=/home/uos/WorkSpace/utat-worker/V2:/home/uos/WorkSpace/utat-worker/src \
python3 -m utat_worker_v2.cli serve --host 0.0.0.0 --port 8766
```

## 浏览器访问

```text
http://<机器IP>:8766/
```

JSON 接口：

```text
http://<机器IP>:8766/api/status
```

按 issue 过滤：

```text
http://<机器IP>:8766/?issue_id=<issue_id>
http://<机器IP>:8766/api/status?issue_id=<issue_id>
```

## 展示字段

- node_id
- task_type
- app_name
- issue_id
- attempt
- state
- current_step
- progress
- message
- created_at/updated_at
- archive_path

## 当前阶段

先不做鉴权，便于局域网验证。后续需要公共机器部署时，再加只读 token 或 nginx 内网白名单。
