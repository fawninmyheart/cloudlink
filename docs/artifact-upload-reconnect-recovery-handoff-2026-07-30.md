# Cloudlink 产物断网续传修复交接

日期：2026-07-30  
版本：`2026.07.30.1`  
仓库：`/Users/hua/Documents/cloudlink`  
生产目录：`sunfawn:/opt/cloudlink`

## 1. 目标

解决脚本计算已经成功，但在产物上传阶段因临时断网或连接中断而被标记
为任务失败的问题。

需要满足：

1. 计算成功和结果交付必须是两个独立阶段。
2. 网络异常时保留待上传状态，等待网络恢复后继续上传。
3. Worker 重启后自动恢复尚未完成的交付。
4. 上传从服务器确认的字节偏移继续，不能重复整个文件。
5. 已完成的历史长任务可以直接补传产物，不能要求重新计算。
6. 恢复接口不能复活取消、执行失败或已经成功的任务。

## 2. 事故证据

受影响任务：

```text
task_id: 5b3e0fcf-30e0-49d7-a7b1-3bcc08a7c975
worker_id: wsl-home
original_lease_id: 39423b70-d183-4f69-8968-239468c6ff9c
```

该 Transformer 任务计算约 7 小时 34 分钟并正常生成产物。失败发生在计算
结束后的第一个产物上传阶段：

```text
artifact: transformer_long_context_predictions.npz
size: 362231 bytes
chunk offset: 0
worker error: Remote end closed connection without response
final error_code: artifact_upload_failed
```

Worker 日志中可见七次上传/检查，时间分别为：

```text
00:37:08
00:37:34
00:38:48
00:39:14
00:39:59
00:41:52
00:43:20
```

可见重试跨度约 6 分 12 秒，整个失败上传阶段约 6 分 35 秒。现有定时重试
耗尽后，系统错误地把已经完成的计算标记为失败。日志能证明连接被远端
关闭，但不能单凭该错误断言具体是哪一段网络设备造成中断。

Worker 侧排查位置：

```bash
sudo systemctl status cloudlink-worker-wsl-home.service
sudo journalctl -u cloudlink-worker-wsl-home.service --since "2026-07-29 00:30:00"
tail -n 300 /home/cloudlink/.cloudlink/logs/worker.log
```

原始任务目录在恢复前保持完整：

```text
/home/cloudlink/.cloudlink/jobs/5b3e0fcf-30e0-49d7-a7b1-3bcc08a7c975/39423b70-d183-4f69-8968-239468c6ff9c
```

## 3. 已实现方案

### 3.1 持久化交付 Outbox

脚本退出码为 0 后，Worker 在第一次上传请求之前写入：

```text
$CLOUDLINK_HOME/delivery-outbox/<task_id>.json
```

记录包含任务、租约、结果元数据、任务目录和预期产物，不包含 Worker
密钥。只有服务器确认任务成功后才删除记录。

### 3.2 网络恢复后持续重连

产物上传遇到临时连接错误时：

1. 不再调用任务 `/failed`。
2. 保持任务租约续期。
3. 使用有上限的退避间隔继续尝试。
4. 不设置固定的总重试截止时间。
5. 始终以服务器返回的已上传字节数为续传起点。

因此，断网持续多久只影响交付延迟，不会改变已经完成的计算结论。

### 3.3 Worker 重启自动恢复

Worker 启动时扫描 `delivery-outbox`，为每条记录启动恢复交付流程。如果原
租约已经过期，Worker 通过服务器恢复接口申请仅用于交付的新租约，然后
继续上传。

接口：

```text
POST /api/worker/tasks/{task_id}/delivery/resume
```

### 3.4 恢复边界

服务器仅允许同时满足以下条件的任务恢复交付：

1. 任务类型为 `script_job`。
2. 请求来自原执行 Worker。
3. 任务因 `artifact_upload_failed` 失败，或因 `worker_lost` 超时。

以下状态不能通过该接口恢复：

```text
success
cancelled
execution timeout
script execution failure
其他无关失败
```

恢复时复用原 artifact 记录及服务器端部分文件，避免新建重复产物。

### 3.5 历史任务手动补传

新增命令：

```bash
scripts/start_local_worker.sh recover-delivery scripts/local_worker.env \
  --task-id TASK_ID \
  --lease-id ORIGINAL_LEASE_ID \
  --job-dir /path/to/jobs/TASK_ID/ORIGINAL_LEASE_ID
```

该命令读取已有 `outputs` 和 `datasets.json`，重建结果元数据、申请交付
租约、上传或续传产物并报告成功，不执行原脚本。

## 4. 主要代码变更

```text
worker/local_worker.py
  Outbox 持久化、重放、交付租约恢复、历史目录补传、重连循环

worker/script_runner.py
  在首次上传前保存完成结果和运行时 artifact manifest

app/task_store.py
  交付恢复状态机、Worker/失败原因约束、artifact 租约迁移

app/main.py
  Worker 鉴权的 /delivery/resume API

scripts/start_local_worker.sh
  recover-delivery 命令透传

app/version.py
  Cloudlink、CPU Worker、GPU Worker 最低版本提升到 2026.07.30.1

CHANGELOG.md
  版本说明
```

已有简要机制文档：

```text
docs/persistent-artifact-delivery-2026-07-30.md
```

## 5. 测试证据

完整测试结果：

```text
239 passed
```

重点覆盖：

```text
tests/test_artifact_api.py
  artifact_upload_failed 可恢复；复用 artifact；错误状态不可复活

tests/test_local_worker_heartbeat.py
  上传失败进入 pending；不报告任务失败；Outbox 可重放

tests/test_worker_script_job.py
  首次上传前持久化计算结果

tests/test_start_local_worker_script.py
  Wrapper 正确接受 recover-delivery 参数
```

最后一次针对 Wrapper 的聚焦测试结果：

```text
44 passed
```

## 6. 部署与恢复结果

生产服务器已经部署 `2026.07.30.1`，`cloudlink.service` 正常运行。运行时
关键文件与本地版本哈希已核对一致。

`wsl-home` 已更新并通过 Doctor：

```text
public_https: ok
worker_heartbeat: ok
dataset_delete_requests: ok
safe_claim_probe: ok
cloudlink-worker-wsl-home.service: active (running)
```

历史任务执行补传后，Worker 输出：

```text
[2026-07-30 19:33:30] completed preserved result delivery for task
5b3e0fcf-30e0-49d7-a7b1-3bcc08a7c975
```

服务器端最终核验：

```text
task status: success
error_code: null
artifact status: uploaded
artifact: transformer_long_context_predictions.npz
stored bytes: 362231
sha256: 9cf7da7be90aba4ab05554f17c1ac255657e1a07178df2e0eabf3dbee615edd7
completed_at: 2026-07-30 19:33:30 CST
```

服务器上的实体文件存在且大小与数据库记录一致。本次恢复没有重新运行
长任务。

## 7. 特殊手动恢复任务的研究结果

这项长任务必须单独向原研究 Codex 汇报。Cloudlink 的 `success` 表示计算
结果已经完整交付，不表示模型通过研究门槛。

任务身份：

```text
research_id: realtime-transformer-change-aware-20260729-r2
schema_version: realtime-transformer-change-aware-window-baseline-v2
runtime: pytorch-cuda
runtime_seconds: 26809.19
exit_code: 0
research status: development_failed
```

手动恢复并返回了三个原始输出：

```text
transformer_long_context_curves.json          20350 bytes
transformer_long_context_predictions.npz     362231 bytes
transformer_long_context_summary.json         29474 bytes
```

选中的容量与解码策略：

```text
model_id: change_aware_xxlarge
d_model: 896
nhead: 16
temporal_layers: 20
fusion_layers: 6
dim_feedforward: 3584
selected_epochs: 1
decoder_policy: causal_rank_p0.800_m0.05
```

开发集关键指标：

```text
window_count: 12
opportunity_window_count: 10
opportunity_window_pass_count: 5
opportunity_window_pass_rate: 0.50
baseline_lift_q25: 0.066096
baseline_lift_median: 0.743225
good_precision: 0.142628
pollution: 0.857372
non_adjacent_good_hit_window_count: 5
```

开发门槛结论：

```text
development_gate.passed: false
```

通过的检查：

```text
every_opportunity_window_has_minimum_activity
four_nonadjacent_good_hit_windows
```

未通过的检查：

```text
opportunity_window_pass_rate_at_least_80pct
baseline_lift_q25_above_1
good_precision_at_least_60pct
pollution_at_most_40pct
all_no_op_windows_abstain
```

由于开发门槛失败：

```text
audit_status: not_opened_development_failed
audit_gate.passed: false
sealed_validation.opened: false
sealed validation windows: 6, 8, 9, 12, 14, 22
sealed validation scores_finite_count: 0
```

正式汇报时应明确写成：

> 长任务计算正常完成，断网导致的产物上传失败已经通过手动
> `recover-delivery` 恢复，三个原始输出均已返回，未重新训练。模型在
> development gate 失败，因此没有开启 audit 或 sealed validation，
> 不能将 Cloudlink 任务状态 `success` 解读为研究通过。

接手 Codex 应读取并归档三个返回产物，将该结果写回原研究报告或证据账本，
然后依据开发门槛失败结论决定下一轮实验；不得重新提交同一长任务来替代
这次已经恢复的证据。

## 8. 当前 Git 边界

生产部署和任务恢复已经完成，但本地修复目前仍在工作区中，尚未在本交接
文档编写时声明为已提交、已推送或已创建 PR。接手者必须先检查：

```bash
git status --short
git diff --stat
git diff
```

不要覆盖或回退当前工作区变更。确认变更和测试后，再按项目流程提交。

## 9. 接手 Codex 建议检查清单

1. 检查当前 Git diff，确认上述变更完整且没有无关文件。
2. 重新运行完整测试，保留测试命令和结果。
3. 增加断网持续较长时间后恢复、Worker 在上传中重启、服务器已有部分
   chunk 三类端到端 smoke。
4. 验证 `/success` 确认响应丢失时仍保持幂等，不能生成重复 artifact。
5. 验证任务取消与 Outbox 重放同时发生时，取消状态优先。
6. 评估 Outbox 的保留、告警和人工清理策略，避免永久错误无限静默重试。
7. 将“计算完成”和“结果已交付”作为两个独立可观测状态展示在控制台。
8. 提交前确认版本门槛不会阻止已更新的 CPU/GPU Worker 正常领取任务。
9. 将第 7 节的手动恢复结果正式汇报到原研究任务，不能只汇报 Cloudlink
   修复完成。

## 10. 验收原则

不能只用 Worker 在线或一次接口成功作为验收。至少需要三类独立证据：

1. 并发首次加载同一数据集。
2. 大文件断线续传、下载和 SHA-256 一致。
3. `/success` 重复报告保持幂等。

对于研究任务，产物成功返回只证明交付完成，不等于模型或策略通过研究
验收。模型结论必须另外依据返回产物和研究门槛判断。
