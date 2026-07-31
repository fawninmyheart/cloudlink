# Cloudlink 节点断网后的执行恢复

日期：2026-07-31  
正式版本：`2026.07.31.2`

## 目标

长任务能够在本地离线继续运行时，Cloudlink 不应仅因控制面租约续期失败
而宣告任务作废，也不能把同一个任务重新派给其他节点造成重复执行。

本版本将“节点失联”和“任务失败”分开：

1. 脚本任务租约过期后进入非终态 `disconnected`。
2. 任务继续绑定原 `worker_id`、原 `lease_id` 和原资源预留。
3. 其他节点不能领取该任务。
4. 网络恢复后，原 Worker 调用 `/execution/resume` 恢复同一租约。
5. 成功、失败、超时和取消回报都在必要时先恢复租约，再提交终态。

## 状态转换

```text
pending -> running -> success / failed / timeout / cancelled
                 |
                 +-> disconnected -> running
                                      |
                                      +-> success / failed / timeout / cancelled
```

`disconnected` 不代表脚本已经停止。它表示服务器无法确认 Worker 的实时
状态，正在等待原执行重新建立控制连接。

## 安全边界

- 恢复请求必须同时匹配原 `worker_id` 和原 `lease_id`。
- 断联任务不参与重新调度，避免两台节点同时计算和覆盖结果。
- 断联期间的取消请求只记录为待处理；原 Worker 重连后收到取消请求并
  终止进程树，再回报 `cancelled`。
- 资源预留在执行断联期间保持不变；只有进入纯产物交付阶段后才释放计算
  资源。
- 已经完成的任务不能通过执行恢复接口复活。

## 与 Worker 重启的区别

网络中断不会停止 Worker 或它启动的脚本，因此可以恢复同一进程的控制
租约。Worker 服务、操作系统或宿主机重启则可能终止内存中的子进程；
Cloudlink 无法通用地重建任意 Python 进程。

已经完成计算并写入本地 outbox 的结果仍可在 Worker 重启后恢复上传。
尚未完成的长任务如需跨进程或跨主机重启继续，任务本身必须实现 checkpoint
并从 checkpoint 启动。

## 运维检查

服务器：

```bash
curl -fsS -H "X-Internal-API-Secret: $INTERNAL_API_SECRET" \
  http://127.0.0.1:8010/api/internal/queue/status
```

Worker：

```bash
sudo systemctl status cloudlink-worker-<worker-id>.service
tail -n 200 ~/.cloudlink/logs/worker.log
```

恢复成功时 Worker 日志包含：

```text
task <task-id> resumed after worker reconnect
```

## 发布要求

服务端和 Worker 必须同时升级到 `2026.07.31.2`。服务端最低 Worker 版本
同步提升，旧 Worker 在升级前不能领取新任务。正式发布使用正常的完整部署
流程；不要恢复或运行已删除的临时文件替换脚本。
