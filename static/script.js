// ─── i18n ───
const I18N = {
  zh: {
    title: 'Claude Code/Codex 日志监控',
    logFile: '日志文件',
    switchFile: '切换',
    intervalLabel: '间隔(s)',
    intervalSet: '设置',
    connecting: '连接中...',
    connected: '已连接',
    disconnected: '断开连接，重连中...',
    clear: '清空',
    themeDark: '🌙 暗色',
    themeLight: '☀️ 亮色',
    langBtn: 'EN',
    emptyTitle: '等待日志事件...',
    emptySub: '确保 CC Switch 代理正在运行，且日志文件路径正确',
    userMsg: '📤 用户消息',
    thinking: '💭 Thinking',
    reply: '💬 回复',
    toolCall: '🔧 ',
    input: '输入',
    output: '输出',
    cacheRead: '缓存读取',
    cacheCreate: '缓存创建',
    latency: '延迟',
    ttft: '首字',
    error: '错误: ',
    sessions: '会话',
    created: '创建',
    updated: '更新',
    reqCount: '次请求',
    errors: '错误',
    errorClear: '清空',
    lineNum: '行',
    systemPrompt: '📋 系统指令',
    systemReminder: '📌 系统提醒',
    contextUser: '👤 用户',
    contextAssistant: '🤖 助手',
    fullLog: '完整日志',
    copied: '已复制',
    copyFail: '复制失败',
    copyBlock: '复制',
    expandContent: '展开',
    collapseContent: '收起',
    debugOutput: '控制台输出',
    copyJson: '复制JSON',
    jsonCopied: 'JSON 已复制',
    noData: '暂无数据',
    toolsBlock: '🛠 工具列表',
    toolsCount: '个工具',
    sidebarCollapse: '收起',
    sidebarExpand: '展开',
  },
  en: {
    title: 'Claude Code/Codex Log Monitor',
    logFile: 'Log File',
    switchFile: 'Switch',
    intervalLabel: 'Interval(s)',
    intervalSet: 'Set',
    connecting: 'Connecting...',
    connected: 'Connected',
    disconnected: 'Disconnected, reconnecting...',
    clear: 'Clear',
    themeDark: '🌙 Dark',
    themeLight: '☀️ Light',
    langBtn: '中文',
    emptyTitle: 'Waiting for log events...',
    emptySub: 'Make sure CC Switch proxy is running and the log file path is correct',
    userMsg: '📤 User Message',
    thinking: '💭 Thinking',
    reply: '💬 Reply',
    toolCall: '🔧 ',
    input: 'Input',
    output: 'Output',
    cacheRead: 'Cache Read',
    cacheCreate: 'Cache Create',
    latency: 'Latency',
    ttft: 'TTFT',
    error: 'Error: ',
    sessions: 'Sessions',
    created: 'Created',
    updated: 'Updated',
    reqCount: 'requests',
    errors: 'Errors',
    errorClear: 'Clear',
    lineNum: 'L',
    systemPrompt: '📋 System Prompt',
    systemReminder: '📌 System Reminder',
    contextUser: '👤 User',
    contextAssistant: '🤖 Assistant',
    fullLog: 'Full Log',
    copied: 'Copied',
    copyFail: 'Copy failed',
    copyBlock: 'Copy',
    expandContent: 'Expand',
    collapseContent: 'Collapse',
    debugOutput: 'Console Log',
    copyJson: 'Copy JSON',
    jsonCopied: 'JSON copied',
    noData: 'No data yet',
    toolsBlock: '🛠 Tool Definitions',
    toolsCount: 'tools',
    sidebarCollapse: 'Collapse',
    sidebarExpand: 'Expand',
  }
};

let currentLang = localStorage.getItem('cc-watch-lang') || 'zh';

function t(key) { return I18N[currentLang][key] || key; }

function applyLang() {
  document.title = t('title');
  document.getElementById('labelLogFile').textContent = t('logFile');
  document.getElementById('btnSwitchFile').textContent = t('switchFile');
  document.getElementById('labelInterval').textContent = t('intervalLabel');
  document.getElementById('btnSetInterval').textContent = t('intervalSet');
  document.getElementById('clearBtn').textContent = t('clear');
  document.getElementById('langBtn').textContent = t('langBtn');
  document.getElementById('labelFullLog').textContent = t('fullLog');
  document.getElementById('labelDebugOutput').textContent = t('debugOutput');
  // 主题按钮
  const theme = document.documentElement.getAttribute('data-theme') || 'light';
  document.getElementById('themeBtn').textContent = theme === 'dark' ? t('themeLight') : t('themeDark');
  // 连接状态
  const dot = document.getElementById('statusDot');
  document.getElementById('statusText').textContent = dot.classList.contains('connected') ? t('connected') : t('connecting');
  // 侧边栏
  document.getElementById('sidebarTitle').textContent = t('sessions');
  const sidebarCollapsed = document.getElementById('sidebar').classList.contains('collapsed');
  document.getElementById('sidebarToggle').title = sidebarCollapsed ? t('sidebarExpand') : t('sidebarCollapse');
  document.getElementById('sidebarExpandBtn').title = t('sidebarExpand');
  // 会话列表项
  renderAllSessions();
  // 错误面板
  document.getElementById('errorPanelTitle').textContent = t('errors');
  document.getElementById('errorClearBtn').textContent = t('errorClear');
  // 空状态
  const empty = document.getElementById('emptyState');
  if (empty) {
    empty.innerHTML = t('emptyTitle') + '<br><small>' + t('emptySub') + '</small>';
  }
  localStorage.setItem('cc-watch-lang', currentLang);
}

function toggleLang() {
  currentLang = currentLang === 'zh' ? 'en' : 'zh';
  applyLang();
}

// ─── 全局状态 ───
const cards = {};          // id -> card DOM element
const cardData = {};       // id -> {model, time, status, sessionId, ...}
const cardRawBodies = {};  // id -> 原始请求体
const cardResponseBlocks = {}; // id -> 响应内容块数组
const pendingCardOps = {}; // id -> [{kind, ...}]，卡片未创建时暂存待渲染操作
const sessions = {};       // session_id -> {id, model, createdAt, updatedAt, requestCount}
let activeSessionId = null;
let eventSource = null;
const errorLogs = [];     // [{time, reason, rawLog, line}]
let fullLogEnabled = localStorage.getItem('cc-watch-full-log') === 'true';
let debugOutputEnabled = localStorage.getItem('cc-watch-debug-output') === 'true';
const debugEntries = [];

function toggleFullLog() {
  fullLogEnabled = document.getElementById('fullLogToggle').checked;
  localStorage.setItem('cc-watch-full-log', fullLogEnabled);
}

function toggleDebugOutput() {
  debugOutputEnabled = document.getElementById('debugOutputToggle').checked;
  localStorage.setItem('cc-watch-debug-output', debugOutputEnabled);
}

function debugLog(tag, payload) {
  if (!debugOutputEnabled) return;
  const entry = {
    time: new Date().toISOString(),
    tag,
    ...payload,
  };
  debugEntries.push(entry);
  if (debugEntries.length > 500) debugEntries.shift();
  window.__CC_SWITCH_DEBUG__ = debugEntries;
  console.log('[cc-watch]', entry);
}

// ─── 主题 ───
function initTheme() {
  const saved = localStorage.getItem('cc-watch-theme') || 'light';
  applyTheme(saved);
}

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  const btn = document.getElementById('themeBtn');
  btn.textContent = theme === 'dark' ? t('themeLight') : t('themeDark');
  localStorage.setItem('cc-watch-theme', theme);
}

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme') || 'light';
  applyTheme(current === 'dark' ? 'light' : 'dark');
}

// ─── SSE 连接 ───
function connectSSE() {
  if (eventSource) eventSource.close();
  eventSource = new EventSource('/events');

  eventSource.onopen = () => {
    debugLog('sse_open', { readyState: eventSource.readyState });
    document.getElementById('statusDot').classList.add('connected');
    document.getElementById('statusText').textContent = t('connected');
  };

  eventSource.onerror = () => {
    debugLog('sse_error', { readyState: eventSource.readyState });
    document.getElementById('statusDot').classList.remove('connected');
    document.getElementById('statusText').textContent = t('disconnected');
  };

  eventSource.onmessage = (e) => {
    try {
      const evt = JSON.parse(e.data);
      debugLog('sse_event', {
        type: evt.type,
        id: evt.id || null,
        session_id: evt.session_id || null,
        role: evt.role || null,
        block_type: evt.block_type || null,
        has_card: !!(evt.id && cards[evt.id]),
        pending_ops: evt.id && pendingCardOps[evt.id] ? pendingCardOps[evt.id].length : 0,
        response_blocks: evt.id && cardResponseBlocks[evt.id] ? cardResponseBlocks[evt.id].length : 0,
        evt,
      });
      handleEvent(evt);
    } catch (err) {
      console.error('Parse error:', err);
    }
  };
}

// ─── 事件处理 ───
function handleEvent(evt) {
  switch (evt.type) {
    case 'request_start':
      createCard(evt);
      break;
    case 'user_message':
      addBlock(evt.id, 'user', t('userMsg'), evt.content);
      break;
    case 'context_message':
      handleContextMessage(evt);
      break;
    case 'request_body':
      cardRawBodies[evt.id] = evt.body;
      break;
    case 'tools_list':
      handleToolsList(evt);
      break;
    case 'content_block':
      handleContentBlock(evt);
      break;
    case 'request_complete':
      completeCard(evt);
      break;
    case 'status':
      showToast(evt.message);
      break;
    case 'error':
      addErrorLog(evt.message, evt.raw_log, evt.line);
      break;
    case 'parse_error':
      addErrorLog(evt.reason, evt.raw_log, evt.line, evt.time);
      break;
  }
}

function handleContentBlock(evt) {
  // 记录响应内容块原始数据
  if (!cardResponseBlocks[evt.id]) cardResponseBlocks[evt.id] = [];
  const beforeCount = cardResponseBlocks[evt.id].length;
  const rawBlock = { type: evt.block_type };
  if (evt.block_type === 'thinking') {
    rawBlock.thinking = evt.text;
    addBlock(evt.id, 'thinking', t('thinking'), evt.text);
  } else if (evt.block_type === 'text') {
    rawBlock.text = evt.text;
    addBlock(evt.id, 'text', t('reply'), evt.text);
  } else if (evt.block_type === 'tool_use') {
    rawBlock.name = evt.name;
    rawBlock.input = evt.input;
    const inputStr = typeof evt.input === 'string'
      ? evt.input
      : JSON.stringify(evt.input, null, 2);
    addBlock(evt.id, 'tool', t('toolCall') + evt.name, inputStr);
  }
  cardResponseBlocks[evt.id].push(rawBlock);
  debugLog('content_block_stored', {
    id: evt.id,
    block_type: evt.block_type,
    before_count: beforeCount,
    after_count: cardResponseBlocks[evt.id].length,
    has_card: !!cards[evt.id],
    preview: evt.text ? evt.text.slice(0, 80) : (evt.name || null),
  });
}

function handleContextMessage(evt) {
  // 未开启完整日志时，只展示最后一条用户消息
  if (!fullLogEnabled && !evt.is_last) return;

  let type, title;
  if (evt.role === 'system') {
    type = 'system';
    title = t('systemPrompt');
  } else if (evt.role === 'system-reminder') {
    type = 'system-reminder';
    title = t('systemReminder');
  } else if (evt.role === 'user') {
    type = 'user';
    title = t('contextUser');
  } else {
    type = 'context-assistant';
    title = t('contextAssistant');
  }
  const collapsed = !evt.is_last;
  addBlock(evt.id, type, title, evt.content, collapsed);
}

function handleToolsList(evt) {
  if (!fullLogEnabled) return;
  if (!evt.tools || !evt.tools.length) return;
  addToolsBlock(evt.id, evt.tools);
}

function addToolsBlock(cardId, tools) {
  const body = document.getElementById('body-' + cardId);
  if (!body) {
    queueCardOp(cardId, { kind: 'tools', tools });
    return;
  }

  const block = document.createElement('div');
  block.className = 'block block-tools';

  const blockId = 'blk-' + Math.random().toString(36).substr(2, 9);
  const preview = tools.map(t_ => t_.name).join(', ').substring(0, 80);

  const header = document.createElement('div');
  header.className = 'block-header';
  header.innerHTML = `
    <span class="toggle">▶</span>
    <span>${t('toolsBlock')}</span>
    <span class="tools-count-badge">${tools.length} ${t('toolsCount')}</span>
    <span class="preview" id="prev-${blockId}">${esc(preview)}</span>
  `;
  header.onclick = () => toggleBlock(blockId);
  block.appendChild(header);

  const contentDiv = document.createElement('div');
  contentDiv.className = 'block-content collapsed';
  contentDiv.id = 'content-' + blockId;

  // 整体复制按钮
  const copyAllBtn = document.createElement('button');
  copyAllBtn.className = 'copy-btn';
  copyAllBtn.textContent = '⧉';
  copyAllBtn.title = t('copyBlock');
  copyAllBtn.onclick = (e) => {
    e.stopPropagation();
    navigator.clipboard.writeText(JSON.stringify(tools, null, 2))
      .then(() => showToast(t('copied')))
      .catch(() => showToast(t('copyFail')));
  };
  contentDiv.appendChild(copyAllBtn);

  // 逐个工具渲染
  for (const tool of tools) {
    const card = document.createElement('div');
    card.className = 'tool-def-card';

    // 工具名 + 单个复制
    const defHeader = document.createElement('div');
    defHeader.className = 'tool-def-header';
    const nameSpan = document.createElement('span');
    nameSpan.className = 'tool-def-name';
    nameSpan.textContent = tool.name || '';
    defHeader.appendChild(nameSpan);

    const copyBtn = document.createElement('button');
    copyBtn.className = 'tool-def-copy';
    copyBtn.textContent = '⧉';
    copyBtn.onclick = (e) => {
      e.stopPropagation();
      navigator.clipboard.writeText(JSON.stringify(tool, null, 2))
        .then(() => showToast(t('copied')))
        .catch(() => showToast(t('copyFail')));
    };
    defHeader.appendChild(copyBtn);
    card.appendChild(defHeader);

    // description
    if (tool.description) {
      const desc = document.createElement('div');
      desc.className = 'tool-def-desc desc-clamped';
      desc.textContent = tool.description;
      card.appendChild(desc);

      // 检查是否需要展开/收起按钮（文本超过3行时）
      requestAnimationFrame(() => {
        if (desc.scrollHeight > desc.clientHeight + 1) {
          const toggleBtn = document.createElement('button');
          toggleBtn.className = 'tool-def-desc-toggle';
          toggleBtn.textContent = '▾ ' + t('expandContent');
          toggleBtn.onclick = (e) => {
            e.stopPropagation();
            if (desc.classList.contains('desc-clamped')) {
              desc.classList.remove('desc-clamped');
              toggleBtn.textContent = '▴ ' + t('collapseContent');
            } else {
              desc.classList.add('desc-clamped');
              toggleBtn.textContent = '▾ ' + t('expandContent');
            }
          };
          desc.insertAdjacentElement('afterend', toggleBtn);
        }
      });
    }

    // input_schema
    if (tool.input_schema) {
      const schemaJson = JSON.stringify(tool.input_schema, null, 2);
      const schemaWrap = document.createElement('div');
      schemaWrap.className = 'tool-def-schema-wrap';
      const schemaCopyBtn = document.createElement('button');
      schemaCopyBtn.className = 'tool-def-schema-copy';
      schemaCopyBtn.textContent = '⧉';
      schemaCopyBtn.onclick = (e) => {
        e.stopPropagation();
        navigator.clipboard.writeText(schemaJson)
          .then(() => showToast(t('copied')))
          .catch(() => showToast(t('copyFail')));
      };
      const schemaPre = document.createElement('pre');
      schemaPre.className = 'tool-def-schema';
      const schemaCode = document.createElement('code');
      schemaCode.textContent = schemaJson;
      schemaPre.appendChild(schemaCode);
      schemaWrap.appendChild(schemaCopyBtn);
      schemaWrap.appendChild(schemaPre);
      card.appendChild(schemaWrap);
    }

    contentDiv.appendChild(card);
  }

  block.appendChild(contentDiv);

  // 插入位置：系统指令之后、system-reminder 之前
  const existingBlocks = body.querySelectorAll('.block');
  let insertBefore = null;
  for (const eb of existingBlocks) {
    if (eb.classList.contains('block-system-reminder') ||
        eb.classList.contains('block-user') ||
        eb.classList.contains('block-context-assistant')) {
      insertBefore = eb;
      break;
    }
  }
  if (insertBefore) {
    body.insertBefore(block, insertBefore);
  } else {
    body.appendChild(block);
  }
}

// ─── 卡片创建 ───
function createCard(evt) {
  hideEmpty();

  const sessionId = evt.session_id || null;

  // 更新或创建会话
  if (sessionId) {
    if (sessions[sessionId]) {
      sessions[sessionId].updatedAt = evt.time;
      sessions[sessionId].requestCount++;
      if (evt.model) sessions[sessionId].model = evt.model;
    } else {
      sessions[sessionId] = {
        id: sessionId,
        model: evt.model || '',
        createdAt: evt.time,
        updatedAt: evt.time,
        requestCount: 1,
      };
    }
    renderAllSessions();
    // 首个请求时自动选中该会话
    if (!activeSessionId || !sessions[activeSessionId]) {
      switchSession(sessionId);
    } else if (activeSessionId !== sessionId) {
      // 有新会话请求但当前不是它，不切换，仅更新列表
    }
  }

  const card = document.createElement('div');
  card.className = 'card';
  card.id = 'card-' + evt.id;
  if (sessionId) card.setAttribute('data-session', sessionId);

  const header = document.createElement('div');
  header.className = 'card-header';
  header.innerHTML = `
    <span class="toggle">▼</span>
    <span class="time">[${evt.time}]</span>
    <span class="model">${esc(evt.model)}</span>
    <span class="status-badge pending" id="badge-${evt.id}">...</span>
    <button class="copy-json-btn">${t('copyJson')}</button>
  `;
  header.onclick = () => toggleCard(evt.id);

  // 复制 JSON 按钮事件
  const copyJsonBtn = header.querySelector('.copy-json-btn');
  copyJsonBtn.onclick = (e) => {
    e.stopPropagation();
    copyCardAsJson(evt.id);
  };

  card.appendChild(header);

  const body = document.createElement('div');
  body.className = 'card-body';
  body.id = 'body-' + evt.id;
  card.appendChild(body);

  // 如果当前选中了某会话，且这张卡不属于它，则隐藏
  if (activeSessionId && sessionId !== activeSessionId) {
    card.style.display = 'none';
  }

  const main = document.getElementById('main');
  main.insertBefore(card, main.firstChild);

  cards[evt.id] = card;
  cardData[evt.id] = { model: evt.model, time: evt.time, collapsed: false, sessionId: sessionId };
  flushPendingCardOps(evt.id);
}

// ─── 内容块添加 ───
function addBlock(cardId, type, title, content, defaultCollapsed) {
  const body = document.getElementById('body-' + cardId);
  if (!body) {
    queueCardOp(cardId, { kind: 'block', type, title, content, defaultCollapsed });
    return;
  }

  const block = document.createElement('div');
  block.className = 'block block-' + type;

  const blockId = 'blk-' + Math.random().toString(36).substr(2, 9);
  const preview = (content || '').replace(/\n/g, ' ').substring(0, 80);

  const header = document.createElement('div');
  header.className = 'block-header';
  header.innerHTML = `
    <span class="toggle">▼</span>
    <span>${title}</span>
    <span class="preview" id="prev-${blockId}" style="display:none">${esc(preview)}</span>
  `;
  header.onclick = () => toggleBlock(blockId);
  block.appendChild(header);

  const contentDiv = document.createElement('div');
  contentDiv.className = 'block-content';
  contentDiv.id = 'content-' + blockId;

  // 复制按钮（内容区顶部）
  const copyBtn = document.createElement('button');
  copyBtn.className = 'copy-btn';
  copyBtn.textContent = '⧉';
  copyBtn.onclick = (e) => {
    e.stopPropagation();
    navigator.clipboard.writeText(content || '')
      .then(() => showToast(t('copied')))
      .catch(() => showToast(t('copyFail')));
  };
  contentDiv.appendChild(copyBtn);

  // 文本内容
  const textNode = document.createTextNode(content || '');
  contentDiv.appendChild(textNode);
  block.appendChild(contentDiv);

  if (defaultCollapsed) {
    contentDiv.classList.add('collapsed');
    header.querySelector('.toggle').textContent = '▶';
    const prev = document.getElementById('prev-' + blockId);
    if (prev) prev.style.display = '';
    contentDiv._needsClampCheck = true;
  }

  body.appendChild(block);

  // 非默认折叠的块，立即检查是否需要截断
  if (!defaultCollapsed) {
    maybeClampContent(contentDiv, block);
  }
}

// ─── 内容截断辅助 ───
function maybeClampContent(contentDiv, block) {
  requestAnimationFrame(() => {
    if (contentDiv.scrollHeight > 200) {
      contentDiv.classList.add('clamped');
      const toggleBtn = document.createElement('button');
      toggleBtn.className = 'content-clamp-toggle';
      toggleBtn.textContent = '▾ ' + t('expandContent');
      toggleBtn.onclick = (e) => {
        e.stopPropagation();
        const isClamped = contentDiv.classList.contains('clamped');
        if (isClamped) {
          contentDiv.classList.remove('clamped');
          toggleBtn.textContent = '▴ ' + t('collapseContent');
        } else {
          contentDiv.classList.add('clamped');
          toggleBtn.textContent = '▾ ' + t('expandContent');
        }
      };
      // 插入到 block 内、contentDiv 之后
      block.insertBefore(toggleBtn, contentDiv.nextSibling);
    }
  });
}

// ─── 请求完成 ───
function completeCard(evt) {
  // 用完成行中的 session_id 修正卡片归属
  const cd = cardData[evt.id];
  if (cd && evt.session_id && cd.sessionId !== evt.session_id) {
    const oldSid = cd.sessionId;
    const newSid = evt.session_id;
    cd.sessionId = newSid;

    const card = cards[evt.id];
    if (card) card.setAttribute('data-session', newSid);

    // 从旧会话减少计数
    if (oldSid && sessions[oldSid]) {
      sessions[oldSid].requestCount--;
      if (sessions[oldSid].requestCount <= 0) {
        delete sessions[oldSid];
      }
    }

    // 更新或创建新会话
    if (sessions[newSid]) {
      sessions[newSid].updatedAt = cd.time;
      sessions[newSid].requestCount++;
    } else {
      sessions[newSid] = {
        id: newSid,
        model: cd.model || evt.model || '',
        createdAt: cd.time,
        updatedAt: cd.time,
        requestCount: 1,
      };
    }

    renderAllSessions();

    // 刷新卡片可见性
    if (activeSessionId) {
      if (card) card.style.display = (newSid === activeSessionId) ? '' : 'none';
    }
  }

  const badge = document.getElementById('badge-' + evt.id);
  if (badge) {
    badge.textContent = evt.status;
    badge.className = 'status-badge ' + (evt.status === 200 ? 'ok' : 'err');
  }

  const body = document.getElementById('body-' + evt.id);
  if (!body) {
    queueCardOp(evt.id, { kind: 'complete', evt });
    return;
  }

  const statsBar = document.createElement('div');
  statsBar.className = 'stats-bar';
  statsBar.innerHTML = `
    <span class="stat"><span class="stat-label">${t('input')}=</span><span class="stat-value">${evt.input_tokens}</span></span>
    <span class="stat"><span class="stat-label">${t('output')}=</span><span class="stat-value">${evt.output_tokens}</span></span>
    <span class="stat"><span class="stat-label">${t('cacheRead')}=</span><span class="stat-value">${evt.cache_read}</span></span>
    <span class="stat"><span class="stat-label">${t('cacheCreate')}=</span><span class="stat-value">${evt.cache_creation}</span></span>
    <span class="stat"><span class="stat-label">${t('latency')}=</span><span class="stat-value">${evt.latency_ms}ms</span></span>
    <span class="stat"><span class="stat-label">${t('ttft')}=</span><span class="stat-value">${evt.first_token_ms}ms</span></span>
  `;
  body.appendChild(statsBar);
}

// ─── 折叠/展开 ───
function toggleCard(cardId) {
  const body = document.getElementById('body-' + cardId);
  const card = cards[cardId];
  if (!body || !card) return;

  const toggle = card.querySelector('.card-header .toggle');
  const isCollapsed = body.classList.contains('collapsed');
  if (isCollapsed) {
    body.classList.remove('collapsed');
    toggle.textContent = '▼';
  } else {
    body.classList.add('collapsed');
    toggle.textContent = '▶';
  }
}

function toggleBlock(blockId) {
  const content = document.getElementById('content-' + blockId);
  const prev = document.getElementById('prev-' + blockId);
  if (!content) return;

  const block = content.parentElement;
  const toggle = block.querySelector('.block-header .toggle');
  const isCollapsed = content.classList.contains('collapsed');

  if (isCollapsed) {
    content.classList.remove('collapsed');
    toggle.textContent = '▼';
    if (prev) prev.style.display = 'none';
    const clampToggle = block.querySelector('.content-clamp-toggle');
    if (clampToggle) clampToggle.style.display = '';
    // 延迟检查截断
    if (content._needsClampCheck) {
      content._needsClampCheck = false;
      maybeClampContent(content, block);
    }
  } else {
    content.classList.add('collapsed');
    toggle.textContent = '▶';
    if (prev) prev.style.display = '';
    const clampToggle = block.querySelector('.content-clamp-toggle');
    if (clampToggle) clampToggle.style.display = 'none';
  }
}

// ─── 工具栏操作 ───
function setLogFile() {
  const path = document.getElementById('logFile').value.trim();
  if (!path) return;
  fetch('/api/set-file', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({path})
  })
  .then(r => r.json())
  .then(d => showToast(d.message))
  .catch(e => showToast('错误: ' + e.message));
}

function setInterval_() {
  const val = parseFloat(document.getElementById('pollInterval').value);
  if (isNaN(val)) return;
  fetch('/api/set-interval', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({interval: val})
  })
  .then(r => r.json())
  .then(d => showToast(d.message))
  .catch(e => showToast('错误: ' + e.message));
}

// ─── 会话列表 ───
function renderAllSessions() {
  const list = document.getElementById('sessionList');
  if (!list) return;

  // 按 updatedAt 倒序排序
  const sorted = Object.values(sessions).sort((a, b) => {
    if (a.updatedAt > b.updatedAt) return -1;
    if (a.updatedAt < b.updatedAt) return 1;
    return 0;
  });

  list.innerHTML = '';
  for (const s of sorted) {
    const item = document.createElement('div');
    item.className = 'session-item' + (activeSessionId === s.id ? ' active' : '');
    item.id = 'session-' + s.id;
    item.innerHTML = `
      <div class="session-model">${esc(s.model)}</div>
      <div class="session-meta">${t('created')}: ${s.createdAt}</div>
      <div class="session-meta">${t('updated')}: ${s.updatedAt}  ${s.requestCount}${t('reqCount')}</div>
    `;
    item.onclick = () => switchSession(s.id);
    list.appendChild(item);
  }
}

function switchSession(sessionId) {
  activeSessionId = sessionId;

  // 更新侧边栏高亮
  document.querySelectorAll('.session-item').forEach(el => {
    el.classList.toggle('active', el.id === 'session-' + sessionId);
  });

  // 过滤卡片
  for (const [id, card] of Object.entries(cards)) {
    const cd = cardData[id];
    if (!cd) continue;
    if (sessionId && cd.sessionId !== sessionId) {
      card.style.display = 'none';
    } else {
      card.style.display = '';
    }
  }
}

function clearCards() {
  const main = document.getElementById('main');
  main.innerHTML = '<div class="empty-state" id="emptyState">' + t('emptyTitle') + '<br><small>' + t('emptySub') + '</small></div>';
  Object.keys(cards).forEach(k => delete cards[k]);
  Object.keys(cardData).forEach(k => delete cardData[k]);
  Object.keys(cardRawBodies).forEach(k => delete cardRawBodies[k]);
  Object.keys(cardResponseBlocks).forEach(k => delete cardResponseBlocks[k]);
  Object.keys(pendingCardOps).forEach(k => delete pendingCardOps[k]);
  Object.keys(sessions).forEach(k => delete sessions[k]);
  activeSessionId = null;
  document.getElementById('sessionList').innerHTML = '';
  clearErrors();
}

function queueCardOp(cardId, op) {
  if (!pendingCardOps[cardId]) pendingCardOps[cardId] = [];
  pendingCardOps[cardId].push(op);
  debugLog('queue_card_op', {
    id: cardId,
    kind: op.kind,
    queued: pendingCardOps[cardId].length,
  });
}

function flushPendingCardOps(cardId) {
  const ops = pendingCardOps[cardId];
  if (!ops || !ops.length) return;
  debugLog('flush_card_ops', {
    id: cardId,
    count: ops.length,
    kinds: ops.map(op => op.kind),
  });
  delete pendingCardOps[cardId];

  for (const op of ops) {
    if (op.kind === 'block') {
      addBlock(cardId, op.type, op.title, op.content, op.defaultCollapsed);
    } else if (op.kind === 'tools') {
      addToolsBlock(cardId, op.tools);
    } else if (op.kind === 'complete') {
      completeCard(op.evt);
    }
  }
}

// ─── 工具函数 ───
function hideEmpty() {
  const el = document.getElementById('emptyState');
  if (el) el.remove();
}

function esc(s) {
  const div = document.createElement('div');
  div.textContent = s;
  return div.innerHTML;
}

function copyCardAsJson(cardId) {
  const body = cardRawBodies[cardId];
  const blocks = cardResponseBlocks[cardId] || [];
  debugLog('copy_card_json', {
    id: cardId,
    has_body: !!body,
    response_blocks: blocks.length,
    response_block_types: blocks.map(block => block.type),
  });
  if (!body && blocks.length === 0) {
    showToast(t('noData'));
    return;
  }
  const output = {
    request: body || null,
    response: { content: blocks },
  };
  const json = JSON.stringify(output, null, 2);
  navigator.clipboard.writeText(json)
    .then(() => showToast(t('jsonCopied')))
    .catch(() => showToast(t('copyFail')));
}

function showToast(msg) {
  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.textContent = msg;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 3000);
}

// ─── 错误面板 ───
function addErrorLog(reason, rawLog, line, time) {
  const now = time || new Date().toLocaleTimeString('en-GB', {hour12: false});
  const entry = { time: now, reason: reason || '', rawLog: rawLog || '', line: line || 0 };
  errorLogs.push(entry);

  // 更新 badge
  document.getElementById('errorBadge').textContent = errorLogs.length;

  // 渲染新条目
  const body = document.getElementById('errorPanelBody');
  const el = document.createElement('div');
  el.className = 'error-entry';

  const lineStr = entry.line ? ` ${t('lineNum')}${entry.line}` : '';
  el.innerHTML = `
    <div class="error-entry-header">
      <span class="error-entry-time">${esc(entry.time)}</span>
      ${entry.line ? '<span class="error-entry-line">' + esc(lineStr) + '</span>' : ''}
    </div>
    <div class="error-entry-reason">${esc(entry.reason)}</div>
  `;

  if (entry.rawLog) {
    const rawEl = document.createElement('div');
    rawEl.className = 'error-entry-raw';
    rawEl.textContent = entry.rawLog;
    rawEl.title = entry.rawLog.length > 80 ? '点击展开/收起' : '';
    rawEl.onclick = () => rawEl.classList.toggle('expanded');
    el.appendChild(rawEl);
  }

  body.appendChild(el);
  body.scrollTop = body.scrollHeight;

  // 自动展开面板
  document.getElementById('errorPanel').classList.remove('collapsed');
}

function toggleErrorPanel() {
  document.getElementById('errorPanel').classList.toggle('collapsed');
}

function clearErrors() {
  errorLogs.length = 0;
  document.getElementById('errorPanelBody').innerHTML = '';
  document.getElementById('errorBadge').textContent = '0';
  document.getElementById('errorPanel').classList.add('collapsed');
}

// ─── 回到顶部 ───
function scrollToTop() {
  document.getElementById('main').scrollTo({ top: 0, behavior: 'smooth' });
}

document.getElementById('main').addEventListener('scroll', function() {
  const btn = document.getElementById('scrollTopBtn');
  btn.classList.toggle('visible', this.scrollTop > 200);
});

// ─── 侧边栏收起/展开 ───
function toggleSidebar() {
  const sidebar = document.getElementById('sidebar');
  const expandBtn = document.getElementById('sidebarExpandBtn');
  const collapsed = !sidebar.classList.contains('collapsed');
  sidebar.classList.toggle('collapsed', collapsed);
  expandBtn.classList.toggle('visible', collapsed);
  document.getElementById('sidebarToggle').title = collapsed ? t('sidebarExpand') : t('sidebarCollapse');
  localStorage.setItem('cc-watch-sidebar-collapsed', collapsed);
}

function initSidebar() {
  if (localStorage.getItem('cc-watch-sidebar-collapsed') === 'true') {
    document.getElementById('sidebar').classList.add('collapsed');
    document.getElementById('sidebarExpandBtn').classList.add('visible');
  }
}

// ─── 启动 ───
initTheme();
initSidebar();
document.getElementById('fullLogToggle').checked = fullLogEnabled;
document.getElementById('debugOutputToggle').checked = debugOutputEnabled;
applyLang();
connectSSE();
