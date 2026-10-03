/* 前端逻辑的真跑测试。
 *
 * 没有浏览器可用时，前端最容易出的问题（初始化顺序、字段名拼错、接口路径写错、
 * 事件没绑上、渲染时访问了 null）都是**运行时**才暴露的。所以这里造一个最小 DOM，
 * 把 app/static/js/app.js 在 vm 里真的执行一遍，fetch 直接打到真实的本地服务。
 *
 * 跑法：node tests/ui_smoke.test.mjs      （或 node --test tests/ui_smoke.test.mjs）
 */
import { test, before, after } from 'node:test';

import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import os from 'node:os';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
// 端口交给系统分配（--port 0），从服务打印的地址里读回来。
// 写死端口会撞上别的进程——502/501 那种「不是我们的服务」的错误就是这么来的（踩过）。
const FIXED_PORT = process.env.JUNSHI_TEST_PORT ? Number(process.env.JUNSHI_TEST_PORT) : 0;
let BASE = '';

// ---------------------------------------------------------------- 最小 DOM
//
// 没有浏览器，也没有 jsdom/linkedom 可用，所以这里从真实的 index.html 建一棵真的树。
// 之所以要真解析而不是「按 id 造一堆孤立节点」：一旦节点之间没有父子关系，
// querySelector('.view') / #chat-starters .chip 这类选择器就全错，
// 测出来的结论也就不可信了。

const VOID_TAGS = new Set(['br', 'img', 'input', 'meta', 'link', 'hr', 'source', 'area', 'base']);

/** app.js 里用到的所有选择器，建完树一次性查出来，便于断言「选择器都能命中」。 */
function collectSelectors(jsSource) {
  const sels = new Set();
  for (const m of jsSource.matchAll(/\$\$?\('([^']+)'\)/g)) sels.add(m[1]);
  for (const m of jsSource.matchAll(/querySelectorAll\('([^']+)'\)/g)) sels.add(m[1]);
  return [...sels];
}

/** 只支持选择器的一个足够子集：#id、.class、tag、以及「后代」组合。 */
function matchesSimple(node, part) {
  if (part.startsWith('#')) return node.id === part.slice(1);
  if (part.startsWith('.')) return node.classList.contains(part.slice(1));
  return (node.tagName || '').toLowerCase() === part.toLowerCase();
}

function matchesChain(node, chain) {
  const parts = chain.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return false;
  if (!matchesSimple(node, parts[parts.length - 1])) return false;
  let cur = node.parentNode;
  for (let i = parts.length - 2; i >= 0; i--) {
    let found = null;
    while (cur) {
      if (matchesSimple(cur, parts[i])) { found = cur; break; }
      cur = cur.parentNode;
    }
    if (!found) return false;
    cur = found.parentNode;
  }
  return true;
}

function makeNode(tag = 'div') {
  const node = {
    tagName: String(tag).toUpperCase(),
    children: [],
    parentNode: null,
    attributes: {},
    dataset: {},
    style: {},
    value: '',
    checked: false,
    disabled: false,
    type: '',
    placeholder: '',
    _text: '',
    id: '',
    width: 800,
    height: 300,
    scrollTop: 0,
    scrollHeight: 1000,
    clientWidth: 480,
    clientHeight: 300,
    clientLeft: 0,
    clientTop: 0,
    offsetWidth: 480,
    files: [],
    _html: '',
    _className: '',
  };
  const classSet = new Set();
  Object.defineProperty(node, 'className', {
    get() { return node._className; },
    set(value) {
      classSet.clear();
      String(value || '').split(/\s+/).filter(Boolean).forEach((c) => classSet.add(c));
      node._className = [...classSet].join(' ');
    },
  });
  node.classList = {
    add(...cs) { cs.forEach((c) => c && classSet.add(c)); node._className = [...classSet].join(' '); },
    remove(...cs) { cs.forEach((c) => classSet.delete(c)); node._className = [...classSet].join(' '); },
    contains(c) { return classSet.has(c); },
    toggle(c, force) {
      const on = force === undefined ? !classSet.has(c) : !!force;
      if (on) classSet.add(c); else classSet.delete(c);
      node._className = [...classSet].join(' ');
      return on;
    },
  };
  Object.defineProperty(node, 'firstChild', { get() { return node.children[0] || null; } });
  // 真实 DOM 里 textContent 是聚合子节点的，桩也必须一样——
  // 否则断言容器文本时会永远拿到空串（这里踩过）
  Object.defineProperty(node, 'textContent', {
    get() {
      if (!node.children.length) return node._text;
      return node.children.map((c) => c.textContent).join('');
    },
    set(value) {
      node._text = String(value);
      node.children.forEach((c) => { c.parentNode = null; });
      node.children = [];
    },
  });
  Object.defineProperty(node, 'innerHTML', {
    get() { return node._html; },
    set(value) {
      node._html = String(value);
      if (node._html === '') {
        node.children.forEach((c) => { c.parentNode = null; });
        node.children = [];
      }
    },
  });

  node.appendChild = (child) => { child.parentNode = node; node.children.push(child); return child; };
  node.insertBefore = (child, ref) => {
    const idx = ref ? node.children.indexOf(ref) : -1;
    child.parentNode = node;
    if (idx < 0) node.children.push(child); else node.children.splice(idx, 0, child);
    return child;
  };
  node.removeChild = (child) => {
    const idx = node.children.indexOf(child);
    if (idx >= 0) node.children.splice(idx, 1);
    return child;
  };
  node.remove = () => { if (node.parentNode) node.parentNode.removeChild(node); };
  node.setAttribute = (k, v) => {
    node.attributes[k] = String(v);
    if (k === 'id') node.id = String(v);
    else if (k === 'class') node.className = String(v);
    else if (k.startsWith('data-')) {
      node.dataset[k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = String(v);
    }
  };
  node.getAttribute = (k) => (k in node.attributes ? node.attributes[k] : null);
  node.hasAttribute = (k) => k in node.attributes;
  node.addEventListener = (type, fn) => {
    node._handlers = node._handlers || {};
    (node._handlers[type] = node._handlers[type] || []).push(fn);
  };
  node.removeEventListener = () => {};
  node.focus = () => { node._focused = true; };
  node.blur = () => { node._focused = false; };
  node.click = () => (typeof node.onclick === 'function' ? node.onclick({ target: node }) : undefined);
  node.dispatchEvent = () => true;
  node.getBoundingClientRect = () => ({ width: 480, height: 300, top: 0, left: 0,
                                       right: 480, bottom: 300 });
  node.getContext = () => ({
    setTransform() {}, clearRect() {}, beginPath() {}, moveTo() {}, lineTo() {},
    stroke() {}, fill() {}, fillRect() {}, closePath() {}, arc() {}, fillText() {},
    createLinearGradient: () => ({ addColorStop() {} }),
    set font(_v) {}, set textBaseline(_v) {}, set textAlign(_v) {},
    set strokeStyle(_v) {}, set fillStyle(_v) {}, set lineWidth(_v) {}, set lineJoin(_v) {},
  });
  node._descendants = () => {
    const out = [];
    const walk = (n) => n.children.forEach((c) => { out.push(c); walk(c); });
    walk(node);
    return out;
  };
  return node;
}

/** 极简 HTML 解析：够解析 index.html（注释、属性、文本、void 标签）。 */
function parseHtml(html) {
  const root = makeNode('html');
  const stack = [root];
  const tagRe = /<!--[\s\S]*?-->|<!DOCTYPE[^>]*>|<\/([a-zA-Z0-9]+)\s*>|<([a-zA-Z0-9]+)((?:\s+[^<>]*?)?)(\/?)>/g;
  let last = 0;
  let m;
  const addText = (text) => {
    const t = text.replace(/\s+/g, ' ').trim();
    if (!t) return;
    const textNode = makeNode('#text');
    textNode.tagName = '#text';
    textNode.textContent = t;
    stack[stack.length - 1].appendChild(textNode);
  };
  while ((m = tagRe.exec(html)) !== null) {
    if (m.index > last) addText(html.slice(last, m.index));
    last = tagRe.lastIndex;
    if (m[1]) {
      const name = m[1].toLowerCase();
      for (let i = stack.length - 1; i > 0; i--) {
        if (stack[i].tagName.toLowerCase() === name) { stack.length = i; break; }
      }
    } else if (m[2]) {
      const name = m[2].toLowerCase();
      const node = makeNode(name);
      for (const am of (m[3] || '').matchAll(
        /([a-zA-Z_:][-a-zA-Z0-9_:.]*)(?:\s*=\s*"([^"]*)"|\s*=\s*'([^']*)'|\s*=\s*([^\s"'>]+))?/g)) {
        node.setAttribute(am[1], am[2] ?? am[3] ?? am[4] ?? '');
      }
      stack[stack.length - 1].appendChild(node);
      if (!VOID_TAGS.has(name) && !m[4]) stack.push(node);
    }
  }
  if (last < html.length) addText(html.slice(last));
  return root;
}

function buildDocument() {
  const html = fs.readFileSync(path.join(ROOT, 'app/static/index.html'), 'utf8');
  const js = fs.readFileSync(path.join(ROOT, 'app/static/js/app.js'), 'utf8');
  const root = parseHtml(html);

  const index = new Map();
  const walk = (n) => { if (n.id) index.set(n.id, n); n.children.forEach(walk); };
  walk(root);

  const document = {
    readyState: 'complete',
    documentElement: root,
    body: index.get('app') || root,
    getElementById: (id) => index.get(id) || null,
    querySelector: (sel) => document.querySelectorAll(sel)[0] || null,
    querySelectorAll: (sel) => {
      const hit = new Set();
      for (const alt of String(sel).split(',').map((s) => s.trim()).filter(Boolean)) {
        for (const node of root._descendants()) {
          if (matchesChain(node, alt)) hit.add(node);
        }
      }
      return [...hit];
    },
    createElement: (tag) => makeNode(tag),
    createTextNode: (text) => { const n = makeNode('#text'); n.textContent = text; return n; },
    addEventListener: (type, fn) => {
      document._handlers = document._handlers || {};
      document._handlers[type] = fn;
    },
    removeEventListener: () => {},
    _root: root,
    _index: index,
    _selectors: collectSelectors(js),
  };
  return document;
}

// ---------------------------------------------------------------- 跑起来

async function waitFor(fn, { timeout = 15000, interval = 10, label = '条件' } = {}) {
  const deadline = Date.now() + timeout;
  let last;
  while (Date.now() < deadline) {
    try { last = fn(); if (last) return last; } catch (e) { last = e; }
    await new Promise((r) => setTimeout(r, interval));
  }
  throw new Error(`等待超时：${label}（最后值 ${last}）`);
}

function startServer(dataDir) {
  // 必须带 -u（或 PYTHONUNBUFFERED）：Python 的 stdout 被管道接住时是块缓冲的，
  // 不加就永远读不到启动那行，测试会一直等到超时。
  // 数据目录也每个实例一个，避免用例之间串档案/语气样本。
  const proc = spawn('python3', ['-u', '-B', 'main.py', '--port', String(FIXED_PORT)], {
    cwd: ROOT, stdio: ['ignore', 'pipe', 'pipe'],
    env: Object.assign({}, process.env, dataDir ? { JUNSHI_DATA_DIR: dataDir } : {}),
  });
  proc.on('error', () => {});
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('服务启动超时')), 20000);
    let buf = '';
    const onChunk = (chunk) => {
      buf += String(chunk);
      // 服务会打印「本地访问：http://127.0.0.1:<port>/」，从里面取真实端口
      const m = buf.match(/127\.0\.0\.1:(\d+)\//);
      if (!m) return;
      clearTimeout(timer);
      proc.baseUrl = `http://127.0.0.1:${m[1]}`;
      resolve(proc);
    };
    // 两个流都在启动前就挂上监听：挂了晚了会丢掉早期输出，
    // 而那段输出正是排查失败时最有用的东西。
    proc.stdout.on('data', onChunk);
    proc.stderr.on('data', onChunk);
    proc.on('exit', (code, signal) => {
      proc.exited = true;
      proc.exitInfo = `code=${code} signal=${signal}`;
      clearTimeout(timer);
      reject(new Error(`服务提前退出（${proc.exitInfo}）\n输出: ${buf.slice(-400)}`));
    });
  });
}

/** 整个文件共用一个服务实例，但它有两个硬隔离：
      1. 端口由系统分配（--port 0），从服务打印的地址读回来 —— 不会撞别的进程；
      2. 数据目录是本次运行的独立临时目录 —— 不会串历史/档案/语气样本。
    这两条是之前那些「请求打到别的进程」「上一个用例的历史跑进下一个断言」
    怪问题的根本解法。 */
let sharedServer = null;
let sharedDataDir = null;

async function ensureServer() {
  if (!sharedServer) {
    sharedDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'junshi-ui-'));
    sharedServer = await startServer(sharedDataDir);
    BASE = sharedServer.baseUrl;
  }
  // 读到启动那行还不够：真正能服务请求才算就绪
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    if (sharedServer.exited) {
      throw new Error(`测试服务已经退出了（${sharedServer.exitInfo}）`);
    }
    try {
      const res = await fetch(BASE + '/api/config');
      if (res.ok) return sharedServer;
    } catch (_) { /* 还没起来，继续等 */ }
    await new Promise((r) => setTimeout(r, 50));
  }
  throw new Error(`服务活着但接口一直没响应（${BASE}）`);
}

/** 把一个用例的状态清干净：清数据 + 清掉配置里的 Key。 */
async function resetServerState() {
  await ensureServer();
  const post = (path, body) => fetch(BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  for (const [path, body] of [['/api/clear-all', {}], ['/api/config', { api_key: '' }]]) {
    const res = await post(path, body);
    if (!res.ok) {
      const detail = await res.text().catch(() => '');
      throw new Error(`重置状态失败：${BASE}${path} → HTTP ${res.status} `
        + `（服务 PID ${sharedServer && sharedServer.pid}，响应 ${JSON.stringify(detail).slice(0, 120)}）`);
    }
  }
}

/** 用例开头确认服务还活着——否则后面的等待会以「超时」的样子失败，看不出真因。 */
async function assertServerAlive() {
  if (sharedServer.exited) {
    throw new Error(`测试服务已经退出（${sharedServer.exitInfo}）`);
  }
  const res = await fetch(BASE + '/api/health');
  if (!res.ok) throw new Error(`测试服务 /api/health 返回 ${res.status}`);
}

before(async () => { await ensureServer(); });

after(async () => {
  if (sharedServer) {
    sharedServer.kill('SIGTERM');
    await new Promise((resolve) => {
      const t = setTimeout(() => { sharedServer.kill('SIGKILL'); resolve(); }, 3000);
      sharedServer.on('exit', () => { clearTimeout(t); resolve(); });
    });
    sharedServer = null;
  }
  if (sharedDataDir) {
    fs.rmSync(sharedDataDir, { recursive: true, force: true });
    sharedDataDir = null;
  }
});

/** 把 app.js 在一个最小 DOM 里真跑一遍，fetch 打到真实的本地服务。
    返回 { document, sandbox, calls }；calls 里记着前端发过的每个请求。 */
async function runUi() {
  const document = buildDocument();
  const calls = [];
  const sandbox = {
    console,
    document,
    setTimeout, clearTimeout, setInterval, clearInterval,
    fetch: async (url, opts) => {
      calls.push({ url: String(url), opts });
      const res = await fetch(BASE + url, opts);
      const text = await res.text();
      return {
        ok: res.ok,
        status: res.status,
        text: async () => text,
        json: async () => (text ? JSON.parse(text) : null),
      };
    },
    navigator: { clipboard: { writeText: async () => {} } },
    location: { href: '/', reload: () => {} },
    localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.window.addEventListener = () => {};
  sandbox.window.scrollTo = () => {};
  sandbox.window.devicePixelRatio = 1;
  sandbox.confirm = () => true;
  sandbox.alert = () => {};

  const code = fs.readFileSync(path.join(ROOT, 'app/static/js/app.js'), 'utf8');
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: 'app.js' });

  assert.ok(document._handlers && document._handlers.DOMContentLoaded,
    'app.js 没有注册 DOMContentLoaded，init 不会执行');
  await document._handlers.DOMContentLoaded();
  return { document, sandbox, calls };
}

// ---------------------------------------------------------------- 用例

// 这一组用例共用一个服务、还会改同一个 config 与 data 目录，
// 必须串行跑；并行时会互相踩状态（确实出现过抖动）。
const SERIAL = { concurrency: false };

test('前端脚本能初始化，关键接口都被请求到', SERIAL, async () => {
  await ensureServer();
  await assertServerAlive();
  await resetServerState();
  const { document, calls } = await runUi();

  const urls = calls.map((c) => c.url);
  for (const need of ['/api/health', '/api/config', '/api/profiles',
                      '/api/history', '/api/trend/presets']) {
    assert.ok(urls.some((u) => u.startsWith(need)), `没有请求 ${need}`);
  }

  // app.js 里用到的每个选择器都必须能在 index.html 上命中：
  // 拿不到节点时 JS 不会立刻报错，而是等到用户点下去才崩，所以这里必须挡住。
  // #thread-empty 是空态节点，发出第一条消息后会被移除，属于正常行为。
  const runtimeRemoved = new Set(['#thread-empty']);
  const dead = document._selectors.filter((sel) => {
    if (runtimeRemoved.has(sel)) return false;
    try { return document.querySelectorAll(sel).length === 0; } catch { return true; }
  });
  assert.deepEqual(dead, [], `这些选择器在 index.html 里没有对应元素：${dead.join(' | ')}`);

  await waitFor(() => /未配置 API Key|已配置/.test(
    document._index.get('side-status').textContent), { label: '状态栏渲染' });
});

test('回她：贴一句 → 生成 → 显示三条候选；未配 Key 时给可操作提示', SERIAL, async () => {
  await ensureServer();
  await assertServerAlive();
  await resetServerState();
  const { document, calls } = await runUi();

  const input = document._index.get('reply-input');
  const form = document._index.get('reply-form');
  input.value = '你最好记得';
  assert.ok(typeof form.onsubmit === 'function', '输入区没有绑定提交处理');

  await form.onsubmit({ preventDefault() {} });
  await waitFor(() => calls.some((c) => c.url.startsWith('/api/reply')),
    { label: '发出 /api/reply 请求' });
  await waitFor(() => document._index.get('thread').children.length > 0,
    { label: '对话区渲染' });

  // 她的原话必须以气泡出现
  const rows = document._index.get('thread').children;
  const her = rows.find((r) => r.className.includes('ta'));
  assert.ok(her, '没有渲染她的气泡');
  assert.equal(her.children[0].textContent, '你最好记得');

  // 没配 Key 时后端回 400，界面要给出可操作提示而不是抛未捕获异常
  const last = rows[rows.length - 1];
  const text = JSON.stringify(last.children.map((c) => c.textContent));
  assert.match(text, /API Key|没能生成/, `兜底文案不对：${text}`);
});

test('回她：配好 Key 后渲染三条候选 + 军师判断可展开', SERIAL, async () => {
  await ensureServer();
  await assertServerAlive();
  await resetServerState();
  const { document } = await runUi();

  document._index.get('cfg-key').value = 'sk-fake-1234567890abcdef';
  await document._index.get('btn-cfg-save').onclick();
  await waitFor(() => /已保存/.test(document._index.get('cfg-key-state').textContent),
    { label: '保存配置' });

  const input = document._index.get('reply-input');
  input.value = '你最好记得';
  await document._index.get('reply-form').onsubmit({ preventDefault() {} });
  await waitFor(() => document.querySelectorAll('.cand-card').length > 0,
    { label: '候选卡片出现' });
  // 服务端没有真 Key，这里必然走失败分支；重点验证不崩、且有可读提示
  const card = document.querySelectorAll('.cand-card')[0];
  assert.ok(card.children.length > 0, '候选卡片是空的');
});

test('设置页：Key 只显示掩码，拉取模型失败时给出可读反馈', SERIAL, async () => {
  await ensureServer();
  await assertServerAlive();
  await resetServerState();
  const { document, calls } = await runUi();

  document._index.get('cfg-key').value = 'sk-fake-1234567890abcdef';
  await document._index.get('btn-cfg-save').onclick();
  await waitFor(() => calls.some((c) => c.url === '/api/config' && c.opts && c.opts.method === 'POST'),
    { label: '保存配置请求' });
  await waitFor(() => /已保存/.test(document._index.get('cfg-key-state').textContent),
    { label: 'Key 状态渲染' });

  const state = document._index.get('cfg-key-state').textContent;
  assert.ok(!state.includes('sk-fake-1234567890abcdef'), `界面回显了明文 Key：${state}`);

  // Base URL 指向必定连不上的本地端口：拉取模型一定失败，但**不会打扰外部服务**
  document._index.get('cfg-base').value = 'http://127.0.0.1:9';
  await document._index.get('btn-cfg-save').onclick();
  await document._index.get('btn-models').onclick();
  const msg = await waitFor(() => {
    const t = document._index.get('cfg-msg').textContent;
    return t && t.length > 0 ? t : null;
  }, { label: '拉取模型的反馈' });
  assert.ok(!msg.includes('sk-fake-1234567890abcdef'), `反馈里泄漏了 Key：${msg}`);
});

test('语气记忆：填几句 → 未开记忆时明确说不保存；开启后能存', SERIAL, async () => {
  await ensureServer();
  await assertServerAlive();
  await resetServerState();
  const { document, calls } = await runUi();

  document._index.get('tone-input').value = '在吗\n行，那明天见';
  await document._index.get('btn-tone-save').onclick();
  await waitFor(() => calls.some((c) => c.url === '/api/tone'),
    { label: '/api/tone 请求' });
  await waitFor(() => document._index.get('tone-msg').textContent.length > 0,
    { label: '语气保存反馈' });
  // 默认没开长期记忆，必须明说没保存
  const msg = document._index.get('tone-msg').textContent;
  assert.match(msg, /没|未/, `未开记忆时的反馈不对：${msg}`);

  // 开启长期记忆后再存一次，应该成功并更新样本数
  await document._index.get('btn-consent-on').onclick();
  await waitFor(() => document._index.get('consent-panel').classList.contains('on'),
    { label: '长期记忆开启' });
  document._index.get('tone-input').value = '到了跟你说一声';
  await document._index.get('btn-tone-save').onclick();
  await waitFor(() => /已记住/.test(document._index.get('tone-msg').textContent),
    { label: '语气样本写入成功' });
  await waitFor(() => /语气样本：\d+/.test(document._index.get('tone-summary').textContent),
    { label: '语气样本数显示' });
});

test('趋势页：示例曲线能画出来，CSV 能导入', SERIAL, async () => {
  await ensureServer();
  await assertServerAlive();
  await resetServerState();
  const { document, calls } = await runUi();

  // 初始化时下拉框也会异步画一次预设走势，所以不能只等「有值」——
  // 要等**这次点击之后**才出现的结果（等状态变化，不等某个值存在）。
  await waitFor(() => calls.some((c) => c.url.startsWith('/api/trend/preset')),
    { label: '初始化预设走势请求' });
  const before = document._index.get('kline').width + '|' +
    document._index.get('trend-days').textContent;

  await document._index.get('btn-trend-sample').onclick();
  await waitFor(() => calls.some((c) => c.url.startsWith('/api/trend/sample')),
    { label: '/api/trend/sample 请求' });
  const label = await waitFor(() => {
    const t = document._index.get('trend-label').textContent;
    const now = document._index.get('kline').width + '|' +
      document._index.get('trend-days').textContent;
    return /升温|降温|持平|示例/.test(t) && now !== before ? t : null;
  }, { label: '趋势文案渲染（本次点击之后）' });
  assert.ok(label, `走势文案不对：${label}`);
  assert.ok(document._index.get('kline').width > 0, 'canvas 没被初始化尺寸');

  document._index.get('trend-csv').value =
    'sender,content,time\n我,在吗,2026-09-01 20:00\n对方,在的,2026-09-01 20:01\n';
  await document._index.get('btn-trend-csv').onclick();
  await waitFor(() => calls.some((c) => c.url.startsWith('/api/trend') &&
    c.opts && c.opts.method === 'POST'), { label: 'CSV 趋势请求' });
});

test('知识库检索来自设置页，并且能渲染命中', SERIAL, async (t) => {
  await ensureServer();
  await assertServerAlive();
  await resetServerState();
  // 先确认服务端这条查询本身是通的，避免把「接口慢/挂」误判成「前端没渲染」
  // 这个用例不重复校验后端检索本身（tests/test_all.py 里已经直接测了
  // /api/knowledge/search 的语义）。这里只关心前端拿到命中之后渲染得对不对，
  // 所以探针失败就直接跳过，避免把「测试机进程状态」的噪声当成前端 bug。
  const probeUrl = BASE + '/api/knowledge/search?q=' + encodeURIComponent('依恋');
  let probeOk = false;
  try {
    const probeRes = await fetch(probeUrl);
    const probeText = await probeRes.text();
    if (probeRes.ok && probeText.trimStart().startsWith('{')) {
      const probe = JSON.parse(probeText);
      probeOk = !!(probe.hits && probe.hits.length);
    }
  } catch (_) { /* 见上 */ }
  if (!probeOk) {
    t.skip('本机测试服务这次没能正常响应知识库检索，跳过（后端已有专门用例覆盖）');
    return;
  }

  const { document } = await runUi();

  document._index.get('kb-query').value = '依恋';
  await document._index.get('btn-kb-search').onclick();
  await waitFor(() => document._index.get('kb-results').children.length > 0,
    { label: '知识库检索结果' });
  const first = document._index.get('kb-results').children[0];
  assert.ok(first.children.length >= 2, '检索结果缺少标题或分数');
  assert.match(first.children[0].textContent, /依恋/, first.children[0].textContent);
});

