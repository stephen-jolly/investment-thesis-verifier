/* 投资命题多证据验证台 - 前端逻辑 */

const $ = (id) => document.getElementById(id);

// 中文映射
const STANCE_LABEL = { supporting: '支持', opposing: '反对', unverifiable: '无法验证' };
const VERDICT_LABEL = {
  confirmed: '命题成立',
  partially_confirmed: '部分成立',
  refuted: '命题不成立',
  inconclusive: '无法判断',
};
const SQ_VERDICT_LABEL = {
  supported: '子问题成立',
  opposed: '子问题不成立',
  unverifiable: '无法验证',
};
const CONF_LABEL = { high: '高', medium: '中', low: '低' };
const CONF_COLOR = { high: '#059669', medium: '#d97706', low: '#94a3b8' };
const TYPE_LABEL = { fact: '事实', inference: '推断' };
const STRENGTH_LABEL = { strong: '强', medium: '中', weak: '弱' };

let lastResult = null; // 保存最近一次完整结果，用于追问与溯源

function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ---------------------------------------------------------------------------
// 事件绑定
// ---------------------------------------------------------------------------
document.querySelectorAll('.example-btn').forEach((btn) => {
  btn.addEventListener('click', () => {
    $('thesisInput').value = btn.dataset.thesis;
  });
});

$('analyzeBtn').addEventListener('click', runAnalyze);
$('thesisInput').addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') runAnalyze();
});

async function runAnalyze() {
  const thesis = $('thesisInput').value.trim();
  $('inputError').classList.add('hidden');
  if (!thesis) {
    $('inputError').textContent = '请先输入投资命题';
    $('inputError').classList.remove('hidden');
    return;
  }

  setLoading(true);
  hideResult();

  try {
    const resp = await fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ thesis }),
    });
    const body = await resp.json();
    if (!body.ok) {
      showError(body.error || '分析失败，请稍后重试');
      return;
    }
    lastResult = body.result;
    renderResult(lastResult);
  } catch (e) {
    showError('网络或服务异常：' + e.message);
  } finally {
    setLoading(false);
  }
}

function setLoading(loading) {
  $('analyzeBtn').disabled = loading;
  $('analyzeBtnText').textContent = loading ? '验证中…' : '开始验证';
  $('progressSection').classList.toggle('hidden', !loading);
  if (loading) {
    $('progressSteps').innerHTML =
      ['拆解投资命题，识别标的与核心主张', '按子问题调用金融数据工具取证',
       '对证据进行支持/反对/无法验证分类', '处理证据冲突，生成可追溯综合结论']
        .map((s) => `<div class="flex items-center gap-2"><span class="text-slate-400">•</span>${s}</div>`)
        .join('');
  }
}

function hideResult() {
  $('resultSection').classList.add('hidden');
  $('resultSection').innerHTML = '';
  $('errorSection').classList.add('hidden');
}

function showError(msg) {
  $('errorSection').classList.remove('hidden');
  $('errorContent').textContent = msg;
}

// ---------------------------------------------------------------------------
// 渲染结果
// ---------------------------------------------------------------------------
function renderResult(res) {
  const root = $('resultSection');
  root.innerHTML = '';
  root.classList.remove('hidden');

  root.appendChild(renderOverview(res));
  root.appendChild(renderSubQuestions(res));
  root.appendChild(renderConclusion(res));
  root.appendChild(renderFollowup());

  root.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// 1. 命题概览
function renderOverview(res) {
  const dec = res.decomposition || {};
  const target = dec.target || {};
  const sec = document.createElement('section');
  sec.className = 'bg-white rounded-xl shadow-sm border border-slate-200 p-6 fade-in';

  const clar = (dec.clarifications || [])
    .map((c) => `<li class="text-amber-700">${escapeHtml(c)}</li>`).join('');

  sec.innerHTML = `
    <h2 class="text-base font-bold text-ink mb-4 flex items-center gap-2">
      <span class="w-1.5 h-5 bg-brand rounded"></span>命题概览
    </h2>
    <div class="grid sm:grid-cols-2 gap-4 text-sm">
      <div>
        <div class="text-xs text-slate-500 mb-1">研究标的</div>
        <div class="font-semibold text-ink">${escapeHtml(target.name || '未识别')}
          ${target.thscode ? `<span class="ml-2 text-xs font-normal text-slate-500">${escapeHtml(target.thscode)}</span>` : ''}
        </div>
      </div>
      <div>
        <div class="text-xs text-slate-500 mb-1">时间范围</div>
        <div class="font-semibold text-ink">${escapeHtml(dec.time_scope || '无法判断')}</div>
      </div>
      <div class="sm:col-span-2">
        <div class="text-xs text-slate-500 mb-1">核心主张</div>
        <div class="text-slate-700">${escapeHtml(dec.core_claim || res.thesis)}</div>
      </div>
    </div>
    ${clar ? `<div class="mt-4 rounded-lg bg-amber-50 border border-amber-200 px-4 py-3 text-xs"><div class="font-semibold text-amber-800 mb-1">需要澄清：</div><ul class="list-disc list-inside space-y-1">${clar}</ul></div>` : ''}
  `;
  return sec;
}

// 2. 子问题与证据
function renderSubQuestions(res) {
  const wrap = document.createElement('section');
  wrap.className = 'space-y-5 fade-in';

  const title = document.createElement('h2');
  title.className = 'text-base font-bold text-ink flex items-center gap-2 px-1';
  title.innerHTML = '<span class="w-1.5 h-5 bg-brand rounded"></span>子问题验证与证据链';
  wrap.appendChild(title);

  (res.sub_results || []).forEach((sr, idx) => {
    wrap.appendChild(renderSubQuestionCard(sr, idx));
  });
  return wrap;
}

function renderSubQuestionCard(sr, idx) {
  const sq = sr.sub_question || {};
  const an = sr.analysis || {};
  const meta = sr.tool_result_meta || {};
  const verdict = an.summary_verdict || 'unverifiable';
  const evidences = an.evidences || [];

  const card = document.createElement('div');
  card.className = 'bg-white rounded-xl shadow-sm border border-slate-200 p-6';

  const evidenceHtml = evidences.length
    ? evidences.map(renderEvidence).join('')
    : `<div class="text-xs text-slate-500 py-2">无可用证据</div>`;

  const rawId = `raw-${idx}`;
  const hasRaw = (sr.raw_data || []).length > 0;

  card.innerHTML = `
    <div class="flex items-start justify-between gap-3 mb-3">
      <div class="flex items-start gap-2">
        <span class="flex-shrink-0 w-6 h-6 rounded-md bg-slate-100 text-slate-600 text-xs font-bold flex items-center justify-center mt-0.5">${idx + 1}</span>
        <div>
          <div class="text-sm font-semibold text-ink">${escapeHtml(sq.question || '')}</div>
          <div class="text-xs text-slate-500 mt-1">数据源：${escapeHtml(meta.source || sq.tool || '-')}
            ${meta.data_time ? ` · 时点 ${escapeHtml(meta.data_time)}` : ''}
            ${meta.request_id ? ` · 追踪ID ${escapeHtml(meta.request_id)}` : ''}
          </div>
        </div>
      </div>
      <span class="tag tag-${escapeHtml(verdict)} flex-shrink-0">${SQ_VERDICT_LABEL[verdict] || verdict}</span>
    </div>

    ${an.summary_reason ? `<div class="text-xs text-slate-600 bg-slate-50 rounded-lg px-3 py-2 mb-3">${escapeHtml(an.summary_reason)}</div>` : ''}

    <div class="space-y-2">${evidenceHtml}</div>

    ${hasRaw ? `
      <div class="mt-3">
        <div class="details-toggle text-xs text-brand font-semibold" data-target="${rawId}">
          <span class="chevron">›</span> 查看原始数据（可溯源）
        </div>
        <div id="${rawId}" class="details-content mt-2">
          <div class="raw-data">${escapeHtml(JSON.stringify(sr.raw_data, null, 2))}</div>
        </div>
      </div>` : ''}
  `;

  card.querySelectorAll('.details-toggle').forEach((t) => {
    t.addEventListener('click', () => {
      const content = $(t.dataset.target);
      content.classList.toggle('open');
      t.querySelector('.chevron').classList.toggle('open');
    });
  });

  return card;
}

function renderEvidence(evi) {
  const stance = evi.stance || 'unverifiable';
  const tags = [
    `<span class="tag tag-${escapeHtml(stance)}">${STANCE_LABEL[stance] || stance}</span>`,
    evi.type ? `<span class="tag tag-${escapeHtml(evi.type)}">${TYPE_LABEL[evi.type] || evi.type}</span>` : '',
    evi.strength ? `<span class="tag tag-${escapeHtml(evi.strength)}">证据强度 ${STRENGTH_LABEL[evi.strength] || evi.strength}</span>` : '',
  ].filter(Boolean).join(' ');

  return `
    <div class="evi-card evi-${escapeHtml(stance)} rounded-lg px-4 py-3">
      <div class="flex flex-wrap items-center gap-2 mb-1">${tags}</div>
      <div class="text-sm text-slate-700">${escapeHtml(evi.content || '')}</div>
      <div class="text-[11px] text-slate-500 mt-1.5">
        ${evi.source ? `来源：${escapeHtml(evi.source)}` : ''}
        ${evi.data_time ? ` · ${escapeHtml(evi.data_time)}` : ''}
        ${(evi.fields || []).length ? ` · 字段：${evi.fields.map(escapeHtml).join(', ')}` : ''}
      </div>
    </div>`;
}

// 3. 综合结论
function renderConclusion(res) {
  const c = res.conclusion || {};
  const sec = document.createElement('section');
  sec.className = 'bg-white rounded-xl shadow-sm border border-slate-200 p-6 fade-in';

  const conflicts = (c.conflicts || []).map((cf) => `
    <div class="rounded-lg border border-slate-200 p-3 text-xs">
      <div class="font-semibold text-slate-800 mb-1">${escapeHtml(cf.description || '')}</div>
      <div class="text-emerald-700">证据A：${escapeHtml(cf.evidence_a || '')}</div>
      <div class="text-rose-700">证据B：${escapeHtml(cf.evidence_b || '')}</div>
      ${cf.possible_reason ? `<div class="text-slate-500 mt-1">可能原因：${escapeHtml(cf.possible_reason)}</div>` : ''}
    </div>`).join('');

  const flips = (c.flip_conditions || []).map((f) => `
    <div class="rounded-lg border border-blue-100 bg-blue-50 p-3">
      <div class="text-sm font-semibold text-blue-900 mb-1">${escapeHtml(f.condition || '')}</div>
      <div class="text-xs text-slate-600">监控指标：${escapeHtml(f.monitor_indicator || '-')}</div>
      <div class="text-xs text-slate-600">发生影响：${escapeHtml(f.impact || '-')}</div>
    </div>`).join('');

  const steps = (c.next_steps || []).map((s) =>
    `<li class="text-slate-700">${escapeHtml(s)}</li>`).join('');

  const traceId = 'trace-box';
  const trace = (res.meta?.trace || []).map((t) =>
    `<div class="flex justify-between py-0.5"><span>${escapeHtml(t.step)}</span><span class="text-slate-500">${escapeHtml(t.status)} · ${t.elapsed_ms}ms</span></div>`
  ).join('');

  sec.innerHTML = `
    <h2 class="text-base font-bold text-ink mb-4 flex items-center gap-2">
      <span class="w-1.5 h-5 bg-brand rounded"></span>综合结论
    </h2>

    <div class="flex flex-wrap items-center gap-4 mb-4">
      <span class="verdict-badge v-${escapeHtml(c.verdict || 'inconclusive')}">${VERDICT_LABEL[c.verdict] || c.verdict}</span>
      <span class="text-sm text-slate-600">
        置信度：<span class="confidence-dot" style="background:${CONF_COLOR[c.confidence] || '#94a3b8'}"></span>
        <b>${CONF_LABEL[c.confidence] || c.confidence}</b>
        <span class="text-slate-400 ml-1">${escapeHtml(c.confidence_reason || '')}</span>
      </span>
    </div>

    <div class="text-sm text-slate-700 leading-relaxed bg-slate-50 rounded-lg px-4 py-3 mb-5">${escapeHtml(c.summary || '')}</div>

    ${conflicts ? `<div class="mb-5"><div class="text-sm font-semibold text-ink mb-2">证据冲突点</div><div class="space-y-2">${conflicts}</div></div>` : ''}

    ${flips ? `<div class="mb-5"><div class="text-sm font-semibold text-ink mb-2">关键翻转条件（哪些信息变化会改变结论）</div><div class="grid sm:grid-cols-2 gap-3">${flips}</div></div>` : ''}

    ${steps ? `<div class="mb-5"><div class="text-sm font-semibold text-ink mb-2">建议后续研究动作</div><ul class="list-disc list-inside space-y-1 text-sm">${steps}</ul></div>` : ''}

    <div>
      <div class="details-toggle text-xs text-slate-500 font-semibold" data-target="${traceId}">
        <span class="chevron">›</span> 查看执行链路与耗时（可观测记录）
      </div>
      <div id="${traceId}" class="details-content mt-2 text-xs">
        <div class="bg-slate-50 rounded-lg px-4 py-3">
          ${trace}
          <div class="pt-2 mt-2 border-t border-slate-200 text-slate-500">总耗时：${res.meta?.total_elapsed_ms}ms · 生成于 ${escapeHtml(res.meta?.generated_at || '')}</div>
        </div>
      </div>
    </div>
  `;

  sec.querySelectorAll('.details-toggle').forEach((t) => {
    t.addEventListener('click', () => {
      const content = $(t.dataset.target);
      content.classList.toggle('open');
      t.querySelector('.chevron').classList.toggle('open');
    });
  });

  return sec;
}

// 4. 追问区
function renderFollowup() {
  const sec = document.createElement('section');
  sec.className = 'bg-white rounded-xl shadow-sm border border-slate-200 p-6 fade-in';
  sec.innerHTML = `
    <h2 class="text-base font-bold text-ink mb-3 flex items-center gap-2">
      <span class="w-1.5 h-5 bg-brand rounded"></span>继续追问 / 深入研究
    </h2>
    <div id="followupHistory" class="space-y-3 mb-4"></div>
    <div class="flex gap-2">
      <input id="followupInput" type="text" class="flex-1 rounded-lg border border-slate-300 px-4 py-2 text-sm outline-none focus:ring-2 focus:ring-brand"
        placeholder="基于上面的结论继续提问，如：需要重点关注哪些指标？" />
      <button id="followupBtn" class="px-5 py-2 rounded-lg bg-ink hover:bg-slate-800 text-white text-sm font-semibold">提问</button>
    </div>`;

  sec.querySelector('#followupBtn').addEventListener('click', () => doFollowup(sec));
  sec.querySelector('#followupInput').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') doFollowup(sec);
  });
  return sec;
}

async function doFollowup(sec) {
  const input = sec.querySelector('#followupInput');
  const question = input.value.trim();
  if (!question || !lastResult) return;

  const history = sec.querySelector('#followupHistory');
  history.insertAdjacentHTML('beforeend',
    `<div class="text-sm text-right"><span class="inline-block bg-brand text-white rounded-lg px-3 py-2">${escapeHtml(question)}</span></div>`);
  input.value = '';

  try {
    const resp = await fetch('/api/followup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        thesis: lastResult.thesis,
        prior_result: {
          conclusion: lastResult.conclusion,
          decomposition: { core_claim: lastResult.decomposition?.core_claim },
        },
        question,
      }),
    });
    const body = await resp.json();
    const answer = body.ok ? body.result.answer : ('处理失败：' + body.error);
    history.insertAdjacentHTML('beforeend',
      `<div class="text-sm"><span class="inline-block bg-slate-100 text-slate-800 rounded-lg px-3 py-2">${escapeHtml(answer)}</span></div>`);
  } catch (e) {
    history.insertAdjacentHTML('beforeend',
      `<div class="text-sm text-rose-600">追问异常：${escapeHtml(e.message)}</div>`);
  }
  history.scrollIntoView({ behavior: 'smooth', block: 'end' });
}
