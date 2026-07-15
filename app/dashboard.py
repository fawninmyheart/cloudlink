def dashboard_html() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Cloudlink 调度控制台</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f4f6f8;
      --panel: #ffffff;
      --panel-soft: #f8fafc;
      --line: #d9e0ea;
      --line-strong: #c6d0df;
      --text: #172033;
      --muted: #637083;
      --muted-strong: #465568;
      --accent: #1b5fd6;
      --accent-soft: #eef4ff;
      --ok: #167044;
      --ok-bg: #eaf7f0;
      --warn: #956000;
      --warn-bg: #fff7df;
      --bad: #be2b35;
      --bad-bg: #fff0f1;
      --idle: #5b6678;
      --idle-bg: #f1f4f8;
      --shadow: 0 1px 2px rgba(16, 24, 40, 0.04);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-width: 320px;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
      letter-spacing: 0;
    }
    button {
      appearance: none;
      height: 34px;
      border: 1px solid var(--line-strong);
      background: var(--panel);
      border-radius: 6px;
      padding: 0 12px;
      color: var(--text);
      cursor: pointer;
      font: inherit;
      white-space: nowrap;
    }
    button:hover { border-color: var(--accent); color: var(--accent); }
    button.danger:hover { border-color: var(--bad); color: var(--bad); }
    button:disabled { cursor: not-allowed; opacity: .55; }
    .app-header {
      border-bottom: 1px solid var(--line);
      background: rgba(255, 255, 255, .94);
      padding: 14px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 20px;
      position: sticky;
      top: 0;
      z-index: 5;
      backdrop-filter: blur(10px);
    }
    .brand {
      display: flex;
      align-items: baseline;
      gap: 12px;
      min-width: 0;
    }
    h1 {
      margin: 0;
      font-size: 18px;
      line-height: 1.2;
      font-weight: 700;
      letter-spacing: 0;
    }
    .brand small {
      color: var(--muted);
      font-size: 13px;
      white-space: nowrap;
    }
    .header-actions {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 12px;
      min-width: 0;
    }
    .last-updated {
      color: var(--muted);
      font-size: 13px;
      white-space: nowrap;
    }
    main {
      width: min(1840px, calc(100vw - 24px));
      margin: 0 auto;
      padding: 18px 0 34px;
    }
    .notice {
      display: none;
      margin-bottom: 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 10px 12px;
      color: var(--muted-strong);
      box-shadow: var(--shadow);
    }
    .notice.show { display: block; }
    .metrics {
      display: grid;
      grid-template-columns: repeat(8, minmax(112px, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }
    .metric {
      min-height: 74px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 12px 14px;
      box-shadow: var(--shadow);
    }
    .metric strong {
      display: block;
      margin-bottom: 4px;
      font-size: 26px;
      line-height: 1;
      font-weight: 700;
    }
    .metric span {
      display: block;
      color: var(--muted);
      font-size: 12px;
    }
    .workspace {
      display: grid;
      grid-template-columns: minmax(0, 2.2fr) minmax(320px, .8fr);
      gap: 14px;
      align-items: stretch;
      height: clamp(560px, calc(100vh - 230px), 760px);
    }
    section.panel {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      overflow: hidden;
      box-shadow: var(--shadow);
    }
    .panel-head {
      min-height: 48px;
      border-bottom: 1px solid var(--line);
      padding: 12px 14px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      background: var(--panel);
    }
    .panel-title {
      margin: 0;
      font-size: 14px;
      line-height: 1.25;
      font-weight: 700;
      letter-spacing: 0;
    }
    .panel-meta {
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }
    #tasks-panel {
      display: flex;
      flex-direction: column;
      height: auto;
      min-height: 0;
    }
    .task-scroll {
      flex: 1;
      min-height: 0;
      overflow: auto;
    }
    #tasks-panel thead th {
      position: sticky;
      top: 0;
      z-index: 1;
    }
    #workers-panel {
      display: flex;
      flex-direction: column;
      min-height: 0;
    }
    .worker-card-list {
      flex: 1;
      min-height: 0;
      overflow: auto;
      padding: 12px;
      display: grid;
      align-content: start;
      gap: 10px;
      background: var(--panel-soft);
    }
    .worker-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 10px;
      box-shadow: var(--shadow);
    }
    .worker-card-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 8px;
    }
    .worker-title {
      display: grid;
      gap: 3px;
      min-width: 0;
    }
    .worker-title .mono {
      font-weight: 700;
      color: var(--text);
    }
    .worker-stats {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 6px;
      margin-bottom: 8px;
    }
    .worker-stat {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-soft);
      padding: 7px 8px;
    }
    .worker-stat span {
      display: block;
      color: var(--muted);
      font-size: 12px;
    }
    .worker-stat strong {
      display: block;
      margin-top: 2px;
      font-size: 13px;
      overflow-wrap: anywhere;
    }
    .worker-card-actions { display: flex; align-items: center; gap: 6px; }
    .icon-button {
      width: 34px;
      min-width: 34px;
      padding: 0;
      font-size: 16px;
      line-height: 1;
    }
    .worker-paths {
      display: grid;
      gap: 4px;
      margin-bottom: 8px;
      color: var(--muted);
      font-size: 12px;
    }
    .worker-paths .mono { color: var(--muted-strong); }
    .resource-list {
      display: grid;
      gap: 7px;
    }
    .resource-row {
      display: grid;
      grid-template-columns: 54px minmax(0, 1fr) auto;
      align-items: center;
      gap: 8px;
      color: var(--muted-strong);
      font-size: 12px;
    }
    .resource-track {
      height: 7px;
      border-radius: 999px;
      background: #e8edf4;
      overflow: hidden;
    }
    .resource-fill {
      height: 100%;
      border-radius: 999px;
      background: var(--accent);
    }
    .table-wrap { overflow-x: auto; }
    table {
      width: 100%;
      min-width: 720px;
      border-collapse: collapse;
      table-layout: fixed;
    }
    .compact-table { min-width: 440px; }
    th, td {
      border-bottom: 1px solid var(--line);
      padding: 11px 12px;
      text-align: left;
      vertical-align: top;
      overflow-wrap: anywhere;
    }
    th {
      color: var(--muted);
      background: var(--panel-soft);
      font-size: 12px;
      font-weight: 650;
    }
    tr:last-child td { border-bottom: 0; }
    tbody tr[data-task] { cursor: pointer; }
    tbody tr[data-task]:hover td { background: #fbfdff; }
    tbody tr.selected td { background: var(--accent-soft); }
    .mono {
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
      line-height: 1.35;
    }
    .muted { color: var(--muted); }
    .primary-text { color: var(--text); font-weight: 650; }
    .state {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 56px;
      height: 24px;
      border-radius: 999px;
      padding: 0 9px;
      font-size: 12px;
      font-weight: 650;
      white-space: nowrap;
    }
    .state.success, .state.online, .state.cached, .state.extracted, .state.ok { color: var(--ok); background: var(--ok-bg); }
    .state.running, .state.downloading { color: var(--accent); background: var(--accent-soft); }
    .state.pending, .state.delete_requested { color: var(--warn); background: var(--warn-bg); }
    .state.failed, .state.timeout, .state.offline, .state.missing, .state.invalid, .state.needs_update { color: var(--bad); background: var(--bad-bg); }
    .state.idle, .state.deleted, .state.disabled { color: var(--idle); background: var(--idle-bg); }
    .tags {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    .tag {
      display: inline-flex;
      min-height: 24px;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: var(--panel-soft);
      padding: 2px 8px;
      color: var(--muted-strong);
      font-size: 12px;
    }
    .empty {
      padding: 24px 14px;
      color: var(--muted);
      text-align: center;
    }
    .detail-body {
      padding: 14px;
      display: grid;
      gap: 12px;
      flex: 1;
      min-height: 0;
      overflow: auto;
    }
    .detail-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .kv {
      min-height: 62px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-soft);
      padding: 10px;
    }
    .kv span {
      display: block;
      margin-bottom: 5px;
      color: var(--muted);
      font-size: 12px;
    }
    .kv strong {
      display: block;
      font-size: 13px;
      font-weight: 650;
      overflow-wrap: anywhere;
    }
    .result-line {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: var(--panel);
    }
    .result-line h3 {
      margin: 0 0 7px;
      font-size: 13px;
      line-height: 1.25;
    }
    .artifact-list {
      display: grid;
      gap: 8px;
    }
    .artifact-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
      background: var(--panel-soft);
    }
    .artifact-card .mono {
      margin-top: 6px;
      color: var(--muted-strong);
      overflow-wrap: anywhere;
    }
    .button-link {
      display: inline-flex;
      align-items: center;
      height: 30px;
      margin-top: 8px;
      border: 1px solid var(--line-strong);
      border-radius: 6px;
      padding: 0 10px;
      color: var(--accent);
      background: var(--panel);
      text-decoration: none;
      font-size: 13px;
    }
    .button-link:hover { border-color: var(--accent); }
    pre {
      margin: 0;
      max-height: 280px;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #0f172a;
      color: #dbeafe;
      padding: 12px;
      white-space: pre-wrap;
      word-break: break-word;
    }
    details {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      overflow: hidden;
    }
    summary {
      cursor: pointer;
      padding: 10px 12px;
      color: var(--muted-strong);
      font-weight: 650;
    }
    details pre {
      border: 0;
      border-top: 1px solid var(--line);
      border-radius: 0;
      max-height: 340px;
    }
    .modal-backdrop {
      position: fixed;
      inset: 0;
      display: none;
      align-items: center;
      justify-content: center;
      padding: 24px;
      background: rgba(15, 23, 42, .38);
      z-index: 20;
    }
    .modal-backdrop.show { display: flex; }
    .modal {
      width: min(860px, calc(100vw - 32px));
      max-height: min(780px, calc(100vh - 48px));
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      box-shadow: 0 18px 60px rgba(15, 23, 42, .24);
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    .modal-head {
      min-height: 50px;
      border-bottom: 1px solid var(--line);
      padding: 12px 14px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      background: var(--panel);
    }
    .modal-body {
      padding: 14px;
      overflow: auto;
      display: grid;
      gap: 12px;
      background: var(--panel-soft);
    }
    .modal-foot {
      border-top: 1px solid var(--line);
      padding: 12px 14px;
      display: flex;
      justify-content: flex-end;
      gap: 8px;
      background: var(--panel);
    }
    .form-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }
    label.field {
      display: grid;
      gap: 6px;
      color: var(--muted-strong);
      font-size: 12px;
      font-weight: 650;
    }
    input, select, textarea {
      width: 100%;
      border: 1px solid var(--line-strong);
      border-radius: 6px;
      background: var(--panel);
      color: var(--text);
      font: inherit;
    }
    input, select {
      height: 34px;
      padding: 0 10px;
    }
    textarea {
      min-height: 106px;
      padding: 10px;
      resize: vertical;
    }
    .modal-note {
      color: var(--muted);
      font-size: 12px;
    }
    .command-box {
      display: grid;
      gap: 8px;
    }
    .root-editor-list {
      display: grid;
      gap: 8px;
    }
    .root-editor {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 112px minmax(96px, .35fr) 116px 34px;
      gap: 8px;
      align-items: center;
    }
    .root-validation {
      display: grid;
      gap: 3px;
      min-width: 0;
      color: var(--muted);
      font-size: 12px;
    }
    .root-validation .state {
      justify-self: start;
    }
    .reserve-grid {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 8px;
    }
    .reserve-grid input {
      text-align: right;
    }
    .data-grid {
      display: grid;
      grid-template-columns: 1fr;
      gap: 14px;
      margin-top: 14px;
    }
    .path-cell {
      max-width: 100%;
      color: var(--muted-strong);
    }
    .row-actions {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
    }
    @media (max-width: 1100px) {
      .metrics { grid-template-columns: repeat(4, minmax(112px, 1fr)); }
      .workspace {
        grid-template-columns: 1fr;
        height: auto;
      }
      #tasks-panel {
        height: 460px;
        min-height: 460px;
      }
      #workers-panel {
        min-height: 420px;
      }
      .root-editor {
        grid-template-columns: 1fr;
      }
      .reserve-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
    }
    @media (max-width: 720px) {
      .app-header {
        position: static;
        align-items: flex-start;
        flex-direction: column;
        padding: 14px;
      }
      .brand { flex-direction: column; gap: 3px; }
      .brand small { white-space: normal; }
      .header-actions { width: 100%; justify-content: space-between; }
      main {
        width: calc(100vw - 28px);
        padding-top: 14px;
      }
      .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .detail-grid { grid-template-columns: 1fr; }
      #tasks-panel {
        height: 420px;
        min-height: 420px;
      }
      table { min-width: 680px; }
      .compact-table { min-width: 520px; }
    }
  </style>
</head>
<body>
  <header class="app-header">
    <div class="brand">
      <h1>Cloudlink 调度控制台</h1>
      <small>云端派发，本地计算</small>
    </div>
    <div class="header-actions">
      <div class="last-updated" id="updated">正在载入</div>
      <button id="open-password-modal" type="button">修改密码</button>
      <button id="refresh" type="button">刷新</button>
    </div>
  </header>

  <main>
    <div class="notice" id="notice" role="status"></div>
    <div class="metrics" id="summary"></div>

    <div class="workspace">
      <section class="panel" id="tasks-panel">
        <div class="panel-head">
          <h2 class="panel-title">任务队列</h2>
          <div class="panel-meta" id="task-count">0 个任务</div>
        </div>
        <div class="table-wrap task-scroll">
          <table aria-label="任务队列">
            <thead>
              <tr>
                <th style="width: 18%">任务</th>
                <th style="width: 12%">状态</th>
                <th style="width: 14%">类型</th>
                <th style="width: 18%">执行节点</th>
                <th style="width: 12%">运行 / 总耗时</th>
                <th style="width: 18%">更新时间</th>
                <th style="width: 8%">操作</th>
              </tr>
            </thead>
            <tbody id="tasks"></tbody>
          </table>
        </div>
      </section>

      <section class="panel" id="workers-panel">
        <div class="panel-head">
          <h2 class="panel-title">本地算力节点</h2>
          <div class="row-actions">
            <div class="panel-meta" id="worker-count">0 在线</div>
            <button id="open-worker-install-modal" type="button">添加节点</button>
          </div>
        </div>
        <div class="worker-card-list" id="workers"></div>
      </section>
    </div>

    <div class="data-grid">
      <section class="panel" id="datasets-panel">
        <div class="panel-head">
          <h2 class="panel-title">服务器数据维护</h2>
          <div class="panel-meta" id="dataset-count">0 份数据</div>
        </div>
        <div class="table-wrap">
          <table aria-label="服务器数据维护">
            <thead>
              <tr>
                <th style="width: 20%">数据集</th>
                <th style="width: 10%">版本</th>
                <th style="width: 14%">来源</th>
                <th style="width: 12%">体积</th>
                <th style="width: 30%">服务端位置</th>
                <th style="width: 14%">操作</th>
              </tr>
            </thead>
            <tbody id="datasets"></tbody>
          </table>
        </div>
      </section>

      <section class="panel" id="caches-panel">
        <div class="panel-head">
          <h2 class="panel-title">节点数据缓存</h2>
          <div class="panel-meta" id="cache-count">0 条缓存</div>
        </div>
        <div class="table-wrap">
          <table aria-label="节点数据缓存">
            <thead>
              <tr>
                <th style="width: 18%">节点</th>
                <th style="width: 20%">数据集</th>
                <th style="width: 12%">状态</th>
                <th style="width: 12%">占用</th>
                <th style="width: 26%">本地位置</th>
                <th style="width: 12%">操作</th>
              </tr>
            </thead>
            <tbody id="dataset-caches"></tbody>
          </table>
        </div>
      </section>
    </div>
  </main>

  <div class="modal-backdrop" id="task-modal" role="dialog" aria-modal="true" aria-labelledby="task-modal-title">
    <div class="modal">
      <div class="modal-head">
        <h2 class="panel-title" id="task-modal-title">任务详情</h2>
        <button id="task-modal-close" type="button">关闭</button>
      </div>
      <div class="modal-body" id="task-modal-body"></div>
    </div>
  </div>

  <div class="modal-backdrop" id="worker-settings-modal" role="dialog" aria-modal="true" aria-labelledby="worker-settings-title">
    <div class="modal">
      <div class="modal-head">
        <h2 class="panel-title" id="worker-settings-title">节点设置</h2>
        <button id="worker-settings-close" type="button">关闭</button>
      </div>
      <div class="modal-body">
        <div class="form-grid">
          <label class="field">
            最大并发
            <input id="worker-settings-concurrency" type="number" min="1" step="1">
          </label>
          <label class="field">
            任务路径
            <input id="worker-settings-job-root" type="text" spellcheck="false">
          </label>
        </div>
        <section class="result-line">
          <div class="row-actions" style="justify-content: space-between; margin-bottom: 10px;">
            <h3 style="margin: 0;">数据盘</h3>
            <button id="worker-settings-add-root" type="button">添加数据盘</button>
          </div>
          <div class="root-editor-list" id="worker-settings-roots"></div>
        </section>
        <section class="result-line" id="reserve-overrides">
          <h3>系统保留</h3>
          <div class="reserve-grid">
            <label class="field">CPU 核<input id="reserve-cpu" type="number" min="0" step="1"></label>
            <label class="field">内存 GB<input id="reserve-memory" type="number" min="0" step="1"></label>
            <label class="field">任务盘 GB<input id="reserve-job-disk" type="number" min="0" step="1"></label>
            <label class="field">数据盘 GB<input id="reserve-dataset-disk" type="number" min="0" step="1"></label>
            <label class="field">GPU 显存 GB<input id="reserve-gpu" type="number" min="0" step="1"></label>
          </div>
        </section>
      </div>
      <div class="modal-foot">
        <button id="worker-settings-cancel" type="button">取消</button>
        <button id="worker-settings-save" type="button">保存设置</button>
      </div>
    </div>
  </div>

  <div class="modal-backdrop" id="password-modal" role="dialog" aria-modal="true" aria-labelledby="password-modal-title">
    <div class="modal">
      <div class="modal-head">
        <h2 class="panel-title" id="password-modal-title">修改控制台密码</h2>
        <button id="password-modal-close" type="button">关闭</button>
      </div>
      <div class="modal-body">
        <div class="form-grid">
          <label class="field">
            当前密码
            <input id="password-current" type="password" autocomplete="current-password">
          </label>
          <label class="field">
            新密码
            <input id="password-new" type="password" autocomplete="new-password">
          </label>
          <label class="field">
            确认新密码
            <input id="password-confirm" type="password" autocomplete="new-password">
          </label>
        </div>
        <div class="modal-note">修改成功后，浏览器可能需要刷新页面并重新输入新密码。</div>
      </div>
      <div class="modal-foot">
        <button id="password-modal-cancel" type="button">取消</button>
        <button id="password-save" type="button">保存密码</button>
      </div>
    </div>
  </div>

  <div class="modal-backdrop" id="worker-install-modal" role="dialog" aria-modal="true" aria-labelledby="worker-install-title">
    <div class="modal">
      <div class="modal-head">
        <h2 class="panel-title" id="worker-install-title">添加本地算力节点</h2>
        <button id="worker-install-close" type="button">关闭</button>
      </div>
      <div class="modal-body">
        <div class="form-grid">
          <label class="field">
            系统
            <select id="worker-install-platform">
              <option value="macos">macOS</option>
              <option value="linux">Linux</option>
            </select>
          </label>
          <label class="field">
            节点 ID
            <input id="worker-install-id" type="text" spellcheck="false" placeholder="例如 local-mac-mini">
          </label>
          <label class="field">
            显示名称
            <input id="worker-install-name" type="text" spellcheck="false" placeholder="例如 Mac mini">
          </label>
        </div>
        <div class="command-box">
          <div class="modal-note">生成后复制命令到本地计算节点终端执行。命令使用短期安装 token，不包含长期 worker 密钥。Windows 电脑请先进入 WSL，然后选择 Linux。</div>
          <textarea id="worker-install-command" readonly spellcheck="false" placeholder="点击生成安装命令"></textarea>
          <div class="row-actions">
            <button id="worker-install-generate" type="button">生成安装命令</button>
            <button id="worker-install-copy" type="button">复制命令</button>
          </div>
          <div class="modal-note" id="worker-install-meta"></div>
        </div>
      </div>
    </div>
  </div>

  <script>
    let currentTaskId = null;
    let latestOverview = null;
    let overviewSectionEtags = null;
    const renderState = {
      summary: "",
      tasks: "",
      workers: "",
      datasets: "",
      datasetCaches: "",
      overviewEtag: "",
    };
    const workerSettingsState = {
      open: false,
      dirty: false,
      workerId: null,
      roots: [],
    };
    const passwordState = { open: false, dirty: false };
    const workerInstallState = { open: false, dirty: false };

    const taskStatusLabels = {
      pending: "待领取",
      running: "执行中",
      success: "成功",
      failed: "失败",
      timeout: "超时",
      cancelled: "已取消",
    };
    const workerStatusLabels = { online: "在线", offline: "离线", needs_update: "需要更新" };
    const sourceKindLabels = {
      symlink_file: "软链接文件",
      owned_file: "托管文件",
      owned_archive: "托管压缩包",
    };
    const cacheStatusLabels = {
      downloading: "下载中",
      cached: "已缓存",
      extracted: "已解压",
      delete_requested: "待删除",
      deleted: "已删除",
      failed: "异常",
      missing: "已丢失",
      invalid: "校验失败",
    };
    const rootValidationLabels = {
      ok: "可用",
      failed: "不可用",
      pending: "待验证",
      disabled: "停用",
    };

    function esc(value) {
      return String(value ?? "").replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }[char]));
    }

    function fmt(value) {
      if (!value) return "-";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return "-";
      return date.toLocaleString("zh-CN", { hour12: false });
    }

    function shortId(id) {
      return id ? String(id).slice(0, 8) : "-";
    }

    function bytes(value) {
      const size = Number(value || 0);
      const compact = (number, digits) => Number(number.toFixed(digits)).toString();
      if (size >= 1024 * 1024 * 1024) return `${compact(size / 1024 / 1024 / 1024, 2)} GB`;
      if (size >= 1024 * 1024) return `${compact(size / 1024 / 1024, 2)} MB`;
      if (size >= 1024) return `${compact(size / 1024, 1)} KB`;
      return `${size} B`;
    }

    function bytesToGiB(value) {
      const size = Number(value || 0);
      return Math.round(size / 1024 / 1024 / 1024);
    }

    function giBToBytes(value) {
      const size = Number(value || 0);
      if (!Number.isFinite(size) || size <= 0) return 0;
      return Math.round(size) * 1024 * 1024 * 1024;
    }

    function cores(value) {
      const count = Number(value || 0);
      if (!count) return "0 核";
      return `${Math.max(0, Math.round(count)).toFixed(0)} 核`;
    }

    function stableSortByKey(items, keyFn) {
      return [...(items || [])].sort((left, right) => {
        const leftKey = String(keyFn(left) || "");
        const rightKey = String(keyFn(right) || "");
        return leftKey.localeCompare(rightKey, "zh-CN", {numeric: true});
      });
    }

    function stableJson(value) {
      return JSON.stringify(value);
    }

    function resourceTags(resource) {
      const data = resource || {};
      const gpu = data.gpu || {};
      const tags = [
        `CPU ${cores(data.cpu_cores)}`,
        `内存 ${bytes(data.memory_bytes)}`,
        `任务盘 ${bytes(data.job_disk_bytes)}`,
        `数据盘 ${bytes(data.dataset_disk_bytes)}`,
      ];
      if (gpu.required || gpu.count || gpu.memory_bytes) {
        tags.push(`GPU ${gpu.count || 0} 张 / ${bytes(gpu.memory_bytes)}`);
      }
      return tags.map((item) => `<span class="tag">${esc(item)}</span>`).join("");
    }

    function resourcePercent(total, available) {
      const totalNumber = Number(total || 0);
      if (totalNumber <= 0) return 0;
      const availableNumber = Math.max(0, Number(available ?? totalNumber));
      const used = Math.max(0, totalNumber - availableNumber);
      return Math.min(100, Math.round((used / totalNumber) * 100));
    }

    function resourceRow(label, total, available, formatter) {
      const totalNumber = Number(total || 0);
      const availableNumber = Number(available ?? totalNumber);
      return `
        <div class="resource-row">
          <span>${esc(label)}</span>
          <div class="resource-track"><div class="resource-fill" style="width: ${esc(resourcePercent(totalNumber, availableNumber))}%"></div></div>
          <span>${esc(formatter(availableNumber))} / ${esc(formatter(totalNumber))}</span>
        </div>
      `;
    }

    function workerRawProfile(worker) {
      return worker.hardware_profile?.raw || worker.reported_hardware_profile?.raw || {};
    }

    function workerDiskTotal(worker, kind) {
      const raw = workerRawProfile(worker);
      const scheduler = worker.hardware_profile?.scheduler || {};
      const key = kind === "dataset" ? "dataset_disk" : "job_disk";
      const fallbackKey = kind === "dataset" ? "dataset_disk_bytes" : "job_disk_bytes";
      return raw[`${key}_total_bytes`] ?? scheduler[fallbackKey] ?? 0;
    }

    function workerDiskFree(worker, kind) {
      const raw = workerRawProfile(worker);
      const reported = worker.reported_capacity_state || worker.capacity_state || {};
      const key = kind === "dataset" ? "dataset_disk" : "job_disk";
      const fallbackKey = kind === "dataset" ? "dataset_disk_bytes" : "job_disk_bytes";
      return raw[`${key}_free_bytes`] ?? reported[fallbackKey] ?? workerDiskTotal(worker, kind);
    }

    function reserveSummary(reserve) {
      return `CPU ${cores(reserve?.cpu_cores)} · 内存 ${bytes(reserve?.memory_bytes)}`;
    }

    function reserveSyncLine(worker) {
      const reportedReserve = worker.reported_hardware_profile?.reserve;
      if (!reportedReserve) return "";
      return `<div class="muted">当前上报 ${esc(reserveSummary(reportedReserve))}，待心跳同步</div>`;
    }

    function workerDatasetRoots(worker) {
      const configured = Array.isArray(worker.configured_dataset_roots)
        ? worker.configured_dataset_roots
        : [];
      const runtime = worker.runtime_profile || {};
      if (configured.length) return configured;
      if (Array.isArray(runtime.dataset_roots) && runtime.dataset_roots.length) {
        return runtime.dataset_roots;
      }
      if (runtime.dataset_root) {
        return [{path: runtime.dataset_root, mode: "active"}];
      }
      return [];
    }

    function workerRootSummary(worker) {
      const roots = workerDatasetRoots(worker);
      if (!roots.length) return "-";
      const active = roots.find((root) => root.mode === "active") || roots[0];
      const suffix = roots.length > 1 ? ` +${roots.length - 1}` : "";
      return `${active.path || "-"}${suffix}`;
    }

    function workerEffectiveStatus(worker) {
      if (worker.needs_update) return "needs_update";
      return worker.online ? "online" : "offline";
    }

    function rootValidationFor(worker, path) {
      const checks = Array.isArray(worker?.dataset_root_checks)
        ? worker.dataset_root_checks
        : [];
      return checks.find((check) => check.path === path) || null;
    }

    function rootValidationStatus(worker, path) {
      if (!path) return "pending";
      const check = rootValidationFor(worker, path);
      return check?.status || "pending";
    }

    function rootValidationDetail(check) {
      if (!check) return "等待节点心跳";
      if (check.status === "ok") return `${bytes(check.free_bytes)} 可用`;
      if (check.status === "disabled") return "未参与调度";
      return check.error || "验证失败";
    }

    function rootValidationHtml(worker, path) {
      const check = rootValidationFor(worker, path);
      const status = check?.status || "pending";
      return `
        <div class="root-validation">
          ${state(status, rootValidationLabels)}
          <span>${esc(rootValidationDetail(check))}</span>
        </div>
      `;
    }

    function workerCard(worker) {
      const scheduler = worker.hardware_profile?.scheduler || {};
      const reserve = worker.hardware_profile?.reserve || {};
      const capacity = worker.capacity_state || {};
      const workerId = worker.worker_id || "";
      const concurrency = Number(worker.max_concurrent_tasks || 1);
      const active = Number(worker.active_task_count || 0);
      const runtime = worker.runtime_profile || {};
      const roots = workerDatasetRoots(worker);
      const activeRoot = roots.find((root) => root.mode === "active") || roots[0] || {};
      const runningReserve = worker.reserved_resources || {};
      return `
        <article class="worker-card">
          <div class="worker-card-head">
            <div class="worker-title">
              <div class="mono">${esc(worker.display_name || workerId)}</div>
            </div>
            <div class="worker-card-actions">
              ${state(workerEffectiveStatus(worker), workerStatusLabels)}
              <button class="icon-button" type="button" title="获取部署命令" aria-label="获取部署命令" data-open-worker-install-command="${esc(workerId)}">⇩</button>
              <button class="icon-button" type="button" title="设置" aria-label="设置节点" data-open-worker-settings="${esc(workerId)}">⚙</button>
            </div>
          </div>
          <div class="worker-stats">
            <div class="worker-stat"><span>并发</span><strong>${esc(active)} / ${esc(concurrency)}</strong></div>
            <div class="worker-stat"><span>系统保留</span><strong>${esc(reserveSummary(reserve))}</strong></div>
          </div>
          ${worker.needs_update ? `<div class="muted">worker ${esc(worker.worker_version || "未知版本")}，最低要求 ${esc(worker.minimum_worker_version || worker.required_version || "-")}</div>` : ""}
          ${reserveSyncLine(worker)}
          <div class="worker-paths">
            <div>任务盘 <span class="mono">${esc(runtime.job_root || "-")}</span></div>
            <div>数据盘 <span class="mono">${esc(workerRootSummary(worker))}</span></div>
            <div>数据盘验证 ${state(rootValidationStatus(worker, activeRoot.path), rootValidationLabels)}</div>
          </div>
          <div class="muted">可调度资源与硬盘状态（当前可用 / 上限）</div>
          ${(runningReserve.cpu_cores || runningReserve.memory_bytes) ? `<div class="muted">运行占用 CPU ${esc(cores(runningReserve.cpu_cores))} · 内存 ${esc(bytes(runningReserve.memory_bytes))}</div>` : ""}
          <div class="resource-list">
            ${resourceRow("CPU", scheduler.cpu_cores, capacity.cpu_cores, cores)}
            ${resourceRow("内存", scheduler.memory_bytes, capacity.memory_bytes, bytes)}
            ${resourceRow("任务盘", workerDiskTotal(worker, "job"), workerDiskFree(worker, "job"), bytes)}
            ${resourceRow("数据盘", workerDiskTotal(worker, "dataset"), workerDiskFree(worker, "dataset"), bytes)}
          </div>
        </article>
      `;
    }

    function parseTime(value) {
      if (!value) return null;
      const time = new Date(value).getTime();
      return Number.isNaN(time) ? null : time;
    }

    function durationSeconds(startMs, endMs) {
      if (startMs === null || endMs === null || endMs < startMs) return null;
      return (endMs - startMs) / 1000;
    }

    function durationLabel(seconds) {
      if (seconds === null) return "-";
      return `${seconds.toFixed(1)}s`;
    }

    function taskDurations(task) {
      const now = Date.now();
      const created = parseTime(task.created_at);
      const started = parseTime(task.started_at);
      const finished = parseTime(task.finished_at);
      const runtimeEnd = finished || (task.status === "running" ? now : null);
      const lifecycleEnd = finished || now;
      const runtime = durationSeconds(started, runtimeEnd);
      const lifecycle = durationSeconds(created, lifecycleEnd);
      return {
        runtime: durationLabel(runtime),
        lifecycle: durationLabel(lifecycle),
        combined: `${durationLabel(runtime)} / ${durationLabel(lifecycle)}`,
      };
    }

    function labelFor(value, labels) {
      return labels[value] || value || "-";
    }

    function state(value, labels) {
      const key = value || "idle";
      return `<span class="state ${esc(key)}">${esc(labelFor(key, labels))}</span>`;
    }

    function showNotice(message) {
      const notice = document.getElementById("notice");
      notice.textContent = message;
      notice.classList.add("show");
    }

    function clearNotice() {
      const notice = document.getElementById("notice");
      notice.textContent = "";
      notice.classList.remove("show");
    }

    function renderSummary(data) {
      const summary = data.summary || {};
      const workers = data.workers || [];
      const datasets = data.datasets || [];
      const caches = data.dataset_caches || [];
      const onlineWorkers = workers.filter((worker) => worker.online && !worker.needs_update).length;
      const metrics = [
        ["任务总数", summary.total || 0],
        ["待领取", summary.pending || 0],
        ["执行中", summary.running || 0],
        ["成功", summary.success || 0],
        ["失败", summary.failed || 0],
        ["节点在线", `${onlineWorkers}/${workers.length}`],
        ["维护数据", datasets.length],
        ["本地缓存", caches.length],
      ];
      document.getElementById("summary").innerHTML = metrics.map(([label, value]) => `
        <div class="metric">
          <strong>${esc(value)}</strong>
          <span>${esc(label)}</span>
        </div>
      `).join("");
    }

    function renderSummaryIfChanged(data) {
      const key = stableJson({
        summary: data.summary || {},
        workers: (data.workers || []).map((worker) => [
          worker.worker_id,
          worker.online,
          worker.needs_update,
          worker.worker_version,
          worker.required_version,
          worker.minimum_worker_version,
          worker.server_version,
        ]),
        datasets: (data.datasets || []).length,
        caches: (data.dataset_caches || []).length,
      });
      if (renderState.summary === key) return;
      renderState.summary = key;
      renderSummary(data);
    }

    function renderWorkers(workers, options = {}) {
      if (!options.force && isInteractiveBusy()) return;
      const onlineWorkers = workers.filter((worker) => worker.online && !worker.needs_update).length;
      document.getElementById("worker-count").textContent = `${onlineWorkers}/${workers.length} 在线`;
      document.getElementById("workers").innerHTML = workers.map(workerCard).join("")
        || `<div class="empty">暂无注册节点</div>`;
      document.querySelectorAll("[data-open-worker-settings]").forEach((button) => {
        button.addEventListener("click", () => {
          openWorkerSettings(button.dataset.openWorkerSettings);
        });
      });
      document.querySelectorAll("[data-open-worker-install-command]").forEach((button) => {
        button.addEventListener("click", () => {
          openWorkerInstallModal(button.dataset.openWorkerInstallCommand);
        });
      });
    }

    function renderWorkersIfChanged(workers, options = {}) {
      if (!options.force && isInteractiveBusy()) return;
      const sorted = stableSortByKey(workers || [], (worker) => worker.worker_id);
      const key = stableJson(sorted);
      if (!options.force && renderState.workers === key) return;
      renderState.workers = key;
      renderWorkers(sorted, options);
    }

    function refreshOpenWorkerSettingsValidation() {
      if (isWorkerSettingsOpen() && !workerSettingsState.dirty) {
        const worker = currentSettingsWorker();
        if (worker) renderRootEditors();
      }
    }

    function taskWorker(task) {
      return task.locked_by || task.result?.worker_id || "-";
    }

    function outputFileCard(file) {
      const download = file.stored_on_server && file.artifact_id
        ? `<a class="button-link" href="/api/admin/tasks/${esc(currentTaskId)}/artifacts/${esc(file.artifact_id)}/download">下载</a>`
        : "";
      const meaning = file.meaning || file.description || "暂无说明";
      return `
        <div class="artifact-card">
          <div class="primary-text">${esc(file.title || file.path)}</div>
          <div class="muted">${esc(file.path)} · ${esc(bytes(file.size_bytes))}</div>
          <div><strong>文件意义</strong> ${esc(meaning)}</div>
          ${file.sha256 ? `<div class="mono">${esc(file.sha256)}</div>` : ""}
          ${download}
        </div>
      `;
    }

    function artifactOutputFiles(task) {
      const resultFiles = Array.isArray(task.result?.output_files)
        ? task.result.output_files
        : [];
      const seen = new Set(resultFiles.map((file) => file.artifact_id || file.path));
      const artifactFiles = (Array.isArray(task.artifacts) ? task.artifacts : [])
        .filter((artifact) => !seen.has(artifact.id) && !seen.has(artifact.relative_path))
        .map((artifact) => ({
          path: artifact.relative_path || artifact.display_name || artifact.id,
          title: artifact.title || artifact.display_name,
          description: artifact.description,
          meaning: artifact.meaning,
          size_bytes: artifact.size_bytes,
          sha256: artifact.sha256,
          stored_on_server: artifact.status === "uploaded",
          artifact_id: artifact.id,
        }));
      return resultFiles.concat(artifactFiles);
    }

    function isWorkerSettingsOpen() {
      return document.getElementById("worker-settings-modal").classList.contains("show");
    }

    function isPasswordModalOpen() {
      return document.getElementById("password-modal").classList.contains("show");
    }

    function isWorkerInstallModalOpen() {
      return document.getElementById("worker-install-modal").classList.contains("show");
    }

    function isInteractiveBusy() {
      const active = document.activeElement;
      const editingElement = active && active.matches?.("input, textarea, select");
      return (
        workerSettingsState.open ||
        workerSettingsState.dirty ||
        passwordState.open ||
        passwordState.dirty ||
        workerInstallState.open ||
        workerInstallState.dirty ||
        Boolean(editingElement)
      );
    }

    function currentSettingsWorker() {
      return (latestOverview?.workers || []).find(
        (worker) => worker.worker_id === workerSettingsState.workerId
      );
    }

    function markWorkerSettingsDirty() {
      workerSettingsState.dirty = true;
    }

    function openWorkerSettings(workerId) {
      const worker = (latestOverview?.workers || []).find((item) => item.worker_id === workerId);
      if (!worker) return;
      workerSettingsState.open = true;
      workerSettingsState.dirty = false;
      workerSettingsState.workerId = workerId;
      workerSettingsState.roots = workerDatasetRoots(worker).map((root) => ({
        path: root.path || "",
        mode: root.mode || "active",
        label: root.label || "",
      }));
      if (!workerSettingsState.roots.length) {
        workerSettingsState.roots = [{path: "", mode: "active", label: ""}];
      }
      document.getElementById("worker-settings-title").textContent = `节点设置 ${worker.display_name || workerId}`;
      document.getElementById("worker-settings-concurrency").value = Number(worker.max_concurrent_tasks || 1);
      document.getElementById("worker-settings-job-root").value =
        worker.configured_job_root || worker.runtime_profile?.job_root || "";
      fillReserveInputs(worker);
      renderRootEditors();
      document.getElementById("worker-settings-modal").classList.add("show");
    }

    function currentReserveSettings(worker) {
      return worker.configured_reserve_overrides && Object.keys(worker.configured_reserve_overrides).length
        ? worker.configured_reserve_overrides
        : worker.hardware_profile?.reserve || {};
    }

    function fillReserveInputs(worker) {
      const reserve = currentReserveSettings(worker);
      document.getElementById("reserve-cpu").value = Number(reserve.cpu_cores || 0);
      document.getElementById("reserve-memory").value = bytesToGiB(reserve.memory_bytes);
      document.getElementById("reserve-job-disk").value = bytesToGiB(reserve.job_disk_bytes);
      document.getElementById("reserve-dataset-disk").value = bytesToGiB(reserve.dataset_disk_bytes);
      document.getElementById("reserve-gpu").value = bytesToGiB(reserve.gpu_memory_bytes);
    }

    function collectReserveOverrides() {
      return {
        cpu_cores: Number(document.getElementById("reserve-cpu").value || 0),
        memory_bytes: giBToBytes(document.getElementById("reserve-memory").value),
        job_disk_bytes: giBToBytes(document.getElementById("reserve-job-disk").value),
        dataset_disk_bytes: giBToBytes(document.getElementById("reserve-dataset-disk").value),
        gpu_memory_bytes: giBToBytes(document.getElementById("reserve-gpu").value),
      };
    }

    function renderRootEditors() {
      const container = document.getElementById("worker-settings-roots");
      const worker = currentSettingsWorker();
      container.innerHTML = workerSettingsState.roots.map((root, index) => `
        <div class="root-editor" data-root-row="${esc(index)}">
          <input type="text" spellcheck="false" aria-label="数据盘路径" value="${esc(root.path || "")}" data-root-field="path" data-root-index="${esc(index)}">
          <select aria-label="数据盘模式" data-root-field="mode" data-root-index="${esc(index)}">
            <option value="active" ${root.mode === "active" ? "selected" : ""}>活动</option>
            <option value="readonly" ${root.mode === "readonly" ? "selected" : ""}>只读</option>
            <option value="disabled" ${root.mode === "disabled" ? "selected" : ""}>停用</option>
          </select>
          <input type="text" spellcheck="false" aria-label="数据盘名称" value="${esc(root.label || "")}" data-root-field="label" data-root-index="${esc(index)}">
          ${rootValidationHtml(worker, root.path)}
          <button class="icon-button danger" type="button" title="移除" aria-label="移除数据盘" data-remove-root="${esc(index)}">×</button>
        </div>
      `).join("");
      container.querySelectorAll("[data-root-field]").forEach((field) => {
        field.addEventListener("input", updateRootField);
        field.addEventListener("change", updateRootField);
      });
      container.querySelectorAll("[data-remove-root]").forEach((button) => {
        button.addEventListener("click", () => {
          const index = Number(button.dataset.removeRoot);
          workerSettingsState.roots.splice(index, 1);
          if (!workerSettingsState.roots.length) {
            workerSettingsState.roots.push({path: "", mode: "active", label: ""});
          }
          ensureSingleActiveRoot();
          markWorkerSettingsDirty();
          renderRootEditors();
        });
      });
    }

    function updateRootField(event) {
      const input = event.currentTarget;
      const index = Number(input.dataset.rootIndex);
      const field = input.dataset.rootField;
      if (!workerSettingsState.roots[index]) return;
      workerSettingsState.roots[index][field] = input.value;
      if (field === "mode") ensureSingleActiveRoot(index);
      markWorkerSettingsDirty();
      if (field === "mode") renderRootEditors();
    }

    function ensureSingleActiveRoot(activeIndex = null) {
      let activeSeen = false;
      workerSettingsState.roots = workerSettingsState.roots.map((root, index) => {
        const next = {...root};
        if (next.mode === "active") {
          if (activeSeen || (activeIndex !== null && index !== activeIndex)) {
            next.mode = "readonly";
          } else {
            activeSeen = true;
          }
        }
        return next;
      });
      if (!activeSeen) {
        const firstUsable = workerSettingsState.roots.find((root) => root.mode !== "disabled");
        if (firstUsable) firstUsable.mode = "active";
      }
    }

    function closeWorkerSettings(force = false) {
      if (!force && workerSettingsState.dirty && !confirm("放弃尚未保存的节点设置？")) return;
      document.getElementById("worker-settings-modal").classList.remove("show");
      workerSettingsState.open = false;
      workerSettingsState.dirty = false;
      workerSettingsState.workerId = null;
      workerSettingsState.roots = [];
    }

    async function saveWorkerSettings() {
      const workerId = workerSettingsState.workerId;
      const concurrency = Number(document.getElementById("worker-settings-concurrency").value || 0);
      const jobRoot = document.getElementById("worker-settings-job-root").value.trim();
      const roots = workerSettingsState.roots
        .map((root) => ({
          path: String(root.path || "").trim(),
          mode: root.mode || "active",
          label: String(root.label || "").trim(),
        }))
        .filter((root) => root.path);
      if (!workerId || !Number.isInteger(concurrency) || concurrency < 1) {
        alert("最大并发必须是大于 0 的整数");
        return;
      }
      if (!roots.length) {
        alert("至少需要保留一个数据盘");
        return;
      }
      const button = document.getElementById("worker-settings-save");
      button.disabled = true;
      try {
        const response = await fetch(`/api/admin/workers/${encodeURIComponent(workerId)}/settings`, {
          method: "PATCH",
          credentials: "same-origin",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            max_concurrent_tasks: concurrency,
            job_root: jobRoot,
            dataset_roots: roots,
            reserve_overrides: collectReserveOverrides(),
          }),
        });
        if (!response.ok) throw new Error(await response.text());
        closeWorkerSettings(true);
        await refresh({forceWorkers: true});
      } catch (error) {
        showNotice(`保存节点设置失败：${error.message}`);
      } finally {
        button.disabled = false;
      }
    }

    function openPasswordModal() {
      passwordState.open = true;
      passwordState.dirty = false;
      document.getElementById("password-current").value = "";
      document.getElementById("password-new").value = "";
      document.getElementById("password-confirm").value = "";
      document.getElementById("password-modal").classList.add("show");
      document.getElementById("password-current").focus();
    }

    function closePasswordModal(force = false) {
      if (!force && passwordState.dirty && !confirm("放弃尚未保存的新密码？")) return;
      document.getElementById("password-modal").classList.remove("show");
      passwordState.open = false;
      passwordState.dirty = false;
    }

    function markPasswordDirty() {
      passwordState.dirty = true;
    }

    async function saveAdminPassword() {
      const currentPassword = document.getElementById("password-current").value;
      const newPassword = document.getElementById("password-new").value;
      const confirmPassword = document.getElementById("password-confirm").value;
      if (newPassword.length < 8) {
        alert("新密码至少需要 8 位");
        return;
      }
      if (newPassword !== confirmPassword) {
        alert("两次输入的新密码不一致");
        return;
      }
      const button = document.getElementById("password-save");
      button.disabled = true;
      try {
        const response = await fetch("/api/admin/password", {
          method: "POST",
          credentials: "same-origin",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            current_password: currentPassword,
            new_password: newPassword,
            confirm_password: confirmPassword,
          }),
        });
        if (!response.ok) throw new Error(await response.text());
        closePasswordModal(true);
        showNotice("密码已修改，请刷新页面后使用新密码重新登录。");
      } catch (error) {
        showNotice(`修改密码失败：${error.message}`);
      } finally {
        button.disabled = false;
      }
    }

    function inferWorkerInstallPlatform(worker) {
      const hints = [
        worker?.install_platform,
        worker?.runtime_profile?.install_platform,
        worker?.runtime_profile?.system,
        worker?.runtime_profile?.platform,
      ].map((value) => String(value || "").toLowerCase());
      for (const hint of hints) {
        if (!hint) continue;
        if (hint === "macos" || hint === "darwin" || hint.includes("macos")) return "macos";
        if (hint === "linux" || hint.startsWith("linux-")) return "linux";
        if (hint === "windows" || hint.startsWith("windows-") || hint.includes("microsoft windows")) return "linux";
      }
      return "macos";
    }

    function openWorkerInstallModal(workerId = "") {
      workerInstallState.open = true;
      workerInstallState.dirty = false;
      const worker = workerId
        ? (latestOverview?.workers || []).find((item) => item.worker_id === workerId)
        : null;
      document.getElementById("worker-install-title").textContent = worker
        ? `更新本地算力节点 ${worker.display_name || workerId}`
        : "添加本地算力节点";
      document.getElementById("worker-install-platform").value = worker
        ? inferWorkerInstallPlatform(worker)
        : "macos";
      document.getElementById("worker-install-id").value = worker?.worker_id || "";
      document.getElementById("worker-install-name").value = worker?.display_name || worker?.worker_id || "";
      document.getElementById("worker-install-command").value = "";
      document.getElementById("worker-install-meta").textContent = "";
      document.getElementById("worker-install-modal").classList.add("show");
      document.getElementById("worker-install-generate").focus();
    }

    function closeWorkerInstallModal(force = false) {
      if (!force && workerInstallState.dirty && !confirm("关闭后需要重新生成安装命令，确认关闭？")) return;
      document.getElementById("worker-install-modal").classList.remove("show");
      workerInstallState.open = false;
      workerInstallState.dirty = false;
    }

    function markWorkerInstallDirty() {
      workerInstallState.dirty = true;
      document.getElementById("worker-install-command").value = "";
      document.getElementById("worker-install-meta").textContent = "";
    }

    async function generateWorkerInstallCommand() {
      const platform = document.getElementById("worker-install-platform").value;
      const workerId = document.getElementById("worker-install-id").value.trim();
      const displayName = document.getElementById("worker-install-name").value.trim();
      const button = document.getElementById("worker-install-generate");
      if (!workerId) {
        showNotice("请先填写节点 ID，再生成安装命令。");
        document.getElementById("worker-install-id").focus();
        return;
      }
      button.disabled = true;
      try {
        const response = await fetch("/api/admin/worker-install-invites", {
          method: "POST",
          credentials: "same-origin",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            platform,
            worker_id: workerId || null,
            display_name: displayName || null,
          }),
        });
        if (!response.ok) throw new Error(await response.text());
        const invite = await response.json();
        document.getElementById("worker-install-command").value = invite.command;
        document.getElementById("worker-install-meta").textContent =
          `节点 ${invite.worker_id} · 有效期至 ${fmt(invite.expires_at)}`;
        workerInstallState.dirty = false;
      } catch (error) {
        showNotice(`生成安装命令失败：${error.message}`);
      } finally {
        button.disabled = false;
      }
    }

    async function copyWorkerInstallCommand() {
      const command = document.getElementById("worker-install-command").value.trim();
      if (!command) {
        alert("请先生成安装命令");
        return;
      }
      try {
        await navigator.clipboard.writeText(command);
        showNotice("安装命令已复制。");
      } catch {
        document.getElementById("worker-install-command").select();
        showNotice("已选中安装命令，可以手动复制。");
      }
    }

    function renderTasks(tasks) {
      const tbody = document.getElementById("tasks");
      document.getElementById("task-count").textContent = `${tasks.length} 个任务`;
      if (!tasks.length) {
        currentTaskId = null;
        tbody.innerHTML = `<tr><td colspan="7"><div class="empty">暂无任务记录</div></td></tr>`;
        if (isTaskModalOpen()) closeTaskModal();
        return;
      }
      tbody.innerHTML = tasks.map((task) => `
        <tr data-task="${esc(task.id)}">
          <td>
            <div class="primary-text mono">${esc(shortId(task.id))}</div>
            <div class="muted">重试 ${esc(task.retry_count || 0)}</div>
          </td>
          <td>${state(task.status, taskStatusLabels)}</td>
          <td>${esc(task.type || "-")}</td>
          <td>${esc(taskWorker(task))}</td>
          <td>${esc(taskDurations(task).combined)}</td>
          <td>${esc(fmt(task.updated_at))}</td>
          <td><button type="button" data-view-task="${esc(task.id)}">详情</button></td>
        </tr>
      `).join("");

      tbody.querySelectorAll("[data-view-task]").forEach((button) => {
        button.addEventListener("click", (event) => {
          event.stopPropagation();
          openTaskDetails(button.dataset.viewTask);
        });
      });

      if (isTaskModalOpen()) {
        const taskExists = tasks.some((item) => item.id === currentTaskId);
        if (!taskExists) closeTaskModal();
      }
    }

    function renderTasksIfChanged(tasks) {
      const key = stableJson(tasks || []);
      if (renderState.tasks === key) return;
      renderState.tasks = key;
      renderTasks(tasks || []);
    }

    function taskDetailHtml(task) {
      const result = task.result || {};
      const taskContext = task.payload?.task_context || {};
      const outputFiles = artifactOutputFiles(task);
      const datasets = Array.isArray(result.datasets) ? result.datasets : [];
      const payloadDatasets = Array.isArray(task.payload?.datasets) ? task.payload.datasets : [];
      const summary = result.summary || task.error || "暂无结果摘要";
      const stdout = result.stdout || "";
      const durations = taskDurations(task);
      const resourceRequest = task.resource_request || task.payload?.resource_request || {};
      return `
        <div class="detail-grid">
          <div class="kv"><span>任务 ID</span><strong class="mono">${esc(task.id)}</strong></div>
          <div class="kv"><span>状态</span><strong>${state(task.status, taskStatusLabels)}</strong></div>
          <div class="kv"><span>类型</span><strong>${esc(task.type || "-")}</strong></div>
          <div class="kv"><span>执行节点</span><strong>${esc(taskWorker(task))}</strong></div>
          <div class="kv"><span>脚本运行耗时</span><strong>${esc(durations.runtime)}</strong></div>
          <div class="kv"><span>完整生命周期耗时</span><strong>${esc(durations.lifecycle)}</strong></div>
          <div class="kv"><span>开始时间</span><strong>${esc(fmt(task.started_at))}</strong></div>
          <div class="kv"><span>结束时间</span><strong>${esc(fmt(task.finished_at))}</strong></div>
        </div>
        <div class="result-line">
          <h3>任务说明</h3>
          <div class="primary-text">${esc(task.title || "未命名任务")}</div>
          <div class="muted">${esc(task.description || taskContext.analysis_goal || "暂无说明")}</div>
        </div>
        <div class="result-line">
          <h3>执行结果</h3>
          <div>${esc(summary)}</div>
        </div>
        <div class="result-line">
          <h3>资源申请</h3>
          <div class="tags">${resourceTags(resourceRequest)}</div>
          ${task.resource_reservation ? `<div class="muted">已预留给 ${esc(task.locked_by || "-")}</div>` : ""}
        </div>
        ${stdout ? `<div class="result-line"><h3>标准输出</h3><pre>${esc(stdout)}</pre></div>` : ""}
        ${outputFiles.length ? `<div class="result-line"><h3>结果文件</h3><div class="artifact-list">${outputFiles.map(outputFileCard).join("")}</div></div>` : ""}
        ${datasets.length || payloadDatasets.length ? `<div class="result-line"><h3>数据依赖</h3><div class="tags">${(datasets.length ? datasets : payloadDatasets).map((dataset) => `<span class="tag">${esc(dataset.mount_name || "-")} · ${esc(dataset.dataset_name || shortId(dataset.dataset_version_id))}</span>`).join("")}</div></div>` : ""}
        <details>
          <summary>原始任务数据</summary>
          <pre>${esc(JSON.stringify(task, null, 2))}</pre>
        </details>
      `;
    }

    function fillTaskModal(task) {
      currentTaskId = task.id;
      document.getElementById("task-modal-title").textContent = `任务详情 ${shortId(task.id)}`;
      document.getElementById("task-modal-body").innerHTML = taskDetailHtml(task);
    }

    async function openTaskDetails(taskId) {
      currentTaskId = taskId;
      document.getElementById("task-modal-title").textContent = `任务详情 ${shortId(taskId)}`;
      document.getElementById("task-modal-body").innerHTML = `<div class="empty">正在载入任务详情</div>`;
      document.getElementById("task-modal").classList.add("show");
      try {
        const response = await fetch(`/api/admin/tasks/${encodeURIComponent(taskId)}`, {
          credentials: "same-origin",
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        fillTaskModal(await response.json());
      } catch (error) {
        if (currentTaskId === taskId) {
          document.getElementById("task-modal-body").innerHTML =
            `<div class="empty">任务详情载入失败：${esc(error.message)}</div>`;
        }
      }
    }

    function closeTaskModal() {
      document.getElementById("task-modal").classList.remove("show");
      document.getElementById("task-modal-body").innerHTML = "";
      currentTaskId = null;
    }

    function isTaskModalOpen() {
      return document.getElementById("task-modal").classList.contains("show");
    }

    function renderDatasets(datasets) {
      document.getElementById("dataset-count").textContent = `${datasets.length} 份数据`;
      document.getElementById("datasets").innerHTML = datasets.map((dataset) => `
        <tr>
          <td>
            <div class="primary-text mono">${esc(dataset.dataset_name || dataset.id)}</div>
            <div class="muted">${esc(dataset.dataset_title || "")}</div>
          </td>
          <td>${esc(dataset.version)}</td>
          <td>${esc(labelFor(dataset.source_kind, sourceKindLabels))}</td>
          <td>${esc(bytes(dataset.size_bytes))}</td>
          <td>
            <div class="path-cell mono">${esc(dataset.server_path)}</div>
            ${dataset.original_path ? `<div class="muted mono">来源 ${esc(dataset.original_path)}</div>` : ""}
          </td>
          <td>
            <div class="row-actions">
              <button class="danger" data-delete-dataset="${esc(dataset.id)}" type="button">删除服务器数据</button>
            </div>
          </td>
        </tr>
      `).join("") || `<tr><td colspan="6"><div class="empty">暂无服务器维护数据</div></td></tr>`;

      document.querySelectorAll("[data-delete-dataset]").forEach((button) => {
        button.addEventListener("click", async () => {
          const id = button.dataset.deleteDataset;
          if (!confirm(`确认删除服务器维护的数据版本 ${id}？请先清理所有本地节点缓存。`)) return;
          const response = await fetch(`/api/admin/datasets/${encodeURIComponent(id)}`, {
            method: "DELETE",
            credentials: "same-origin",
          });
          if (!response.ok) alert(await response.text());
          await refresh();
        });
      });
    }

    function renderDatasetsIfChanged(datasets) {
      const sorted = stableSortByKey(
        datasets || [],
        (dataset) => `${dataset.dataset_name || ""}|${dataset.version || ""}|${dataset.id || ""}`,
      );
      const key = stableJson(sorted);
      if (renderState.datasets === key) return;
      renderState.datasets = key;
      renderDatasets(sorted);
    }

    function renderDatasetCaches(caches) {
      document.getElementById("cache-count").textContent = `${caches.length} 条缓存`;
      document.getElementById("dataset-caches").innerHTML = caches.map((cache) => {
        const totalSize = Number(cache.size_bytes || 0) + Number(cache.extracted_size_bytes || 0);
        const canDelete = cache.status !== "deleted";
        return `
          <tr>
            <td>
              <div class="primary-text mono">${esc(cache.worker_id)}</div>
              <div class="muted">${fmt(cache.updated_at)}</div>
            </td>
            <td>
              <div>${esc(cache.dataset_name || shortId(cache.dataset_version_id))}</div>
              <div class="muted">${esc(cache.dataset_version || "")}</div>
            </td>
            <td>${state(cache.status, cacheStatusLabels)}</td>
            <td>${esc(bytes(totalSize))}</td>
            <td>
              <div class="path-cell mono">${esc(cache.local_extracted_path || cache.local_archive_path || "-")}</div>
              ${cache.data_root_path ? `<div class="muted mono">数据盘 ${esc(cache.data_root_path)}</div>` : ""}
              ${cache.last_error ? `<div class="state failed">${esc(cache.last_error)}</div>` : ""}
            </td>
            <td>
              <div class="row-actions">
                <button class="danger" data-delete-cache="${esc(cache.dataset_version_id)}" data-worker="${esc(cache.worker_id)}" type="button" ${canDelete ? "" : "disabled"}>删除本地缓存</button>
              </div>
            </td>
          </tr>
        `;
      }).join("") || `<tr><td colspan="6"><div class="empty">暂无节点数据缓存</div></td></tr>`;

      document.querySelectorAll("[data-delete-cache]").forEach((button) => {
        button.addEventListener("click", async () => {
          const id = button.dataset.deleteCache;
          const worker = button.dataset.worker;
          if (!confirm(`确认通知 ${worker} 删除本地缓存 ${id}？`)) return;
          const response = await fetch(`/api/admin/datasets/${encodeURIComponent(id)}/worker-delete`, {
            method: "POST",
            credentials: "same-origin",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({worker_id: worker}),
          });
          if (!response.ok) alert(await response.text());
          await refresh();
        });
      });
    }

    function renderDatasetCachesIfChanged(caches) {
      const sorted = stableSortByKey(
        caches || [],
        (cache) => `${cache.worker_id || ""}|${cache.dataset_name || ""}|${cache.dataset_version || ""}|${cache.dataset_version_id || ""}`,
      );
      const key = stableJson(sorted);
      if (renderState.datasetCaches === key) return;
      renderState.datasetCaches = key;
      renderDatasetCaches(sorted);
    }

    async function refresh(options = {}) {
      const headers = {};
      if (overviewSectionEtags) {
        headers["X-Cloudlink-Section-Etags"] = JSON.stringify(overviewSectionEtags);
      }
      if (renderState.overviewEtag) {
        headers["If-None-Match"] = renderState.overviewEtag;
      }
      const response = await fetch("/api/admin/overview", {
        credentials: "same-origin",
        headers,
      });
      if (response.status === 304) {
        clearNotice();
        document.getElementById("updated").textContent = `最后检查 ${new Date().toLocaleTimeString("zh-CN", {hour12: false})}`;
        return;
      }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      renderState.overviewEtag = response.headers.get("ETag") || "";
      overviewSectionEtags = data.section_etags || overviewSectionEtags;
      latestOverview = {...(latestOverview || {}), ...data};
      clearNotice();
      const summaryInputsChanged = ["summary", "workers", "datasets", "dataset_caches"]
        .some((section) => section in data);
      if (summaryInputsChanged) renderSummaryIfChanged(latestOverview);
      if ("tasks" in data) renderTasksIfChanged(latestOverview.tasks || []);
      if ("workers" in data) {
        renderWorkersIfChanged(latestOverview.workers || [], {force: Boolean(options.forceWorkers)});
        refreshOpenWorkerSettingsValidation();
      }
      if ("datasets" in data) renderDatasetsIfChanged(latestOverview.datasets || []);
      if ("dataset_caches" in data) renderDatasetCachesIfChanged(latestOverview.dataset_caches || []);
      document.getElementById("updated").textContent = `最后更新 ${new Date().toLocaleTimeString("zh-CN", {hour12: false})}`;
    }

    document.getElementById("refresh").addEventListener("click", () => {
      refresh({forceWorkers: true}).catch((error) => showNotice(`刷新失败：${error.message}`));
    });
    document.getElementById("open-password-modal").addEventListener("click", openPasswordModal);
    document.getElementById("open-worker-install-modal").addEventListener("click", openWorkerInstallModal);
    document.getElementById("task-modal-close").addEventListener("click", closeTaskModal);
    document.getElementById("task-modal").addEventListener("click", (event) => {
      if (event.target.id === "task-modal") closeTaskModal();
    });
    document.getElementById("worker-settings-close").addEventListener("click", () => closeWorkerSettings());
    document.getElementById("worker-settings-cancel").addEventListener("click", () => closeWorkerSettings());
    document.getElementById("worker-settings-save").addEventListener("click", saveWorkerSettings);
    document.getElementById("worker-settings-add-root").addEventListener("click", () => {
      workerSettingsState.roots.push({path: "", mode: "readonly", label: ""});
      markWorkerSettingsDirty();
      renderRootEditors();
    });
    document.getElementById("worker-settings-concurrency").addEventListener("input", markWorkerSettingsDirty);
    document.getElementById("worker-settings-job-root").addEventListener("input", markWorkerSettingsDirty);
    ["reserve-cpu", "reserve-memory", "reserve-job-disk", "reserve-dataset-disk", "reserve-gpu"].forEach((id) => {
      document.getElementById(id).addEventListener("input", markWorkerSettingsDirty);
    });
    document.getElementById("worker-settings-modal").addEventListener("click", (event) => {
      if (event.target.id === "worker-settings-modal") closeWorkerSettings();
    });
    document.getElementById("password-modal-close").addEventListener("click", () => closePasswordModal());
    document.getElementById("password-modal-cancel").addEventListener("click", () => closePasswordModal());
    document.getElementById("password-save").addEventListener("click", saveAdminPassword);
    ["password-current", "password-new", "password-confirm"].forEach((id) => {
      document.getElementById(id).addEventListener("input", markPasswordDirty);
    });
    document.getElementById("password-modal").addEventListener("click", (event) => {
      if (event.target.id === "password-modal") closePasswordModal();
    });
    document.getElementById("worker-install-close").addEventListener("click", () => closeWorkerInstallModal());
    document.getElementById("worker-install-generate").addEventListener("click", generateWorkerInstallCommand);
    document.getElementById("worker-install-copy").addEventListener("click", copyWorkerInstallCommand);
    ["worker-install-platform", "worker-install-id", "worker-install-name"].forEach((id) => {
      document.getElementById(id).addEventListener("input", markWorkerInstallDirty);
      document.getElementById(id).addEventListener("change", markWorkerInstallDirty);
    });
    document.getElementById("worker-install-modal").addEventListener("click", (event) => {
      if (event.target.id === "worker-install-modal") closeWorkerInstallModal();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && isTaskModalOpen()) closeTaskModal();
      else if (event.key === "Escape" && isWorkerSettingsOpen()) closeWorkerSettings();
      else if (event.key === "Escape" && isPasswordModalOpen()) closePasswordModal();
      else if (event.key === "Escape" && isWorkerInstallModalOpen()) closeWorkerInstallModal();
    });
    refresh().catch((error) => {
      document.getElementById("updated").textContent = "连接失败";
      showNotice(`载入失败：${error.message}`);
    });
    let refreshTimer = null;
    function refreshDelayMs() {
      return document.hidden ? 30000 : 5000;
    }
    function scheduleRefresh() {
      if (refreshTimer) clearTimeout(refreshTimer);
      refreshTimer = setTimeout(async () => {
        try {
          await refresh();
        } catch (error) {
          showNotice(`刷新失败：${error.message}`);
        } finally {
          scheduleRefresh();
        }
      }, refreshDelayMs());
    }
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        refresh().catch((error) => showNotice(`刷新失败：${error.message}`));
      }
      scheduleRefresh();
    });
    scheduleRefresh();
  </script>
</body>
</html>"""
