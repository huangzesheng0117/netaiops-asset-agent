def render_index_html() -> str:
    return r'''
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>网络信息统一查询入口</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root {
      --bg: #ffffff;
      --side: #f7f7f8;
      --panel: #ffffff;
      --text: #111111;
      --muted: #6b7280;
      --muted2: #9ca3af;
      --line: #e5e7eb;
      --line2: #d1d5db;
      --black: #111111;
      --hover: #f3f4f6;
      --active: #ececec;
      --danger: #b91c1c;
      --radius: 14px;
      --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      --sans: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
    }

    * {
      box-sizing: border-box;
    }

    html, body {
      margin: 0;
      width: 100%;
      height: 100%;
      overflow: hidden;
      background: var(--bg);
      color: var(--text);
      font-family: var(--sans);
      font-size: 14px;
    }

    button, textarea, input {
      font: inherit;
    }

    .layout {
      display: grid;
      grid-template-columns: 280px minmax(0, 1fr);
      width: 100vw;
      height: 100vh;
      height: 100dvh;
      overflow: hidden;
      background: var(--bg);
    }

    .sidebar {
      height: 100%;
      min-height: 0;
      overflow: hidden;
      border-right: 1px solid var(--line);
      background: var(--side);
      display: flex;
      flex-direction: column;
      padding: 12px;
    }

    .brand {
      height: 44px;
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 0 8px;
      font-weight: 700;
      letter-spacing: .01em;
    }

    .brand-mark {
      width: 22px;
      height: 22px;
      border-radius: 6px;
      background: var(--black);
      display: inline-block;
    }

    .new-btn {
      height: 42px;
      width: 100%;
      margin: 8px 0 12px;
      border: 1px solid var(--line2);
      background: var(--panel);
      color: var(--text);
      border-radius: 10px;
      cursor: pointer;
      font-weight: 600;
      text-align: left;
      padding: 0 14px;
    }

    .new-btn:hover {
      background: var(--hover);
    }

    .history-title {
      color: var(--muted);
      font-size: 12px;
      padding: 8px 8px 6px;
    }

    .conv-list {
      flex: 1;
      min-height: 0;
      overflow-y: auto;
      padding-right: 2px;
    }

    .conv-item {
      position: relative;
      border-radius: 10px;
      padding: 10px 34px 10px 10px;
      margin-bottom: 4px;
      cursor: pointer;
      color: var(--text);
      border: 1px solid transparent;
      background: transparent;
    }

    .conv-item:hover {
      background: var(--hover);
    }

    .conv-item.active {
      background: var(--active);
      border-color: var(--line2);
    }

    .conv-title {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      line-height: 1.4;
    }

    .conv-meta {
      color: var(--muted);
      font-size: 12px;
      margin-top: 4px;
    }

    .delete-conv {
      display: none;
      position: absolute;
      right: 8px;
      top: 8px;
      width: 22px;
      height: 22px;
      border: 0;
      border-radius: 6px;
      background: transparent;
      color: var(--muted);
      cursor: pointer;
      padding: 0;
      line-height: 20px;
      text-align: center;
      font-size: 17px;
    }

    .conv-item:hover .delete-conv {
      display: block;
    }

    .delete-conv:hover {
      background: #e5e7eb;
      color: var(--danger);
    }

    .main {
      height: 100%;
      min-width: 0;
      min-height: 0;
      display: flex;
      flex-direction: column;
      background: var(--bg);
    }

    .topbar {
      height: 58px;
      flex: 0 0 auto;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 24px;
      background: var(--bg);
    }

    .title {
      margin: 0;
      font-size: 18px;
      font-weight: 700;
      letter-spacing: .01em;
    }

    .status-bar {
      display: flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      font-size: 12px;
    }

    .pill {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 5px 10px;
      background: var(--panel);
      white-space: nowrap;
    }

    .chat-shell {
      flex: 1;
      min-height: 0;
      display: flex;
      flex-direction: column;
      background: var(--bg);
    }

    .conversation {
      flex: 1;
      min-height: 0;
      overflow-y: auto;
      padding: 28px 0;
      scroll-behavior: smooth;
    }

    .empty {
      height: 100%;
      min-height: 320px;
      display: grid;
      place-items: center;
      text-align: center;
      color: var(--muted);
      padding: 0 24px;
    }

    .empty-title {
      font-size: 18px;
      font-weight: 700;
      color: var(--text);
      margin-bottom: 10px;
    }

    .turn {
      max-width: 980px;
      margin: 0 auto 22px;
      padding: 0 24px;
    }

    .bubble {
      line-height: 1.75;
      word-break: break-word;
    }

    .bubble.user {
      width: fit-content;
      max-width: 78%;
      margin-left: auto;
      background: var(--black);
      color: #ffffff;
      border-radius: 18px;
      padding: 11px 15px;
    }

    .bubble.assistant {
      margin-top: 12px;
      color: var(--text);
      padding: 0;
    }

    .answer {
      white-space: pre-wrap;
      font-size: 15px;
    }

    .meta {
      margin-top: 8px;
      color: var(--muted2);
      font-size: 12px;
      line-height: 1.7;
      word-break: break-all;
      font-family: var(--mono);
    }

    .meta code {
      font-family: var(--mono);
      color: var(--muted);
      background: #f3f4f6;
      padding: 2px 5px;
      border-radius: 5px;
    }

    .result-actions {
      margin-top: 12px;
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }

    .table-wrap {
      margin-top: 12px;
      border: 1px solid var(--line);
      border-radius: 12px;
      overflow: auto;
      max-height: 420px;
      background: var(--panel);
    }

    table {
      border-collapse: collapse;
      min-width: 820px;
      width: 100%;
      font-size: 13px;
    }

    th, td {
      border-bottom: 1px solid var(--line);
      padding: 10px 12px;
      text-align: left;
      vertical-align: top;
      white-space: nowrap;
    }

    th {
      position: sticky;
      top: 0;
      background: #fafafa;
      z-index: 1;
      font-weight: 700;
    }

    th span {
      display: block;
      margin-top: 2px;
      color: var(--muted2);
      font-family: var(--mono);
      font-weight: 400;
      font-size: 11px;
    }

    tr:hover td {
      background: #fafafa;
    }

    .input-area {
      flex: 0 0 auto;
      border-top: 1px solid var(--line);
      background: var(--bg);
      padding: 16px 24px 18px;
    }

    .composer-wrap {
      max-width: 980px;
      margin: 0 auto;
    }

    .composer {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: end;
    }

    textarea {
      width: 100%;
      min-height: 52px;
      max-height: 180px;
      resize: vertical;
      border: 1px solid var(--line2);
      border-radius: 16px;
      padding: 14px 15px;
      outline: none;
      color: var(--text);
      background: var(--panel);
      line-height: 1.6;
    }

    textarea:focus {
      border-color: var(--black);
      box-shadow: 0 0 0 2px rgba(0, 0, 0, .08);
    }

    textarea::placeholder {
      color: var(--muted2);
    }

    .send-wrap {
      display: flex;
      gap: 8px;
      align-items: center;
    }

    button {
      border: 1px solid var(--black);
      background: var(--black);
      color: #ffffff;
      border-radius: 12px;
      padding: 10px 16px;
      cursor: pointer;
      font-weight: 600;
      white-space: nowrap;
    }

    button:hover {
      opacity: .88;
    }

    button.secondary {
      background: var(--panel);
      color: var(--text);
      border: 1px solid var(--line2);
    }

    button.secondary:hover {
      background: var(--hover);
      opacity: 1;
    }

    .toolbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-top: 10px;
      color: var(--muted);
      font-size: 12px;
      flex-wrap: wrap;
    }

    .tools {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }

    input {
      width: 64px;
      border: 1px solid var(--line2);
      border-radius: 10px;
      padding: 7px 8px;
      outline: none;
      text-align: center;
      background: var(--panel);
      color: var(--text);
    }

    .kbd {
      display: inline-block;
      border: 1px solid var(--line2);
      border-radius: 5px;
      padding: 1px 5px;
      color: var(--muted);
      background: #f9fafb;
      font-family: var(--mono);
      font-size: 11px;
    }

    .loading {
      display: none;
      color: var(--muted);
      font-size: 13px;
      margin-top: 10px;
    }

    .error {
      display: none;
      margin-top: 10px;
      color: var(--danger);
      background: #fff5f5;
      border: 1px solid #fecaca;
      border-radius: 10px;
      padding: 10px 12px;
      line-height: 1.6;
      font-size: 13px;
    }

    @media (max-width: 860px) {
      .layout {
        grid-template-columns: 1fr;
      }

      .sidebar {
        display: none;
      }

      .topbar {
        padding: 0 16px;
      }

      .status-bar {
        display: none;
      }

      .turn {
        padding: 0 16px;
      }

      .input-area {
        padding: 12px 16px 14px;
      }

      .composer {
        grid-template-columns: 1fr;
      }

      .send-wrap button {
        flex: 1;
      }

      .bubble.user {
        max-width: 92%;
      }
    }
  </style>
</head>
<body>
  <div class="layout">
    <aside class="sidebar">
      <div class="brand">
        <span class="brand-mark"></span>
        <span>NetAIOps</span>
      </div>
      <button class="new-btn" onclick="newConversation()">＋ 新建查询</button>
      <div class="history-title">最近对话</div>
      <div class="conv-list" id="conversationList"></div>
    </aside>

    <section class="main">
      <header class="topbar">
        <h1 class="title">网络信息统一查询入口</h1>
        <div class="status-bar">
          <span class="pill">CMDB Online</span>
          <span class="pill">Read only</span>
          <span class="pill">Conversation History</span>
        </div>
      </header>

      <main class="chat-shell">
        <div class="conversation" id="conversation">
          <div class="empty" id="emptyState">
            <div>
              <div class="empty-title">新对话</div>
              <div>输入一句话查询网络设备资产</div>
            </div>
          </div>
        </div>

        <div class="input-area">
          <div class="composer-wrap">
            <div class="composer">
              <textarea id="question" placeholder="例如：10.189.250.8是哪台设备，主机名、序列号、型号、状态、IDC、机房、机架是什么？"></textarea>
              <div class="send-wrap">
                <button onclick="ask()">查询</button>
                <button class="secondary" onclick="clearCurrentView()">清空</button>
              </div>
            </div>

            <div class="toolbar">
              <div class="tools">
                <span>快捷键 <span class="kbd">Ctrl</span> + <span class="kbd">Enter</span> 查询</span>
              </div>
              <div class="tools">
                <span>返回条数</span>
                <input id="limit" type="number" min="1" max="100" value="20" />
              </div>
            </div>

            <div id="loading" class="loading">正在查询，请稍候...</div>
            <div id="error" class="error"></div>
          </div>
        </div>
      </main>
    </section>
  </div>

<script>
let activeConversationId = null;
let activeConversationTitle = '新对话';
let resultStore = [];

function focusQuestionInput() {
  setTimeout(function() {
    const el = document.getElementById('question');
    if (el) el.focus();
  }, 80);
}

function escapeHtml(value) {
  if (value === null || value === undefined || value === '') return '-';
  return String(value)
    .replaceAll('&','&amp;')
    .replaceAll('<','&lt;')
    .replaceAll('>','&gt;')
    .replaceAll('"','&quot;')
    .replaceAll("'","&#039;");
}

function scrollToBottom() {
  const box = document.getElementById('conversation');
  box.scrollTop = box.scrollHeight;
}

function renderEmpty(title='新对话') {
  document.getElementById('conversation').innerHTML =
    `<div class="empty" id="emptyState"><div><div class="empty-title">${escapeHtml(title)}</div><div>输入一句话查询网络设备资产</div></div></div>`;
}

function renderTable(columns, labels, items) {
  if (!items || items.length === 0) return '';

  const cols = columns && columns.length ? columns : Object.keys(items[0]);
  let html = '<div class="table-wrap"><table><thead><tr>';

  for (const c of cols) {
    html += '<th>' + escapeHtml((labels && labels[c]) || c) + '<span>' + escapeHtml(c) + '</span></th>';
  }

  html += '</tr></thead><tbody>';

  for (const row of items) {
    html += '<tr>';
    for (const c of cols) {
      html += '<td>' + escapeHtml(row[c]) + '</td>';
    }
    html += '</tr>';
  }

  html += '</tbody></table></div>';
  return html;
}

function buildExportParamsFromChat(data) {
  const params = new URLSearchParams();
  const parsed = data.parsed || {};
  const filters = parsed.filters || {};

  const map = {
    "search": "search",
    "IDC__icontains": "IDC",
    "server_room__icontains": "server_room",
    "rack__icontains": "rack",
    "host_name__icontains": "host_name",
    "mgmt_ip": "mgmt_ip",
    "mgmt_ip__in": "mgmt_ip__in",
    "sn__icontains": "sn",
    "ci_type__icontains": "ci_type",
    "manufacturer__icontains": "manufacturer",
    "band__icontains": "band",
    "device_spec__icontains": "device_spec",
    "os_version__icontains": "os_version",
    "env": "env",
    "status__icontains": "status",
    "tag__icontains": "tag",
    "maintenance_manufacturer__icontains": "maintenance_manufacturer"
  };

  if (parsed.intent === "query_device_detail" && parsed.keyword) {
    params.set("mgmt_ip", parsed.keyword);
  }

  for (const [k, v] of Object.entries(filters)) {
    if (v === null || v === undefined || v === "") continue;
    if (map[k]) {
      params.set(map[k], v);
    } else {
      params.set(k, v);
    }
  }

  const cols = data.columns || [];
  if (cols.length > 0) params.set("fields", cols.join(","));

  const pageSize = Math.min(Math.max(parseInt(data.count || data.returned || 20), 1), 500);
  params.set("pageSize", String(pageSize));
  return params;
}

function appendTurn(question, data) {
  const empty = document.getElementById('emptyState');
  if (empty) empty.remove();

  const idx = resultStore.length;
  const exportParams = buildExportParamsFromChat(data);
  resultStore.push({data, exportParams});

  const tableHtml = renderTable(data.columns || [], data.field_labels || {}, data.items || []);
  const showExport = data.items && data.items.length > 0;
  const showActionExport = data.export_url && String(data.export_url).length > 0;
  const plannerText = data.planner_source ? `&nbsp; planner：<code>${escapeHtml(data.planner_source)}</code>` : '';

  const html = `
    <div class="turn">
      <div class="bubble user">${escapeHtml(question)}</div>
      <div class="bubble assistant">
        <div class="answer">${escapeHtml(data.answer || '')}</div>
        <div class="meta">
          request_id：<code>${escapeHtml(data.request_id || '-')}</code>
          &nbsp; 总数：<code>${escapeHtml(data.count)}</code>
          &nbsp; 本次返回：<code>${escapeHtml(data.returned)}</code>
          ${plannerText}
        </div>
        ${showActionExport ? `<div class="result-actions"><button class="secondary" onclick="downloadActionExport(${idx})">下载 Excel</button></div>` : ''}
        ${(!showActionExport && showExport) ? `<div class="result-actions"><button class="secondary" onclick="exportResultXlsx(${idx})">导出结果 Excel</button></div>` : ''}
      </div>
      ${tableHtml}
    </div>
  `;

  document.getElementById('conversation').insertAdjacentHTML('beforeend', html);
  scrollToBottom();
}

function setActiveTitle(title) {
  activeConversationTitle = title || '新对话';
}

function renderConversation(conv) {
  activeConversationId = conv.conversation_id;
  setActiveTitle(conv.title);
  resultStore = [];
  document.getElementById('conversation').innerHTML = '';

  const turns = conv.turns || [];
  if (!turns.length) {
    renderEmpty('当前对话暂无查询');
    focusQuestionInput();
    return;
  }

  for (const turn of turns) {
    appendTurn(turn.question, turn.response || {});
  }

  scrollToBottom();
  focusQuestionInput();
}

async function loadConversations() {
  const resp = await fetch('/api/v1/conversations?limit=80');
  const data = await resp.json();
  const list = document.getElementById('conversationList');
  const items = data.items || [];

  if (!items.length) {
    list.innerHTML = '<div class="history-title">暂无历史对话</div>';
    return;
  }

  list.innerHTML = items.map(item => `
    <div class="conv-item ${item.conversation_id === activeConversationId ? 'active' : ''}" onclick="openConversation('${item.conversation_id}')">
      <button class="delete-conv" onclick="deleteConversation(event, '${item.conversation_id}')">×</button>
      <div class="conv-title">${escapeHtml(item.title)}</div>
      <div class="conv-meta">${escapeHtml(item.turn_count)} 条查询</div>
    </div>
  `).join('');
}

async function newConversation() {
  const resp = await fetch('/api/v1/conversations', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({user: 'web_user', title: '新对话'})
  });

  const data = await resp.json();
  const conv = data.conversation;

  activeConversationId = conv.conversation_id;
  setActiveTitle(conv.title);
  resultStore = [];
  renderEmpty('新对话');
  document.getElementById('question').value = '';
  await loadConversations();
  focusQuestionInput();
}

async function openConversation(id) {
  const resp = await fetch('/api/v1/conversations/' + encodeURIComponent(id));
  if (!resp.ok) {
    alert('对话不存在或已被删除');
    await loadConversations();
    return;
  }

  const data = await resp.json();
  renderConversation(data.conversation);
  await loadConversations();
  focusQuestionInput();
}

async function deleteConversation(event, id) {
  event.stopPropagation();
  if (!confirm('确认删除这个查询对话？')) return;

  await fetch('/api/v1/conversations/' + encodeURIComponent(id), {method: 'DELETE'});

  if (activeConversationId === id) {
    activeConversationId = null;
    setActiveTitle('新对话');
    resultStore = [];
    renderEmpty('新对话');
  }

  await loadConversations();
  focusQuestionInput();
}

function clearCurrentView() {
  resultStore = [];
  renderEmpty('当前窗口已清空');
  document.getElementById('error').style.display = 'none';
  focusQuestionInput();
}

async function ask() {
  document.getElementById('error').style.display = 'none';

  const q = document.getElementById('question').value.trim();
  const limit = parseInt(document.getElementById('limit').value || '20');

  if (!q) {
    document.getElementById('error').innerText = '请输入查询问题。';
    document.getElementById('error').style.display = 'block';
    focusQuestionInput();
    return;
  }

  document.getElementById('loading').style.display = 'block';

  try {
    const resp = await fetch('/api/v1/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        user: 'web_user',
        question: q,
        limit: limit,
        conversation_id: activeConversationId,
        planner_mode: 'llm',
        debug: false
      })
    });

    const data = await resp.json();
    document.getElementById('loading').style.display = 'none';

    if (!resp.ok || data.status === 'error') {
      document.getElementById('error').innerText = data.message || '查询失败。';
      document.getElementById('error').style.display = 'block';
      focusQuestionInput();
      return;
    }

    activeConversationId = data.conversation_id || activeConversationId;
    if (activeConversationTitle === '新对话') setActiveTitle(q.slice(0, 28));
    appendTurn(q, data);
    document.getElementById('question').value = '';
    focusQuestionInput();
    await loadConversations();
  } catch (e) {
    document.getElementById('loading').style.display = 'none';
    document.getElementById('error').innerText = '请求异常：' + e;
    document.getElementById('error').style.display = 'block';
    focusQuestionInput();
  }
}

function exportResultXlsx(index) {
  const item = resultStore[index];
  if (!item || !item.exportParams) {
    alert('当前结果无法导出。');
    return;
  }
  window.location.href = '/api/v1/cmdb/devices/export.xlsx?' + item.exportParams.toString();
}

function downloadActionExport(index) {
  const item = resultStore[index];
  if (!item || !item.data || !item.data.export_url) {
    alert('当前结果没有可下载的 Excel 链接。');
    return;
  }
  window.location.href = item.data.export_url;
}

document.addEventListener('DOMContentLoaded', async function() {
  const q = document.getElementById('question');
  q.addEventListener('keydown', function(e) {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      ask();
    }
  });

  await loadConversations();
  renderEmpty('新对话');
  focusQuestionInput();
});
</script>
</body>
</html>
'''
