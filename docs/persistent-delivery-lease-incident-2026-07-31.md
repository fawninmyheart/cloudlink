# Cloudlink 持久化交付租约失效事故与临时修复

日期：2026-07-31  
临时修复版本：`2026.07.31.1`  
本地仓库：`/home/ubuntu/research/cloudlink-source`  
上游仓库：`https://github.com/fawninmyheart/cloudlink`

## 1. 摘要

Cloudlink `2026.07.30.1` 已经能够在脚本计算完成后把待交付结果持久化到
delivery outbox，并在网络恢复或 Worker 重启后继续上传。但如果网络中断
时间超过任务租约期限，恢复线程仍会使用已经失效的租约创建 artifact，
持续收到 HTTP 409。

旧实现把持久化交付线程计入 `active_tasks`。在
`max_concurrent_tasks=1` 的 GPU Worker 上，这个无限重试的交付线程会永久
占用唯一计算槽。服务端恢复交付租约时还保留原训练任务的
`resource_reservation`，因此即使只修正线程计数，调度器仍可能继续假占
GPU、CPU、内存和磁盘资源。

本次临时修复完成了以下处理：

1. 识别 artifact API 返回的任务租约类 HTTP 409。
2. 终止旧交付租约续期，重新调用 `/delivery/resume` 获取交付租约。
3. 使用新租约继续既有 artifact 的断点上传。
4. 将交付线程从脚本计算并发计数中分离。
5. 恢复交付时清除原计算任务的资源预留。

## 2. 受影响任务

```text
task_id: 3d884ce1-3aca-40a3-a452-706529d3c8d0
worker_id: wsl-home
artifact: transformer_r3_crossfit_fold_predictions.npz
```

修复前反复出现：

```text
artifact_delivery_pending task=3d884ce1-3aca-40a3-a452-706529d3c8d0:
artifact upload failed for transformer_r3_crossfit_fold_predictions.npz:
POST /api/worker/tasks/3d884ce1-3aca-40a3-a452-706529d3c8d0/artifacts
failed after 1 attempt(s): HTTP Error 409: Conflict
```

修复前状态证据：

```text
task status: timeout
error_code: worker_lost
locked_by: wsl-home
artifact rows: 0
worker active_task_count: 1
worker max_concurrent_tasks: 1
queue running_count: 0
```

`running_count=0` 但 `active_task_count=1` 是关键异常组合：没有脚本正在运行，
但 Worker 认为唯一计算槽已被占用。

## 3. 根因链路

### 3.1 交付租约只在重放开始时恢复一次

`replay_delivery_item()` 启动时调用一次 `resume_delivery_lease()`，随后启动
租约续期线程并进入 `deliver_preserved_result()`。

如果网络中断时间超过服务端 `TASK_LOCK_SECONDS=1800`：

1. 租约续期请求无法到达服务端。
2. 服务端将任务转为 `timeout/worker_lost`。
3. 网络恢复后，Worker 仍使用 outbox 中的旧租约上传 artifact。
4. artifact 创建接口因任务已结束或租约不匹配返回 HTTP 409。

### 3.2 409 的具体含义在异常包装中丢失

`WorkerApiClient` 原来只保留 HTTP 状态码，不保留响应体中的 FastAPI
`detail`。`ResultArtifactUploader` 又把底层异常包装成通用
`ArtifactUploadFailed`。

因此 Worker 无法区分：

- `Task is already finished`
- `Task is not running`
- `Task is not locked by this worker`
- `Task lease does not match`
- artifact 相同路径但元数据不同造成的真实 `ArtifactConflict`

所有情况都会进入相同的无限上传重试。

### 3.3 交付线程占用计算槽

`replay_delivery_outbox()` 把交付线程写入 `active_tasks`。
`active_task_count()` 直接返回该字典长度，主循环仅在：

```text
active_task_count < max_concurrent_tasks
```

时领取新任务。单并发 GPU Worker 因此永久停止领取任务。

### 3.4 交付阶段仍保留计算资源预留

`resume_task_delivery()` 把任务重新设为 `running`，但没有清除原来的
`resource_reservation`。模型已经计算完成，交付阶段只需要网络和少量文件
读取，不应继续预留整张 GPU 或原训练资源。

## 4. 临时修复实现

### 4.1 保留 API 错误响应体

文件：`worker/api_client.py`

`ApiRequestError` 新增 `response_body`。遇到 `urllib.error.HTTPError` 时读取
并保存响应体，使 Worker 可以解析服务端 `detail`。

### 4.2 精确识别租约类冲突

文件：`worker/local_worker.py`

新增：

```text
DeliveryLeaseLost
delivery_lease_was_lost()
DELIVERY_LEASE_CONFLICT_DETAILS
```

仅将已知的任务状态或租约冲突识别为租约失效。artifact 元数据冲突不会被
误判为需要重新申请租约。

### 4.3 重新获取交付租约

`deliver_preserved_result()` 检测到租约失效后抛出
`DeliveryLeaseLost`。`replay_delivery_item()` 会：

1. 停止旧租约续期线程。
2. 使用当前 outbox 租约调用 `/delivery/resume`。
3. 把新租约原子写回 outbox。
4. 启动新租约续期线程。
5. 继续产物上传和成功结果回报。

网络类临时错误仍保持原来的持续退避重试，不会重新执行已经完成的 GPU
计算。

### 4.4 交付线程与计算线程分离

新增独立的：

```text
delivery_threads
delivery_threads_lock
join_delivery_threads()
```

`active_tasks` 只记录正在执行脚本的线程。心跳和领取任务请求中的
`active_task_count` 不再包含持久化交付线程。

### 4.5 清除交付阶段资源预留

文件：`app/task_store.py`

`resume_task_delivery()` 将：

```sql
resource_reservation = NULL
```

交付恢复不会继续占用原模型任务的 GPU、CPU、内存和磁盘调度资源。

### 4.6 版本与部署入口

版本提升为：

```text
CLOUDLINK_VERSION=2026.07.31.1
MINIMUM_WORKER_VERSION=2026.07.31.1
MINIMUM_GPU_WORKER_VERSION=2026.07.31.1
```

本机临时部署入口：

```text
scripts/deploy_local_delivery_fix.sh
```

该脚本仅替换六个明确列出的文件，备份旧文件，检查 Python 语法，重启
`cloudlink.service` 并验证版本。它不读取或覆盖数据库、环境文件、令牌和
Worker 本地配置。

## 5. 验证结果

代码验证：

```text
完整测试：242 passed
最新定向测试：39 passed
git diff --check：通过
部署脚本 bash -n：通过
部署脚本 --help：通过
部署脚本 --dry-run：通过
无 root 权限执行：按预期 exit 2
```

新增或扩展的回归覆盖：

1. delivery outbox 重放不增加 `active_task_count`。
2. artifact 租约冲突后重新申请租约。
3. 包装后的 HTTP 409 仍可识别任务租约失效。
4. artifact 元数据冲突不会被误判为租约失效。
5. 同一运行中的交付租约可以再次恢复。
6. 恢复交付后 `resource_reservation` 被清除。
7. API 客户端保留 HTTP 错误响应体。

生产验证：

```text
server version: 2026.07.31.1
wsl-home version: 2026.07.31.1
wsl-home online: true
wsl-home needs_update: false
wsl-home active_task_count: 0
wsl-home last_error: null
GPU runtime verified: true
```

原阻塞任务恢复结果：

```text
task status: success
error_code: null
artifact status: uploaded
artifact size: 211980 bytes
artifact path: transformer_r3_crossfit_fold_predictions.npz
finished_at: 2026-07-31T05:09:24.671443+00:00
```

CUDA 烟测：

```text
task_id: c58ce0a0-f78e-4b61-84b6-a2d9e61bb96f
status: success
worker_id: wsl-home
device: NVIDIA GeForce RTX 4090
torch: 2.12.1+cu130
CUDA result finite: true
created_at: 2026-07-31T05:11:19.805722+00:00
finished_at: 2026-07-31T05:11:35.819782+00:00
```

烟测结束后：

```text
pending_count: 0
running_count: 0
reserved gpu_count: 0
wsl-home active_task_count: 0
```

## 6. Git 和部署边界

截至本文档编写时：

```text
本地补丁代码 tip: b008be4
临时修复提交: db05e17
本地部署入口提交: b008be4
GitHub origin/main: 031b9d6
```

没有向 GitHub 推送任何提交，也没有创建 PR。

重要：GitHub `031b9d6` 是 `2026.07.29.1`。生产中已经部署但未提交到上游
的 `2026.07.30.1` 持久化交付改动，已先还原到本地仓库，再叠加本次
`2026.07.31.1` 修复。因此开发者不能只从远端旧版本摘取少量
`2026.07.31.1` 差异而忽略 `2026.07.30.1` 基线。

## 7. 当前限制

以下两个 CPU Worker 仍运行 `2026.07.30.1`，并因最低版本要求被标记为
`needs_update=true`：

```text
local-Mac-mini-1
macbook-air
```

根据用户决定，本次暂不处理这两个节点。它们不会影响已经更新的
`wsl-home` GPU Worker，但在正式版本发布后需要按正常 Worker 更新流程
升级。

## 8. 正式合入建议

Cloudlink 开发者正式接手时建议：

1. 先确认并提交 `2026.07.30.1` 持久化交付基线。
2. 将本次租约重新获取、线程分离和资源预留清理纳入正式分支。
3. 保留任务租约冲突与 artifact 元数据冲突的明确区分。
4. 增加网络中断超过 `TASK_LOCK_SECONDS` 后恢复的端到端测试。
5. 增加上传期间 Worker 重启、服务端已有部分 chunk 的端到端测试。
6. 验证取消请求与 delivery outbox 重放并发时，取消状态优先。
7. 在控制台分别展示“计算完成”和“结果交付完成”，避免把交付阻塞误解
   为 GPU 仍在计算。
8. 为长期 pending 的 delivery outbox 增加告警和人工处理入口。
9. 更新并重新验收 CPU 与 GPU Worker 安装包。

## 9. 研究结果边界

本次 Cloudlink 修复只证明：

1. 旧计算结果可以在租约失效后继续交付。
2. GPU Worker 可以重新领取和执行 CUDA 任务。
3. 交付失败不会再永久占用计算槽和 GPU 调度资源。

Cloudlink 任务进入 `success` 不表示 Transformer 模型通过研究门槛。模型
是否有效仍必须依据预测产物、开发集门槛、审计集和密封验证集结果单独
判断。
