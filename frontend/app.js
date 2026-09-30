/* ============================================================
   投资命题多证据验证台（对话版）
   ============================================================ */

const $ = (id) => document.getElementById(id);

// ---------- 文案映射 ----------
const STANCE_LABEL = { supporting: '支持', opposing: '反对', unverifiable: '无法验证' };
const VERDICT_LABEL = {
  confirmed: '命题成立', partially_confirmed: '部分成立',
  refuted: '命题不成立', inconclusive: '无法判断',
};
const SQ_LABEL = { supported: '子问题成立', opposed: '子问题不成立', unverifiable: '无法验证' };
const CONF_LABEL = { high: '高', medium: '中', low: '低' };
const CONF_COLOR = { high: '#059669', medium: '#d97706', low: '#94a3b8' };
const TYPE_LABEL = { fact: '事实', inference: '推断' };
const STRENGTH_LABEL = { strong: '强', medium: '中', weak: '弱' };
const VERDICT_DOT = { supported: '#16a34a', opposed: '#e11d48', unverifiable: '#94a3b8' };

let conversationId = null;
let activeAi = null;      // 当前正在生成的 AI 消息控制器
let generating = false;

// ============================================================
// 工具
// ============================================================
function esc(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function yi(v) {  // 元 -> 亿元
  if (v == null || isNaN(v)) return null;
  return (v / 1e8).toFixed(2);
}
function scrollToBottom(smooth = false) {
  const m = $('messages');
  m.scrollTo({ top: m.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
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
      <div class="work-panel collapsed">
        <button class="work-head">
          <span class="work-status-text">准备开始…</span>
          <span class="chev">▾</span>
        </button>
        <div class="work-timeline"></div>
      </div>
      <div class="ai-content">
        <div class="slot" data-slot="overview"></div>
        <div class="slot" data-slot="matrix"></div>
        <div class="slot" data-slot="subs"></div>
        <div class="slot" data-slot="charts"></div>
        <div class="slot" data-slot="conclusion"></div>
        <div class="slot" data-slot="followup"></div>
      </div>
    </div>`;
  $('messages').appendChild(row);

  const panel = row.querySelector('.work-panel');
  const timeline = row.querySelector('.work-timeline');
  const head = row.querySelector('.work-head');
  const statusText = row.querySelector('.work-status-text');

  head.addEventListener('click', () => panel.classList.toggle('collapsed'));

  const api = {
    row,
    timelineItem(label) {
      // 把已有 running 标记 done
      timeline.querySelectorAll('.wl-item.running').forEach((it) => {
        it.classList.remove('running');
        it.classList.add('done');
      });
      const it = document.createElement('div');
      it.className = 'wl-item running';
      it.innerHTML = `<span class="wl-dot"></span><span>${esc(label)}</span>`;
      timeline.appendChild(it);
      statusText.textContent = label;
      // 自动展开工作面板（生成中）
      panel.classList.remove('collapsed');
      scrollToBottom();
    },
    finishTimeline() {
      timeline.querySelectorAll('.wl-item.running').forEach((it) => {
        it.classList.remove('running');
        it.classList.add('done');
      });
      statusText.textContent = '验证完成';
      panel.classList.add('collapsed');
    },
    slot(name) { return row.querySelector(`.slot[data-slot="${name}"]`); },
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
    if (!resp.ok || !resp.body) { throw new Error('HTTP ' + resp.status); }

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
          catch (e) { /* 忽略无法解析帧 */ }
        }
      }
    }
  } catch (e) {
    activeAi.slot('followup').innerHTML =
      `<div class="error-bubble">网络或服务异常：${esc(e.message)}，请稍后重试。</div>`;
  } finally {
    if (activeAi) activeAi.finishTimeline();
    generating = false;
    activeAi = null;
    updateSendButton();
  }
}

// ============================================================
// 事件处理
// ============================================================
function handleEvent(ev) {
  const t = ev.type;
  switch (t) {
    case 'session':
      conversationId = ev.conversation_id;
      break;
    case 'meta':
      // intent 已知，无需特别处理
      break;
    case 'status':
      activeAi.timelineItem(ev.label);
      break;
    case 'tool_start':
      activeAi.timelineItem(ev.label);
      break;
    case 'decomposition':
      activeAi.slot('overview').innerHTML = renderOverview(ev.data);
      break;
    case 'sub_results':
      activeAi.slot('subs').insertAdjacentHTML(
        'beforeend', ev.data.map(renderSqCard).join(''));
      bindDetails(activeAi.row);
      scrollToBottom();
      break;
    case 'conclusion':
      activeAi.slot('conclusion').innerHTML = renderConclusion(ev.data);
      break;
    case 'answer_delta': {
      const slot = activeAi.slot('followup');
      let txt = slot.querySelector('.followup-text');
      if (!txt) {
        slot.innerHTML = '<div class="followup-text"></div>';
        txt = slot.querySelector('.followup-text');
      }
      txt.textContent += ev.text;
      scrollToBottom();
      break;
    }
    case 'followup_done':
      // 追问结束
      break;
    case 'done':
      finalizeResult(ev.data);
      break;
    case 'error':
      activeAi.slot('followup').innerHTML =
        `<div class="error-bubble">${esc(ev.message)}</div>`;
      break;
    case 'ping':
      break;
  }
}

function finalizeResult(result) {
  // 兜底渲染概览/结论（防止中途事件缺失）
  if (!activeAi.slot('overview').children.length) {
    activeAi.slot('overview').innerHTML = renderOverview(result.decomposition);
  }
  activeAi.slot('matrix').innerHTML = renderMatrix(result);
  activeAi.slot('charts').innerHTML = renderCharts(result);
  if (!activeAi.slot('conclusion').children.length) {
    activeAi.slot('conclusion').innerHTML = renderConclusion(result.conclusion);
  }
  bindDetails(activeAi.row);
  renderAllCharts(result);
  saveConversation(result);
  scrollToBottom();
}

// ============================================================
// 渲染：命题概览
// ============================================================
function renderOverview(dec) {
  dec = dec || {};
  const tg = dec.target || {};
  return `
  <div class="card fade-in">
    <div class="card-title">命题概览</div>
    <div class="kv-grid">
      <div>
        <div class="kv-label">研究标的</div>
        <div class="kv-value">${esc(tg.name || '未识别')}
          ${tg.thscode ? `<span class="text-slate-400 font-normal text-xs ml-1">${esc(tg.thscode)}</span>` : ''}
        </div>
      </div>
      <div>
        <div class="kv-label">时间范围</div>
        <div class="kv-value">${esc(dec.time_scope || '无法判断')}</div>
      </div>
      <div class="kv-full">
        <div class="kv-label">核心主张</div>
        <div class="kv-value norm">${esc(dec.core_claim || '')}</div>
      </div>
    </div>
  </div>`;
}

// ============================================================
// 渲染：证据矩阵总览
// ============================================================
function renderMatrix(result) {
  const rows = (result.sub_results || []).map((sr) => {
    const sq = sr.sub_question || {};
    const an = sr.analysis || {};
    const v = an.summary_verdict || 'unverifiable';
    const evis = an.evidences || [];
    // 最高证据强度
    let strength = '';
    if (evis.some((e) => e.strength === 'strong')) strength = '强';
    else if (evis.some((e) => e.strength === 'medium')) strength = '中';
    else if (evis.length) strength = '弱';
    // 关键数值：取首条证据前 38 字
    const keyNum = evis[0] ? (evis[0].content || '').slice(0, 42) : (an.summary_reason || '');
    const src = sr.tool_result_meta?.source || '';
    return `<tr>
      <td><b class="text-slate-700">${esc(sq.question || '')}</b></td>
      <td><span class="vdot" style="background:${VERDICT_DOT[v] || '#94a3b8'}"></span>${esc(SQ_LABEL[v] || v)}</td>
      <td>${esc(strength)}</td>
      <td class="text-slate-500">${esc(keyNum)}</td>
      <td class="text-slate-400">${esc(src)}</td>
    </tr>`;
  }).join('');
  return `
  <div class="card fade-in">
    <div class="card-title">证据矩阵总览</div>
    <div style="overflow-x:auto">
      <table class="matrix-table">
        <thead><tr>
          <th>子问题</th><th>结论</th><th>强度</th><th>关键数值</th><th>来源</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  </div>`;
}

// ============================================================
// 渲染：子问题卡片
// ============================================================
function renderSqCard(sr) {
  const sq = sr.sub_question || {};
  const an = sr.analysis || {};
  const meta = sr.tool_result_meta || {};
  const v = an.summary_verdict || 'unverifiable';
  const evis = (an.evidences || []);
  const id = 'raw-' + Math.random().toString(36).slice(2, 8);

  const eviHtml = evis.length
    ? `<div class="evi-list">${evis.map(renderEvi).join('')}</div>`
    : `<div class="text-xs text-slate-400 py-1">无可用证据</div>`;

  const rawHtml = renderRawTable(sr.raw_data || []);

  return `
  <div class="card sq-card v-${esc(v)} fade-in">
    <div class="sq-head">
      <div style="min-width:0">
        <div class="sq-q">${esc(sq.question || '')}</div>
        <div class="sq-meta">数据源：${esc(meta.source || '')}
          ${meta.data_time ? ` · 时点 ${esc(meta.data_time)}` : ''}
          ${meta.request_id ? ` · 追踪ID ${esc(meta.request_id)}` : ''}
        </div>
      </div>
      <span class="tag tag-${esc(v)}" style="flex-shrink:0">${esc(SQ_LABEL[v] || v)}</span>
    </div>
    ${an.summary_reason ? `<div class="sq-reason">${esc(an.summary_reason)}</div>` : ''}
    ${eviHtml}
    ${(sr.raw_data || []).length ? `
      <button class="details-toggle" data-target="${id}">
        <span class="chev">›</span> 查看原始数据（可溯源）
      </button>
      <div id="${id}" class="details-content">${rawHtml}</div>` : ''}
  </div>`;
}

function renderEvi(evi) {
  const stance = evi.stance || 'unverifiable';
  const tags = [
    `<span class="tag tag-${esc(stance)}">${STANCE_LABEL[stance] || stance}</span>`,
    evi.type ? `<span class="tag tag-${esc(evi.type)}">${TYPE_LABEL[evi.type]}</span>` : '',
    evi.strength ? `<span class="tag tag-${esc(evi.strength)}">强度 ${STRENGTH_LABEL[evi.strength]}</span>` : '',
  ].filter(Boolean).join(' ');
  return `
    <div class="evi-card evi-${esc(stance)}">
      <div class="flex flex-wrap gap-1.5 mb-1">${tags}</div>
      <div class="evi-content">${esc(evi.content || '')}</div>
      <div class="evi-foot">
        ${evi.source ? `来源：${esc(evi.source)}` : ''}
        ${evi.data_time ? ` · ${esc(evi.data_time)}` : ''}
        ${(evi.fields || []).length ? ` · 字段：${evi.fields.map(esc).join(', ')}` : ''}
      </div>
    </div>`;
}

// 原始数据表格化
function renderRawTable(raw) {
  if (!raw || !raw.length) return '';
  const cols = [];
  raw.slice(0, 6).forEach((r) => Object.keys(r).forEach((k) => {
    if (!cols.includes(k)) cols.push(k);
  }));
  const head = cols.map((c) => `<th>${esc(c)}</th>`).join('');
  const body = raw.slice(0, 6).map((r) =>
    `<tr>${cols.map((c) => `<td>${esc(r[c] == null ? '' : r[c])}</td>`).join('')}</tr>`
  ).join('');
  return `<table class="raw-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

// ============================================================
// 渲染：综合结论
// ============================================================
function renderConclusion(c) {
  c = c || {};
  const flips = (c.flip_conditions || []).map((f) => `
    <div class="flip-item">
      <div class="flip-cond">${esc(f.condition || '')}</div>
      <div class="flip-meta">监控指标：${esc(f.monitor_indicator || '-')}</div>
      <div class="flip-meta">发生影响：${esc(f.impact || '-')}</div>
    </div>`).join('');

  const conflicts = (c.conflicts || []).map((cf) => `
    <div class="conflict-item">
      <div class="font-semibold text-slate-700 mb-1">${esc(cf.description || '')}</div>
      <div class="text-emerald-700">证据A：${esc(cf.evidence_a || '')}</div>
      <div class="text-rose-700">证据B：${esc(cf.evidence_b || '')}</div>
      ${cf.possible_reason ? `<div class="text-slate-400 mt-1">可能原因：${esc(cf.possible_reason)}</div>` : ''}
    </div>`).join('');

  const steps = (c.next_steps || []).map((s) => `<li>${esc(s)}</li>`).join('');

  return `
  <div class="card fade-in">
    <div class="card-title">综合结论</div>
    <div class="verdict-row">
      <span class="verdict-badge v-${esc(c.verdict || 'inconclusive')}">${VERDICT_LABEL[c.verdict] || c.verdict}</span>
      <span class="conf-text">置信度：
        <span class="confidence-dot" style="background:${CONF_COLOR[c.confidence] || '#94a3b8'}"></span>
        <b>${CONF_LABEL[c.confidence] || c.confidence}</b>
        <span class="text-slate-400 ml-1">${esc(c.confidence_reason || '')}</span>
      </span>
    </div>
    <div class="conclusion-summary">${esc(c.summary || '')}</div>
    ${conflicts ? `<div class="sub-title">证据冲突点</div>${conflicts}` : ''}
    ${flips ? `<div class="sub-title">关键翻转条件（哪些信息变化会改变结论）</div>
      <div class="flip-grid">${flips}</div>` : ''}
    ${steps ? `<div class="sub-title">建议后续研究动作</div><ul class="next-list">${steps}</ul>` : ''}
  </div>`;
}

// ============================================================
// 图表
// ============================================================
function renderCharts(result) {
  return `
  <div class="card fade-in">
    <div class="card-title">关键数据可视化</div>
    <div class="chart-grid-2">
      <div class="chart-box sm"><canvas id="chartTrend"></canvas></div>
      <div class="chart-box sm"><canvas id="chartCash"></canvas></div>
    </div>
  </div>`;
}

function extractChartData(result) {
  const map = new Map();
  for (const sr of result.sub_results || []) {
    for (const r of sr.raw_data || []) {
      const y = r.fiscal_year;
      if (!y) continue;
      const e = map.get(y) || { year: y };
      if (r.operating_income != null) e.oi = r.operating_income;
      if (r.operating_profit != null) e.op = r.operating_profit;
      if (r.parent_holder_net_profit != null) e.np = r.parent_holder_net_profit;
      if (r.act_cash_flow_net != null) e.cf = r.act_cash_flow_net;
      map.set(y, e);
    }
  }
  return [...map.values()].sort((a, b) => a.year - b.year);
}

function renderAllCharts(result) {
  const data = extractChartData(result);
  if (!data.length) return;
  const labels = data.map((d) => d.year + '年');
  const font = { size: 10 };

  const ct = document.getElementById('chartTrend');
  if (ct) {
    new Chart(ct, {
      type: 'bar',
      data: {
        labels,
        datasets: [
          { label: '营业收入', data: data.map((d) => yi(d.oi)), backgroundColor: '#93c5fd' },
          { label: '营业利润', data: data.map((d) => yi(d.op)), backgroundColor: '#3b82f6' },
          { label: '归母净利润', data: data.map((d) => yi(d.np)), backgroundColor: '#1d4ed8' },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { title: { display: true, text: '营收与利润趋势（亿元）', font }, legend: { labels: { font } } },
        scales: { x: { ticks: { font } }, y: { ticks: { font } } },
      },
    });
  }

  const cc = document.getElementById('chartCash');
  if (cc) {
    new Chart(cc, {
      type: 'bar',
      data: {
        labels,
        datasets: [
          { label: '经营现金流净额', data: data.map((d) => yi(d.cf)), backgroundColor: '#34d399' },
          { label: '归母净利润', data: data.map((d) => yi(d.np)), backgroundColor: '#60a5fa' },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { title: { display: true, text: '现金流 vs 净利润（亿元）', font }, legend: { labels: { font } } },
        scales: { x: { ticks: { font } }, y: { ticks: { font } } },
      },
    });
  }
}

// ============================================================
// 原始数据折叠绑定
// ============================================================
function bindDetails(scope) {
  scope.querySelectorAll('.details-toggle').forEach((t) => {
    if (t._bound) return;
    t._bound = true;
    t.addEventListener('click', () => {
      const c = scope.querySelector('#' + t.dataset.target);
      c.classList.toggle('open');
      t.classList.toggle('open');
    });
  });
}

// ============================================================
// 会话历史（localStorage）
// ============================================================
const LS_KEY = 'tv_conversations';
function loadConvList() {
  try { return JSON.parse(localStorage.getItem(LS_KEY)) || []; }
  catch { return []; }
}
function saveConversation(result) {
  const list = loadConvList();
  const idx = list.findIndex((c) => c.id === conversationId);
  const item = { id: conversationId, title: result.thesis.slice(0, 22), updatedAt: Date.now() };
  if (idx >= 0) list[idx] = item; else list.unshift(item);
  localStorage.setItem(LS_KEY, JSON.stringify(list.slice(0, 30)));
  renderConvList();
}
function renderConvList() {
  const box = $('conversationList');
  const list = loadConvList();
  box.innerHTML = list.length
    ? list.map((c) =>
        `<button class="conv-item ${c.id === conversationId ? 'active' : ''}" data-id="${esc(c.id)}">${esc(c.title)}</button>`
      ).join('')
    : `<div class="text-[11px] text-slate-300 px-2">暂无历史对话</div>`;
  box.querySelectorAll('.conv-item').forEach((b) =>
    b.addEventListener('click', () => loadConversation(b.dataset.id)));
}

async function loadConversation(id) {
  // 后端内存仍在则回放，否则提示过期
  try {
    const r = await fetch('/api/conversation/' + id);
    const body = await r.json();
    if (!body.ok || !body.result) throw new Error('expired');
    resetChat(false);
    conversationId = id;
    const result = body.result;
    appendUserMessage(result.thesis);
    activeAi = createAiMessage();
    activeAi.slot('overview').innerHTML = renderOverview(result.decomposition);
    activeAi.slot('matrix').innerHTML = renderMatrix(result);
    activeAi.slot('subs').innerHTML =
      result.sub_results.map(renderSqCard).join('');
    activeAi.slot('charts').innerHTML = renderCharts(result);
    activeAi.slot('conclusion').innerHTML = renderConclusion(result.conclusion);
    bindDetails(activeAi.row);
    renderAllCharts(result);
    activeAi.finishTimeline();
    activeAi = null;
    renderConvList();
  } catch {
    // 过期：从列表移除
    const list = loadConvList().filter((c) => c.id !== id);
    localStorage.setItem(LS_KEY, JSON.stringify(list));
    renderConvList();
    alert('该历史对话已过期（服务重启或实例休眠），可新建对话重新验证。');
  }
}

// ============================================================
// 界面交互
// ============================================================
function resetChat(clearId = true) {
  $('messages').innerHTML = '';
  $('welcome')?.remove();
  if (clearId) conversationId = null;
  $('chatTitle').textContent = '投资命题多证据验证台';
}

function startWith(text) {
  $('welcome')?.remove();
  if (!generating) sendMessage(text);
}

function updateSendButton() {
  const btn = $('sendBtn');
  btn.classList.toggle('stop', generating);
  btn.innerHTML = generating
    ? `<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"></rect></svg>`
    : `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="19" x2="12" y2="5"></line><polyline points="5 12 12 5 19 12"></polyline></svg>`;
}

// 输入框自适应
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
  if (generating) {
    // 停止：刷新页面会中断 fetch，这里简单 reload 当前对话
    location.reload();
    return;
  }
  const text = ta.value.trim();
  if (!text) return;
  ta.value = '';
  ta.style.height = 'auto';
  startWith(text);
}

// 示例 / 建议
document.querySelectorAll('.suggest-btn').forEach((b) =>
  b.addEventListener('click', () => startWith(b.textContent.trim())));

// 新建对话
$('newChatBtn').addEventListener('click', () => location.reload());

// 移动端侧栏
$('menuToggle').addEventListener('click', () => {
  $('sidebar').classList.add('open');
  $('sidebarMask').classList.add('show');
});
$('sidebarMask').addEventListener('click', () => {
  $('sidebar').classList.remove('open');
  $('sidebarMask').classList.remove('show');
});

// 初始化
renderConvList();
