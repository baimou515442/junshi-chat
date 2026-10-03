/* 军师 Chat 前端：原生 JS，无框架、无外部 CDN。
   一套代码同时服务桌面版（宽屏）和移动版（窄屏），布局差异全在 CSS 媒体查询里。 */

(() => {
  'use strict';

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  const state = {
    view: 'chat',
    config: null,
    profiles: [],
    relationships: {},
    consent: false,
    profileId: 'default',
    thread: [],          // 真实对话：her = 她发的，me = 你实际发出去的
    toneCount: 0,
    toneSamples: [],
    adviceOn: true,
    busy: false,
    trend: null,
  };

  // ---------- 小工具 ----------

  function toast(message, isError) {
    const el = $('#toast');
    el.textContent = message;
    el.classList.toggle('err', !!isError);
    el.classList.add('show');
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.remove('show'), isError ? 4200 : 2200);
  }

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  async function api(path, options) {
    const opts = Object.assign({ headers: { 'Content-Type': 'application/json' } }, options || {});
    const res = await fetch(path, opts);
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (_) { data = null; }
    if (!res.ok) {
      const msg = (data && data.error) || `请求失败（HTTP ${res.status}）`;
      const err = new Error(msg);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  const post = (path, body) =>
    api(path, { method: 'POST', body: JSON.stringify(body || {}) });

  function busy(button, on, label) {
    if (!button) return;
    if (on) {
      button.dataset.label = button.textContent;
      button.disabled = true;
      button.textContent = label || '处理中…';
    } else {
      button.disabled = false;
      if (button.dataset.label) button.textContent = button.dataset.label;
    }
  }

  // ---------- 视图切换 ----------

  function go(view) {
    state.view = view;
    $('#app').dataset.view = view;
    $$('.view').forEach((node) => node.classList.toggle('active', node.dataset.view === view));
    $$('.nav-item').forEach((node) => node.classList.toggle('active', node.dataset.go === view));
    window.scrollTo({ top: 0 });
    if (view === 'trend' && !state.trend) drawPreset($('#trend-preset').value);
    if (view === 'profile') { loadProfiles(); updateToneSummary(); }
    if (view === 'settings') refreshConfigUI();
  }
  // ---------- 回她（主功能） ----------

  /** 一轮的完整渲染：她的原话 + 三条候选 + 军师判断。
      state.thread 里存的是**真实对话**：her = 她发的，me = 你实际发出去的。 */
  function renderThread() {
    const box = $('#thread');
    box.innerHTML = '';
    if (!state.thread.length) {
      box.appendChild(threadEmpty());
      bindReplyStarters();
      return;
    }
    state.thread.forEach((item) => {
      if (item.role === 'candidates') box.appendChild(candidatesBubble(item));
      else box.appendChild(bubble(item));
    });
    scrollThread();
    if (state.toneCount) updateToneSummary();
  }

  function threadEmpty() {
    const wrap = el('div', 'empty');
    wrap.id = 'thread-empty';
    wrap.appendChild(el('h2', null, '把她的原话贴进来'));
    const p = el('p');
    p.appendChild(document.createTextNode(
      '粘进来之后，军师会判断她的意思，再给你三条'));
    const strong = el('strong', null, '用你自己语气');
    p.appendChild(strong);
    p.appendChild(document.createTextNode(
      '写好的回复——发不发、发哪条都由你决定。'));
    wrap.appendChild(p);
    const chips = el('div', 'chips');
    chips.id = 'reply-starters';
    ['你最好记得', '随便吧', '我到家了', '你最近怎么都不主动找我'].forEach((text) => {
      const b = el('button', 'chip', text);
      b.type = 'button';
      b.dataset.fill = text;
      chips.appendChild(b);
    });
    wrap.appendChild(chips);
    return wrap;
  }

  function bindReplyStarters() {
    $$('#reply-starters .chip').forEach((chip) => {
      chip.onclick = () => {
        const input = $('#reply-input');
        input.value = chip.dataset.fill;
        autoGrow(input);
        input.focus();
      };
    });
  }

  function bubble(item) {
    const row = el('div', 'bubble-row ' + (item.role === 'me' ? 'me' : 'ta'));
    const body = el('div', 'bubble', item.text || '');
    row.appendChild(body);
    if (item.role === 'her') {
      const meta = el('div', 'bubble-meta');
      meta.appendChild(el('span', 'tag', '她发的'));
      row.appendChild(meta);
    }
    return row;
  }

  /** 三条候选 + 判断。这是这个页面的主要产出，所以给足操作按钮。 */
  function candidatesBubble(item) {
    const card = el('div', 'cand-card');
    const head = el('div', 'cand-card-head');
    head.appendChild(el('strong', null, '该回什么'));
    if (item.model) head.appendChild(el('span', 'tag', item.model));
    if (item.degraded) head.appendChild(el('span', 'tag', '格式降级'));
    card.appendChild(head);

    if (item.degraded) {
      card.appendChild(note('这一轮模型没按格式回，下面是原文——可以再生成一次。'));
    }

    (item.candidates || []).forEach((cand, idx) => {
      const box = el('div', 'cand' + (idx === item.best ? ' best' : ''));
      box.appendChild(el('div', 'cand-text', cand.text));
      const foot = el('div', 'cand-foot');
      if (idx === item.best) foot.appendChild(el('span', 'tag best', '军师推荐'));
      if (cand.tone) foot.appendChild(el('span', 'tag', cand.tone));
      if (cand.why) foot.appendChild(el('span', 'cand-why', '适合：' + cand.why));
      if (cand.cost) foot.appendChild(el('span', 'cand-why', '代价：' + cand.cost));

      const copy = el('button', 'btn ghost tiny', '复制');
      copy.type = 'button';
      copy.onclick = () => copyText(cand.text);
      foot.appendChild(copy);

      // 「我发的是这句」——让用户确认实际发出的版本，顺便收进语气记忆。
      const sent = el('button', 'btn ghost tiny', '我发的是这句');
      sent.type = 'button';
      sent.onclick = () => markSent(cand.text, sent);
      foot.appendChild(sent);

      box.appendChild(foot);
      card.appendChild(box);
    });

    if (item.analysis) card.appendChild(analysisDetails(item.analysis));
    if (item.refs && item.refs.length) {
      const wrap = el('div', 'refs');
      item.refs.forEach((r) => wrap.appendChild(el('span', 'ref-pill', r)));
      card.appendChild(wrap);
    }
    return card;
  }

  /** 判断细节默认收起：军师的东西是给你参考的，不该压过要发的那三句话。 */
  function analysisDetails(a) {
    const box = el('div', 'analysis-box');
    const toggle = el('button', 'btn ghost tiny', '展开军师判断');
    toggle.type = 'button';
    const body = el('div', 'analysis-body hide');
    const rows = [
      ['她的情绪与诉求', a.reading],
      ['事实与推测', a.facts],
      ['现在该做什么', a.action],
      ['现在最不该说', a.avoid],
      ['接下来观察', a.observe],
    ];
    rows.forEach(([title, text]) => {
      if (!text) return;
      const blk = el('div', 'advice-block');
      blk.appendChild(el('h3', null, title));
      blk.appendChild(el('p', null, text));
      body.appendChild(blk);
    });
    if (a.ask && a.ask.length) {
      const blk = el('div', 'advice-block');
      blk.appendChild(el('h3', null, '想更准可以告诉我'));
      const ul = el('ul');
      a.ask.forEach((q) => ul.appendChild(el('li', null, q)));
      blk.appendChild(ul);
      body.appendChild(blk);
    }
    toggle.onclick = () => {
      const hidden = body.classList.toggle('hide');
      toggle.textContent = hidden ? '展开军师判断' : '收起军师判断';
    };
    box.appendChild(toggle);
    box.appendChild(body);
    return box;
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      toast('已复制');
      return true;
    } catch (_) {
      toast('复制失败，请长按选择文字', true);
      return false;
    }
  }

  /** 用户点「我发的是这句」：把这条补进对话、并收进语气记忆。 */
  async function markSent(text, button) {
    if (state.busy) return;
    state.busy = true;
    busy(button, true, '记录中…');
    try {
      const data = await post('/api/tone', { text, profile_id: state.profileId });
      state.toneCount = data.tone_count || 0;
      // 本地也补上，界面立刻反映真实对话
      state.thread = state.thread.filter((i) => i.role !== 'candidates');
      state.thread.push({ role: 'me', text });
      renderThread();
      updateToneSummary();
      toast(data.saved
        ? '已记为「我发的话」，下一轮会更像你'
        : (data.message || '长期记忆没开启，这句没被记住'));
    } catch (err) {
      toast(err.message, true);
    } finally {
      state.busy = false;
      busy(button, false);
    }
  }

  function renderAdvice(analysis, refs, degraded) {
    const body = $('#advice-body');
    body.innerHTML = '';
    const badge = $('#advice-refs-badge');
    badge.classList.toggle('hide', !(refs && refs.length));
    if (!analysis) {
      body.appendChild(el('p', 'advice-null',
        '这一轮军师没多说什么。候选在上面，直接用就行。'));
      return;
    }
    const rows = [
      ['她的情绪与诉求', analysis.reading],
      ['事实与推测', analysis.facts],
      ['现在该做什么', analysis.action],
      ['现在最不该说', analysis.avoid],
      ['接下来观察', analysis.observe],
    ];
    rows.forEach(([title, text]) => {
      if (!text) return;
      const blk = el('div', 'advice-block');
      blk.appendChild(el('h3', null, title));
      blk.appendChild(el('p', null, text));
      body.appendChild(blk);
    });
    if (analysis.ask && analysis.ask.length) {
      const blk = el('div', 'advice-block');
      blk.appendChild(el('h3', null, '想更准可以告诉我'));
      const ul = el('ul');
      analysis.ask.forEach((q) => ul.appendChild(el('li', null, q)));
      blk.appendChild(ul);
      body.appendChild(blk);
    }
    if (refs && refs.length) {
      const blk = el('div', 'advice-block');
      blk.appendChild(el('h3', null, '这次参考了'));
      const wrap = el('div', 'refs');
      refs.forEach((r) => wrap.appendChild(el('span', 'ref-pill', r)));
      blk.appendChild(wrap);
      body.appendChild(blk);
    }
  }

  function note(text) {
    return el('p', 'muted small', text);
  }

  /** 生成回复。这是这个产品的主流程。 */
  async function generateReply(event) {
    if (event) event.preventDefault();
    const input = $('#reply-input');
    const latest = input.value.trim();
    if (!latest || state.busy) return;

    const profile = state.profiles.find((p) => p.id === state.profileId) || null;
    const empty = $('#thread-empty');
    if (empty) empty.remove();

    // 她的原话先落到对话里（本地立即显示；后端在长期记忆开着时也会存一份）
    state.thread = state.thread.filter((i) => i.role !== 'candidates');
    state.thread.push({ role: 'her', text: latest });
    input.value = '';
    autoGrow(input);
    renderThread();

    state.busy = true;
    busy($('#btn-generate'), true, '生成中…');
    const pending = el('div', 'cand-card');
    pending.appendChild(el('p', 'typing-holder', '军师正在看这句…'));
    $('#thread').appendChild(pending);
    scrollThread();

    try {
      const data = await post('/api/reply', {
        latest,
        profile_id: state.profileId,
        relationship: profile ? (profile.status || '') : '',
        goal: profile ? (profile.goal || '') : '',
      });
      pending.remove();
      state.thread.push({
        role: 'candidates',
        candidates: data.candidates || [],
        best: data.best || 0,
        analysis: data.analysis,
        refs: data.refs || [],
        degraded: data.degraded,
        model: data.model,
      });
      renderThread();
      renderAdvice(data.analysis, data.refs, data.degraded);
      state.toneCount = data.tone_count || state.toneCount;
      updateToneSummary();
    } catch (err) {
      pending.remove();
      state.thread.push({ role: 'candidates', candidates: [], best: 0,
                          analysis: { action: '没能生成：' + err.message } });
      renderThread();
      renderAdvice({ action: '没能生成：' + err.message }, [], false);
      if (err.status === 400 && /API Key/.test(err.message)) {
        toast('先去「设置」填入 API Key', true);
        go('settings');
      } else {
        toast(err.message, true);
      }
    } finally {
      state.busy = false;
      busy($('#btn-generate'), false);
      input.focus();
    }
  }

  function scrollThread() {
    const box = $('#thread');
    box.scrollTop = box.scrollHeight;
  }

  function autoGrow(textarea) {
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 132) + 'px';
  }

  async function loadThread() {
    try {
      const data = await api('/api/history?profile_id=' +
        encodeURIComponent(state.profileId) + '&limit=60');
      state.toneCount = data.tone_count || 0;
      state.toneSamples = data.tone_samples || [];
      state.thread = (data.items || []).map((row) => ({
        role: row.role === 'me' ? 'me' : 'her',
        text: row.text,
      }));
      renderThread();
      renderAdvice(null, [], false);
      updateToneSummary();
    } catch (_) {
      state.thread = [];
      renderThread();
    }
  }

  function updateToneSummary() {
    const box = $('#tone-summary');
    if (!box) return;
    box.textContent = state.toneCount
      ? `语气样本：${state.toneCount} 句（越用越像你）`
      : '语气样本：还没有。往下填几句你平时怎么说话，候选会明显更像你。';
  }


  // ---------- 记录分析 ----------

  function renderAnalysis(data) {
    const body = $('#analysis-body');
    body.innerHTML = '';
    const wrap = el('div', 'verdict');

    const top = el('div', 'verdict-top');
    top.appendChild(el('span', 'verdict-intent', data.intent_label || '无法判断'));
    if (data.confidence) {
      top.appendChild(el('span', 'tag', '自评把握 ' + Math.round(data.confidence * 100) + '%'));
    }
    if (data.literal) top.appendChild(el('span', 'tag', '字面意思'));
    top.appendChild(el('span', 'tag', '紧张度 ' + data.danger + '/10'));
    wrap.appendChild(top);

    const meter = el('div', 'meter');
    const bar = el('i');
    bar.style.width = Math.max(3, data.danger * 10) + '%';
    meter.appendChild(bar);
    wrap.appendChild(meter);
    wrap.appendChild(el('p', 'small muted', data.danger_label || ''));

    if (data.injection_notice) {
      const warn = el('p', 'warn-item', data.injection_notice);
      wrap.appendChild(warn);
    }

    const action = el('div', 'advice-block');
    action.appendChild(el('h3', null, '建议的动作'));
    action.appendChild(el('p', null, (data.action_label || '') +
      (data.action_reason ? '——' + data.action_reason : '')));
    wrap.appendChild(action);

    if (data.evidence && data.evidence.length) {
      const b = el('div', 'advice-block');
      b.appendChild(el('h3', null, '依据（引自记录）'));
      const ul = el('ul', 'evidence');
      data.evidence.forEach((e) => ul.appendChild(el('li', null, e)));
      b.appendChild(ul);
      wrap.appendChild(b);
    }

    const candHead = el('div', 'advice-block');
    candHead.appendChild(el('h3', null, '候选回复（点一下复制）'));
    wrap.appendChild(candHead);

    (data.candidates || []).forEach((cand, idx) => {
      const card = el('div', 'cand' + (idx === data.best ? ' best' : ''));
      card.appendChild(el('div', 'cand-text', cand.text));
      const foot = el('div', 'cand-foot');
      if (idx === data.best) foot.appendChild(el('span', 'tag best', '推荐'));
      if (cand.tone) foot.appendChild(el('span', 'tag', cand.tone));
      if (cand.why) foot.appendChild(el('span', 'cand-why', '适用：' + cand.why));
      if (cand.cost) foot.appendChild(el('span', 'cand-why', '代价：' + cand.cost));
      const copy = el('button', 'btn ghost tiny', '复制');
      copy.type = 'button';
      copy.onclick = async () => {
        try { await navigator.clipboard.writeText(cand.text); toast('已复制'); }
        catch (_) { toast('复制失败，请长按选择', true); }
      };
      foot.appendChild(copy);
      card.appendChild(foot);
      wrap.appendChild(card);
    });

    if (data.watch) {
      const b = el('div', 'advice-block');
      b.appendChild(el('h3', null, '接下来看什么'));
      b.appendChild(el('p', null, data.watch));
      wrap.appendChild(b);
    }
    if (data.refs && data.refs.length) {
      const b = el('div', 'advice-block');
      b.appendChild(el('h3', null, '这次参考了'));
      const refs = el('div', 'refs');
      data.refs.forEach((r) => refs.appendChild(el('span', 'ref-pill', r)));
      b.appendChild(refs);
      wrap.appendChild(b);
    }
    if (data.degraded) {
      wrap.appendChild(note('这一轮模型没有按格式回，结果可能不完整——可以再点一次「开始分析」。'));
    }
    body.appendChild(wrap);
  }

  async function runAnalysis() {
    const text = $('#analyze-input').value.trim();
    if (!text) { toast('先粘贴聊天记录', true); return; }
    const btn = $('#btn-analyze');
    busy(btn, true, '分析中…');
    $('#analyze-status').textContent = '正在判断意图并生成候选…';
    try {
      const data = await post('/api/analyze', {
        records: text,
        note: $('#analyze-note').value.trim(),
        relationship: $('#analyze-relationship').value,
        use_kb: $('#analyze-usekb').checked,
        profile_id: state.profileId,
      });
      renderAnalysis(data);
      $('#analyze-status').textContent = '完成';
    } catch (err) {
      $('#analyze-status').textContent = '';
      toast(err.message, true);
    } finally {
      busy(btn, false);
    }
  }

  // ---------- 趋势 ----------

  function renderTrend(data) {
    state.trend = data;
    const s = data.summary || {};
    $('#trend-label').textContent = s.trend || '—';
    $('#trend-days').textContent = s.days != null ? s.days : '—';
    $('#trend-msgs').textContent = s.messages != null ? s.messages : '—';
    $('#trend-range').textContent = (s.first != null && s.last != null)
      ? `${s.first} → ${s.last}（${s.delta >= 0 ? '+' : ''}${s.delta}）` : '—';
    $('#trend-split').textContent = (s.me_msgs != null)
      ? `${s.me_msgs} / ${s.her_msgs}` : '—';

    const warns = $('#trend-warnings');
    warns.innerHTML = '';
    (data.warnings || []).forEach((w) => warns.appendChild(el('div', 'warn-item', '· ' + w)));
    if (s.peak_date) warns.appendChild(el('div', 'warn-item',
      `· 高点 ${s.peak_date}（${s.peak}），低点 ${s.low_date}（${s.low}）`));

    drawKline(data.points || []);
  }

  function drawPreset(key) {
    if (!key) return;
    api('/api/trend/preset?key=' + encodeURIComponent(key))
      .then(renderTrend)
      .catch((err) => toast(err.message, true));
  }

  function drawKline(points) {
    const canvas = $('#kline');
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const cssW = canvas.clientWidth || 600;
    const cssH = canvas.clientHeight || 300;
    canvas.width = Math.round(cssW * dpr);
    canvas.height = Math.round(cssH * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    const padL = 34, padR = 12, padT = 14, padB = 26;
    const w = cssW - padL - padR;
    const h = cssH - padT - padB;

    // 网格与纵轴
    ctx.font = '11px -apple-system, "PingFang SC", sans-serif';
    ctx.textBaseline = 'middle';
    [0, 25, 50, 75, 100].forEach((v) => {
      const y = padT + h - (v / 100) * h;
      ctx.strokeStyle = v === 50 ? 'rgba(255,255,255,.16)' : 'rgba(255,255,255,.06)';
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(padL + w, y);
      ctx.stroke();
      ctx.fillStyle = 'rgba(154,164,184,.75)';
      ctx.textAlign = 'right';
      ctx.fillText(String(v), padL - 6, y);
    });

    // 中性区间 40–60
    const yHigh = padT + h - (60 / 100) * h;
    const yLow = padT + h - (40 / 100) * h;
    ctx.fillStyle = 'rgba(255,255,255,.035)';
    ctx.fillRect(padL, yHigh, w, yLow - yHigh);

    if (!points.length) {
      ctx.fillStyle = 'rgba(154,164,184,.8)';
      ctx.textAlign = 'center';
      ctx.fillText('还没有数据 —— 导入 CSV，或点「示例曲线」看效果', padL + w / 2, padT + h / 2);
      return;
    }

    const n = points.length;
    const xOf = (i) => padL + (n === 1 ? w / 2 : (i / (n - 1)) * w);
    const yOf = (v) => padT + h - (Math.max(0, Math.min(100, v)) / 100) * h;

    // 面积
    const grad = ctx.createLinearGradient(0, padT, 0, padT + h);
    grad.addColorStop(0, 'rgba(255,138,91,.30)');
    grad.addColorStop(1, 'rgba(255,138,91,0)');
    ctx.beginPath();
    ctx.moveTo(xOf(0), padT + h);
    points.forEach((p, i) => ctx.lineTo(xOf(i), yOf(p.value)));
    ctx.lineTo(xOf(n - 1), padT + h);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    // 折线
    ctx.beginPath();
    points.forEach((p, i) => (i ? ctx.lineTo(xOf(i), yOf(p.value)) : ctx.moveTo(xOf(i), yOf(p.value))));
    ctx.strokeStyle = '#ff8a5b';
    ctx.lineWidth = 2;
    ctx.lineJoin = 'round';
    ctx.stroke();

    // 数据点：点少时全画，点多时只画首尾与高低点
    const showAll = n <= 30;
    const marked = new Set([0, n - 1]);
    if (!showAll) {
      let hi = 0, lo = 0;
      points.forEach((p, i) => { if (p.value > points[hi].value) hi = i; if (p.value < points[lo].value) lo = i; });
      marked.add(hi); marked.add(lo);
    }
    points.forEach((p, i) => {
      if (!showAll && !marked.has(i)) return;
      ctx.beginPath();
      ctx.arc(xOf(i), yOf(p.value), 3, 0, Math.PI * 2);
      ctx.fillStyle = '#ffb27a';
      ctx.fill();
    });

    // 横轴标签：最多 6 个
    ctx.fillStyle = 'rgba(154,164,184,.75)';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    const step = Math.max(1, Math.ceil(n / 6));
    points.forEach((p, i) => {
      if (i % step !== 0 && i !== n - 1) return;
      const label = String(p.date || '').replace(/^\d{4}-/, '');
      ctx.fillText(label, xOf(i), padT + h + 6);
    });
  }

  async function trendFromCsv() {
    const text = $('#trend-csv').value;
    if (!text.trim()) { toast('先粘贴 CSV 内容', true); return; }
    const btn = $('#btn-trend-csv');
    busy(btn, true, '计算中…');
    try {
      const records = parseCsvClient(text);
      if (!records.length) throw new Error('CSV 里没解析出带时间的内容列');
      const data = await post('/api/trend', { records });
      if (!data.points || !data.points.length) {
        toast('这些记录里没有可用日期', true);
      }
      renderTrend(data);
    } catch (err) {
      toast(err.message, true);
    } finally {
      busy(btn, false);
    }
  }

  /* 前端也做一层 CSV 解析：这样「选文件」不用把内容传给后端解析，
     并且能立刻告诉用户「这份 CSV 少了时间列」而不是等到算出空白曲线。 */
  function parseCsvClient(text) {
    const lines = text.replace(/\r/g, '').split('\n').filter((l) => l.trim());
    if (!lines.length) return [];
    const delim = (lines[0].match(/\t/) ? '\t' : (lines[0].match(/;/) ? ';' : ','));
    const split = (line) => {
      const out = [];
      let cur = '', quoted = false;
      for (let i = 0; i < line.length; i++) {
        const ch = line[i];
        if (quoted) {
          if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++; }
          else if (ch === '"') quoted = false;
          else cur += ch;
        } else if (ch === '"') quoted = true;
        else if (ch === delim) { out.push(cur); cur = ''; }
        else cur += ch;
      }
      out.push(cur);
      return out.map((c) => c.trim());
    };
    const header = split(lines[0]).map((c) => c.toLowerCase());
    const find = (names) => header.findIndex((h) => names.indexOf(h) >= 0);
    const iFrom = find(['from', 'sender', 'speaker', 'role', 'who', '说话人', '发送者', '方向', '角色']);
    const iText = find(['text', 'content', 'message', 'msg', '内容', '消息']);
    const iTime = find(['time', 'timestamp', 'date', 'datetime', '时间', '日期']);
    const hasHeader = iText >= 0 || iFrom >= 0;
    const rows = hasHeader ? lines.slice(1) : lines;
    const get = (cols, idx, fallback) => (idx >= 0 && idx < cols.length ? cols[idx] : (fallback || ''));
    const out = [];
    rows.forEach((line) => {
      const cols = split(line);
      const text = hasHeader ? get(cols, iText) : get(cols, 1);
      if (!text) return;
      out.push({
        from: hasHeader ? get(cols, iFrom) : get(cols, 0),
        text,
        time: hasHeader ? get(cols, iTime) : get(cols, 2),
      });
    });
    return out;
  }

  // ---------- 档案 ----------

  function fillRelationshipOptions(selects) {
    selects.forEach((sel) => {
      const current = sel.value;
      sel.innerHTML = '';
      const blank = el('option', null, sel.dataset.blank || '未指定');
      blank.value = '';
      sel.appendChild(blank);
      Object.keys(state.relationships).forEach((key) => {
        const opt = el('option', null, state.relationships[key]);
        opt.value = key;
        sel.appendChild(opt);
      });
      sel.value = current || '';
    });
  }

  function renderProfiles() {
    const list = $('#pf-list');
    list.innerHTML = '';
    $('#pf-count').textContent = state.profiles.length ? state.profiles.length + ' 个' : '';
    if (!state.profiles.length) {
      list.appendChild(el('p', 'muted', state.consent
        ? '还没有保存任何对象。左边填好之后点「保存档案」。'
        : '长期记忆还没开启，所以现在不会保存任何对象。'));
      return;
    }
    state.profiles.forEach((p) => {
      const card = el('div', 'pf-card');
      const head = el('div', 'pf-card-head');
      head.appendChild(el('strong', null, p.codename || p.label || '未命名'));
      const actions = el('div', 'row');
      actions.style.margin = '0';
      const use = el('button', 'btn ghost tiny', '用它回她');
      use.type = 'button';
      use.onclick = () => {
        state.profileId = p.id;
        $('#reply-profile').value = p.id;
        loadThread();
        go('reply');
        toast('已切换到这个对象');
      };
      const edit = el('button', 'btn ghost tiny', '编辑');
      edit.type = 'button';
      edit.onclick = () => fillProfileForm(p);
      const del = el('button', 'btn ghost tiny', '删除');
      del.type = 'button';
      del.onclick = async () => {
        if (!confirm('删除这个对象的档案、对话记录和语气样本？')) return;
        try {
          await api('/api/profiles/' + encodeURIComponent(p.id), { method: 'DELETE' });
          toast('已删除');
          if (state.profileId === p.id) { state.profileId = 'default'; loadThread(); }
          loadProfiles();
        } catch (err) { toast(err.message, true); }
      };
      actions.appendChild(use); actions.appendChild(edit); actions.appendChild(del);
      head.appendChild(actions);
      card.appendChild(head);

      const meta = el('div', 'pf-meta');
      if (p.mbti) meta.appendChild(el('span', 'tag', p.mbti));
      if (p.status && state.relationships[p.status]) meta.appendChild(el('span', 'tag', state.relationships[p.status]));
      if (p.score != null) meta.appendChild(el('span', 'tag', '评分 ' + p.score));
      if (meta.childNodes.length) card.appendChild(meta);

      const bits = [];
      if (p.goal) bits.push('目标：' + p.goal);
      if (p.background) bits.push(p.background);
      if (p.notes) bits.push('注意：' + p.notes);
      if (bits.length) card.appendChild(el('div', 'pf-body', bits.join('\n')));

      if (p.events && p.events.length) {
        const ul = el('ul', 'events');
        p.events.slice(-4).forEach((ev) => {
          const when = ev.at ? new Date(ev.at * 1000).toLocaleDateString() : '';
          ul.appendChild(el('li', null, (when ? when + ' · ' : '') + ev.text));
        });
        card.appendChild(ul);
      }
      list.appendChild(card);
    });
  }

  function fillProfileForm(p) {
    $('#pf-id').value = p.id || '';
    $('#pf-codename').value = p.codename || '';
    $('#pf-mbti').value = p.mbti || '';
    $('#pf-status').value = p.status || '';
    $('#pf-score').value = p.score != null ? p.score : '';
    $('#pf-goal').value = p.goal || '';
    $('#pf-background').value = p.background || '';
    $('#pf-notes').value = p.notes || '';
    $('#pf-status-msg').textContent = p.id ? '正在编辑已有档案' : '';
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function resetProfileForm() {
    ['#pf-id', '#pf-codename', '#pf-mbti', '#pf-score', '#pf-goal', '#pf-background', '#pf-notes']
      .forEach((sel) => { $(sel).value = ''; });
    $('#pf-status').value = '';
    $('#pf-status-msg').textContent = '';
  }

  async function saveProfile() {
    const payload = {
      id: $('#pf-id').value || undefined,
      codename: $('#pf-codename').value.trim(),
      mbti: $('#pf-mbti').value.trim(),
      status: $('#pf-status').value,
      goal: $('#pf-goal').value.trim(),
      background: $('#pf-background').value.trim(),
      notes: $('#pf-notes').value.trim(),
    };
    const score = $('#pf-score').value;
    if (score !== '') payload.score = Number(score);
    if (!payload.codename) { toast('至少填一个代号', true); return; }
    const btn = $('#btn-pf-save');
    busy(btn, true, '保存中…');
    try {
      await post('/api/profiles', payload);
      toast('已保存');
      resetProfileForm();
      await loadProfiles();
    } catch (err) {
      toast(err.message, true);
      if (err.status === 403) $('#pf-status-msg').textContent = '需要先开启长期记忆';
    } finally {
      busy(btn, false);
    }
  }

  async function loadProfiles() {
    try {
      const data = await api('/api/profiles');
      state.profiles = data.items || [];
      state.relationships = data.relationships || {};
      state.consent = !!(data.summary && data.summary.consent);
      renderProfiles();
      renderConsent(data.summary || {});
      fillRelationshipOptions([$('#pf-status'), $('#analyze-relationship'), $('#cfg-relationship')]);
      fillProfileSelect();
    } catch (err) {
      toast(err.message, true);
    }
  }

  function fillProfileSelect() {
    const sel = $('#reply-profile');
    sel.innerHTML = '';
    const def = el('option', null, '默认（无档案）');
    def.value = 'default';
    sel.appendChild(def);
    state.profiles.forEach((p) => {
      const opt = el('option', null, p.codename || p.label || '未命名');
      opt.value = p.id;
      sel.appendChild(opt);
    });
    sel.value = state.profileId;
  }

  function renderConsent(summary) {
    const on = !!summary.consent;
    state.consent = on;
    $('#consent-panel').classList.toggle('on', on);
    $('#consent-title').textContent = '长期记忆：' + (on ? '已开启' : '未开启');
    $('#btn-consent-on').disabled = on;
    $('#btn-consent-off').disabled = !on;
    const bits = [];
    if (summary.profiles != null) bits.push(summary.profiles + ' 个对象');
    if (summary.events != null) bits.push(summary.events + ' 条关键事件');
    if (summary.data_dir) bits.push('存于 ' + summary.data_dir);
    $('#consent-desc').dataset.summary = bits.join(' · ');
  }

  // ---------- 设置 ----------

  function refreshConfigUI() {
    const c = state.config;
    if (!c) return;
    $('#cfg-base').value = c.base_url || '';
    $('#cfg-model').value = c.model || '';
    $('#cfg-style').value = c.style || '';
    $('#cfg-goal').value = c.goal || '';
    $('#cfg-usekb').checked = !!c.use_kb;
    $('#cfg-relationship').value = c.relationship || '';
    $('#cfg-key-state').textContent = c.key_set ? ('已保存：' + c.key_mask) : '未配置';
    $('#cfg-key').placeholder = c.key_set
      ? '已保存 ' + c.key_mask + '（留空不改动）'
      : 'sk-…';
    const list = $('#model-list');
    list.innerHTML = '';
    (c.models || []).forEach((m) => {
      const opt = el('option');
      opt.value = m;
      list.appendChild(opt);
    });
    if (c.model_error) $('#cfg-msg').textContent = c.model_error;
    updateStatusLine();
  }

  function updateStatusLine() {
    const c = state.config;
    if (!c) return;
    $('#side-status').textContent = c.key_set
      ? `已配置 ${c.key_mask} · ${c.model || ''}`
      : '未配置 API Key';
    const sub = $('#reply-sub');
    if (sub) {
      sub.textContent = c.key_set
        ? '把对方最新发来的话粘进来，给你三条能直接发出去的回复'
        : '还没填 API Key —— 去「设置」填一个就能开始';
    }
  }

  async function saveConfig(patch, button, okMessage) {
    busy(button, true, '保存中…');
    try {
      state.config = await post('/api/config', patch);
      refreshConfigUI();
      toast(okMessage || '已保存');
      return true;
    } catch (err) {
      toast(err.message, true);
      return false;
    } finally {
      busy(button, false);
    }
  }

  async function verifyKey() {
    const btn = $('#btn-verify');
    busy(btn, true, '测试中…');
    $('#cfg-msg').textContent = '正在测试连接…';
    try {
      const typed = $('#cfg-key').value.trim();
      const data = await post('/api/config/verify', {
        api_key: typed || undefined,
        base_url: $('#cfg-base').value.trim(),
      });
      $('#cfg-msg').textContent = data.message;
      toast(data.ok ? '连接正常' : data.message, !data.ok);
    } catch (err) {
      $('#cfg-msg').textContent = '';
      toast(err.message, true);
    } finally {
      busy(btn, false);
    }
  }

  async function fetchModels() {
    const btn = $('#btn-models');
    busy(btn, true, '拉取中…');
    $('#cfg-msg').textContent = '正在拉取模型列表…';
    try {
      const typed = $('#cfg-key').value.trim();
      if (typed) {
        // 先把新填的 Key 存下来，否则后端拿不到它
        const ok = await saveConfig({ api_key: typed, base_url: $('#cfg-base').value.trim() },
          null, '已保存 Key');
        if (!ok) return;
        $('#cfg-key').value = '';
      }
      const data = await api('/api/config?models=1');
      state.config = data;
      refreshConfigUI();
      $('#cfg-msg').textContent = '共 ' + (data.models || []).length + ' 个模型';
      if (data.model_error) toast(data.model_error, true);
    } catch (err) {
      $('#cfg-msg').textContent = '';
      toast(err.message, true);
    } finally {
      busy(btn, false);
    }
  }

  async function searchKb() {
    const q = $('#kb-query').value.trim();
    if (!q) return;
    const box = $('#kb-results');
    box.innerHTML = '';
    try {
      const data = await api('/api/knowledge/search?q=' + encodeURIComponent(q) + '&k=6');
      if (!data.hits.length) {
        box.appendChild(el('p', 'muted small', '没有匹配的参考文档。'));
        return;
      }
      data.hits.forEach((hit) => {
        const row = el('div', 'kb-hit');
        row.appendChild(el('span', null, hit.title));
        row.appendChild(el('span', null, (hit.category === 'knowledge' ? '知识' : '实用') + ' · ' + hit.score));
        box.appendChild(row);
      });
    } catch (err) {
      toast(err.message, true);
    }
  }

  // ---------- 初始化 ----------

  async function init() {
    bindNav();
    bindReply();
    bindAnalyze();
    bindTrend();
    bindProfile();
    bindSettings();

    try {
      const health = await api('/api/health');
      $('#kb-count').textContent = health.kb_docs + ' 份参考文档';
    } catch (_) { /* 健康检查失败不影响主流程 */ }

    try {
      state.config = await api('/api/config');
      refreshConfigUI();
    } catch (err) {
      toast('读取配置失败：' + err.message, true);
    }
    await loadProfiles();
    await loadThread();
    bindReplyStarters();
    go('reply');

    // 示例数据：让用户第一眼就知道分析长什么样
    $('#analyze-input').placeholder =
      '我: 在吗\n对方: 你最好记得\n我: 记得什么\n对方: 随便吧';
  }

  function bindNav() {
    $$('.nav-item').forEach((btn) => {
      btn.onclick = () => go(btn.dataset.go);
    });
    $('#btn-toggle-advice').onclick = (e) => {
      state.adviceOn = !state.adviceOn;
      $('#app').classList.toggle('advice-off', !state.adviceOn);
      e.target.textContent = '建议面板：' + (state.adviceOn ? '开' : '关');
    };
  }

  function bindReply() {
    $('#reply-form').onsubmit = generateReply;
    const input = $('#reply-input');
    input.oninput = () => autoGrow(input);
    input.onkeydown = (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        generateReply();
      }
    };
    $('#btn-clear-thread').onclick = async () => {
      if (!confirm('清空这个对象的对话记录？（档案和语气样本都不会删）')) return;
      try {
        await post('/api/history/clear', { profile_id: state.profileId });
        state.thread = [];
        renderThread();
        renderAdvice(null, [], false);
        toast('已清空');
      } catch (err) { toast(err.message, true); }
    };
    $('#reply-profile').onchange = (e) => {
      state.profileId = e.target.value;
      loadThread();
    };
  }

  function bindAnalyze() {
    $('#btn-analyze').onclick = runAnalysis;
    $('#btn-analyze-sample').onclick = () => {
      $('#analyze-input').value =
        '我: 在吗\n对方: 你最好记得\n我: 记得什么\n对方: 你自己想\n对方: 每次都是这样，我累了';
      $('#analyze-note').value = '我忘了上周三答应她的事';
      toast('已载入示例，点「开始分析」试试');
    };
  }

  function bindTrend() {
    const sel = $('#trend-preset');
    api('/api/trend/presets').then((data) => {
      sel.innerHTML = '';
      (data.items || []).forEach((item) => {
        const opt = el('option', null, item.label);
        opt.value = item.key;
        sel.appendChild(opt);
      });
      if (!state.trend && sel.value) drawPreset(sel.value);
    }).catch(() => {});
    sel.onchange = (e) => drawPreset(e.target.value);
    $('#btn-trend-sample').onclick = () => {
      api('/api/trend/sample').then(renderTrend).catch((err) => toast(err.message, true));
    };
    $('#btn-trend-csv').onclick = trendFromCsv;
    $('#trend-file').onchange = (e) => {
      const file = e.target.files && e.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => {
        $('#trend-csv').value = String(reader.result || '');
        trendFromCsv();
      };
      reader.readAsText(file, 'utf-8');
    };
    let resizeTimer = null;
    window.addEventListener('resize', () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        if (state.trend) drawKline(state.trend.points || []);
      }, 160);
    });
  }

  function bindProfile() {
    $('#btn-consent-on').onclick = async () => {
      try {
        const data = await post('/api/consent', { enabled: true });
        state.consent = !!data.enabled;
        toast('已开启长期记忆，从现在起才会保存');
        loadProfiles();
      } catch (err) { toast(err.message, true); }
    };
    $('#btn-consent-off').onclick = async () => {
      try {
        const data = await post('/api/consent', { enabled: false });
        state.consent = !!data.enabled;
        toast('已撤销：立即停止写入（已有数据还在，可单独删除）');
        loadProfiles();
      } catch (err) { toast(err.message, true); }
    };
    $('#btn-pf-save').onclick = saveProfile;
    $('#btn-tone-save').onclick = saveToneSamples;
    $('#btn-tone-clear').onclick = clearToneSamples;
    $('#btn-pf-reset').onclick = resetProfileForm;
    $('#btn-clear-all').onclick = async () => {
      if (!confirm('这会删除全部对象档案、关键事件、对话记录、语气样本，并关闭长期记忆。确定吗？')) return;
      try {
        await post('/api/clear-all', {});
        state.profileId = 'default';
        state.toneCount = 0;
        state.toneSamples = [];
        toast('已清空全部本地数据');
        await loadProfiles();
        state.thread = [];
        renderThread();
        updateToneSummary();
      } catch (err) { toast(err.message, true); }
    };
  }

  /** 把用户填的几句真实说过的话收进语气记忆。一行一句，去重后逐条上报。 */
  async function saveToneSamples() {
    const raw = $('#tone-input').value || '';
    const lines = raw.split('\n').map((x) => x.trim()).filter(Boolean);
    if (!lines.length) { toast('先写一句你平时会说的话', true); return; }
    const btn = $('#btn-tone-save');
    busy(btn, true, '保存中…');
    let saved = 0;
    let firstError = '';
    try {
      for (const text of lines.slice(0, 40)) {
        try {
          const data = await post('/api/tone', { text, profile_id: state.profileId });
          if (data.saved) saved += 1;
          else firstError = data.message || '没能保存';
        } catch (err) {
          firstError = err.message;
          break;
        }
      }
      $('#tone-msg').textContent = saved
        ? `已记住 ${saved} 句`
        : (firstError || '一句都没存进去');
      if (saved) {
        $('#tone-input').value = '';
        toast(`已记住 ${saved} 句，下一轮会更像你`);
      } else {
        toast(firstError || '没能保存', true);
      }
      // 刷新样本数
      const data = await api('/api/history?profile_id=' +
        encodeURIComponent(state.profileId) + '&limit=1');
      state.toneCount = data.tone_count || 0;
      state.toneSamples = data.tone_samples || [];
      updateToneSummary();
    } finally {
      busy(btn, false);
    }
  }

  async function clearToneSamples() {
    if (!confirm('清空这个对象的语气样本？这不会影响档案和对话记录。')) return;
    try {
      await post('/api/tone/clear', { profile_id: state.profileId });
      state.toneCount = 0;
      state.toneSamples = [];
      $('#tone-msg').textContent = '';
      updateToneSummary();
      toast('已清空语气样本');
    } catch (err) { toast(err.message, true); }
  }

  function bindSettings() {
    $('#btn-key-show').onclick = (e) => {
      const input = $('#cfg-key');
      const show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      e.target.textContent = show ? '隐藏' : '显示';
    };
    $('#btn-cfg-save').onclick = () => {
      const patch = {
        base_url: $('#cfg-base').value.trim(),
        model: $('#cfg-model').value.trim(),
      };
      const typed = $('#cfg-key').value.trim();
      if (typed) patch.api_key = typed;
      saveConfig(patch, $('#btn-cfg-save'), '设置已保存').then((ok) => {
        if (ok) $('#cfg-key').value = '';
      });
    };
    $('#btn-pref-save').onclick = () => saveConfig({
      style: $('#cfg-style').value,
      goal: $('#cfg-goal').value.trim(),
      relationship: $('#cfg-relationship').value,
      use_kb: $('#cfg-usekb').checked,
    }, $('#btn-pref-save'), '偏好已保存');
    $('#btn-verify').onclick = verifyKey;
    $('#btn-models').onclick = fetchModels;
    $('#btn-key-clear').onclick = async () => {
      if (!confirm('清除本机保存的 API Key？')) return;
      await saveConfig({ api_key: '' }, $('#btn-key-clear'), '已清除 Key');
    };
    $('#btn-kb-search').onclick = searchKb;
    $('#kb-query').onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); searchKb(); } };
  }

  document.addEventListener('DOMContentLoaded', init);
})();
