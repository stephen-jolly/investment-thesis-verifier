/* ============================================================
   投资命题验证台（简洁对话版）
   ============================================================ */

const $ = (id) => document.getElementById(id);

let conversationId = null;
let activeAi = null;
let generating = false;

marked.setOptions({ gfm: true, breaks: true });
function renderMd(md) {
  return DOMPurify.sanitize(marked.parse(md || ''));
}

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function scrollToBottom() {
  const m = $('messages');
  m.scrollTop = m.scrollHeight;
}

// ============================================================
// AI 消息控制器
// ============================================================
function createAiMessage() {
  const row = document.createElement('div');
  row.className = 'msg-row ai fade-in';
  row.innerHTML = `
    <div class="avatar ai">验</div>
    <div class="msg-body">
      <div class="ai-status"><span class="spin"></span><span class="status-text">准备开始…</span></div>
      <div class="md-body"></div>
      <span class="typing-cursor" style="display:none"></span>
    </div>`;
  $('messages').appendChild(row);

  const statusEl = row.querySelector('.ai-status');
  const statusText = row.querySelector('.status-text');
  const mdEl = row.querySelector('.md-body');
  const cursor = row.querySelector('.typing-cursor');

  let md = '';

  const api = {
    row,
    setStatus(label) {
      statusText.textContent = label;
      statusEl.style.display = label ? '' : 'none';
    },
    startWriting() {
      statusEl.style.display = 'none';
      cursor.style.display = '';
    },
    append(text) {
      md += text;
      mdEl.innerHTML = renderMd(md);
      cursor.style.display = '';
      scrollToBottom();
    },
    setMarkdown(text) {
      md = text || '';
      mdEl.innerHTML = renderMd(md);
    },
    finish(label) {
      cursor.style.display = 'none';
      if (label) {
        statusText.textContent = label;
        statusEl.style.display = '';
        statusEl.querySelector('.spin')?.remove();
      } else {
        statusEl.style.display = 'none';
      }
    },
    error(msg) {
      cursor.style.display = 'none';
      statusEl.style.display = 'none';
      mdEl.innerHTML = `<div class="error-text">${esc(msg)}</div>`;
    },
  };
  return api;
}

function appendUserMessage(text) {
  const row = document.createElement('div');
  row.className = 'msg-row user fade-in';
  row.innerHTML = `
    <div class="avatar user">我</div>
    <div class="msg-body"><div class="user-bubble">${esc(text)}</div></div>`;
  $('messages').appendChild(row);
  scrollToBottom();
}

// ============================================================
// SSE 发送
// ============================================================
async function sendMessage(text) {
  appendUserMessage(text);
  generating = true;
  updateSendButton();
  activeAi = createAiMessage();

  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, conversation_id: conversationId }),
    });
    if (!resp.ok || !resp.body) throw new Error('HTTP ' + resp.status);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf('\n\n')) >= 0) {
        const chunk = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const dataLine = chunk.split('\n').find((l) => l.startsWith('data:'));
        if (dataLine) {
          try { handleEvent(JSON.parse(dataLine.slice(5).trim())); }
          catch { /* ignore bad frame */ }
        }
      }
    }
  } catch (e) {
    activeAi.error(`网络或服务异常：${e.message}，请稍后重试。`);
  } finally {
    generating = false;
    activeAi = null;
    updateSendButton();
  }
}

// ============================================================
// 事件处理
// ============================================================
function handleEvent(ev) {
  switch (ev.type) {
    case 'session':
      conversationId = ev.conversation_id;
      break;
    case 'status':
      activeAi.setStatus(ev.label);
      break;
    case 'report_start':
      activeAi.startWriting();
      break;
    case 'answer_delta':
      activeAi.append(ev.text);
      break;
    case 'followup_done':
      activeAi.finish('');
      renderConvList();
      break;
    case 'done':
      upsertLocalConv(conversationId, ev.data.thesis.slice(0, 22));
      activeAi.finish(`验证完成 · 用时 ${Math.round((ev.data.meta.total_elapsed_ms) / 1000)} 秒`);
      renderConvList();
      break;
    case 'error':
      activeAi.error(ev.message);
      break;
  }
}

// ============================================================
// 会话历史（localStorage）
// ============================================================
const LS_KEY = 'tv_conversations';
function loadConvList() {
  try { return JSON.parse(localStorage.getItem(LS_KEY)) || []; }
  catch { return []; }
}
function renderConvList() {
  const list = loadConvList();
  const box = $('conversationList');
  box.innerHTML = list.length
    ? list.map((c) =>
        `<button class="conv-item ${c.id === conversationId ? 'active' : ''}" data-id="${esc(c.id)}">${esc(c.title)}</button>`
      ).join('')
    : `<div class="text-[11px] text-slate-300 px-2">暂无历史对话</div>`;
  box.querySelectorAll('.conv-item').forEach((b) =>
    b.addEventListener('click', () => loadConversation(b.dataset.id)));
}

// 会话标题在后端生成时前端也记录（从 done 事件不易拿到 title，用首次消息）
function upsertLocalConv(convId, title) {
  const list = loadConvList();
  if (list.some((c) => c.id === convId)) return;
  list.unshift({ id: convId, title, updatedAt: Date.now() });
  localStorage.setItem(LS_KEY, JSON.stringify(list.slice(0, 30)));
}

async function loadConversation(id) {
  try {
    const r = await fetch('/api/conversation/' + id);
    const body = await r.json();
    if (!body.ok) throw new Error('expired');
    resetChat(false);
    conversationId = id;
    appendUserMessage(body.thesis || body.markdown.slice(0, 20));
    activeAi = createAiMessage();
    activeAi.setMarkdown(body.markdown);
    activeAi.finish('');
    activeAi = null;
    renderConvList();
  } catch {
    const list = loadConvList().filter((c) => c.id !== id);
    localStorage.setItem(LS_KEY, JSON.stringify(list));
    renderConvList();
    alert('该历史对话已过期（实例休眠或重启），可新建对话重新验证。');
  }
}

// ============================================================
// 界面交互
// ============================================================
function resetChat(clearId = true) {
  $('messages').innerHTML = '';
  if (clearId) conversationId = null;
}

function startWith(text) {
  $('welcome')?.remove();
  if (!generating) sendMessage(text);
}
function updateSendButton() {
  const btn = $('sendBtn');
  btn.classList.toggle('stop', generating);
  btn.innerHTML = generating
    ? `<svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"></rect></svg>`
    : `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="19" x2="12" y2="5"></line><polyline points="5 12 12 5 19 12"></polyline></svg>`;
}

const ta = $('composerInput');
ta.addEventListener('input', () => {
  ta.style.height = 'auto';
  ta.style.height = Math.min(ta.scrollHeight, 160) + 'px';
});
ta.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    fireComposer();
  }
});
$('sendBtn').addEventListener('click', fireComposer);

function fireComposer() {
  if (generating) { location.reload(); return; }
  const text = ta.value.trim();
  if (!text) return;
  ta.value = '';
  ta.style.height = 'auto';
  startWith(text);
}

document.querySelectorAll('.suggest-btn').forEach((b) =>
  b.addEventListener('click', () => startWith(b.textContent.trim())));

$('newChatBtn').addEventListener('click', () => location.reload());

$('menuToggle').addEventListener('click', () => {
  $('sidebar').classList.add('open');
  $('sidebarMask').classList.add('show');
});
$('sidebarMask').addEventListener('click', () => {
  $('sidebar').classList.remove('open');
  $('sidebarMask').classList.remove('show');
});

renderConvList();
