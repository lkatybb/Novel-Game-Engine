/* ==========================================================================
 * 小说互动游戏引擎 - 前端逻辑
 * 模块划分：API 封装 / 书架（launcher）/ 游戏（SSE + 打字机）/ 菜单 / 工具
 * 数据契约（后端 SSE）：
 *   {type:"scene",  text}                 场景文本（打字机）
 *   {type:"npc",    speaker, text}        NPC 台词（speaker 标签 + 打字机）
 *   {type:"choices", options:[...]}       选项按钮
 *   {type:"state",  state:{...}}          全局状态（位置/时间线）
 *   {type:"done"}                         本轮结束
 *   {type:"error",  message}              错误
 * ========================================================================== */

/* --------------------------------------------------------------------------
 * 0. DOM 引用与全局状态
 * -------------------------------------------------------------------------- */
const $ = (id) => document.getElementById(id);

const dom = {
  body: document.body,
  // 书架
  fileInput: $('fileInput'), importBtn: $('importBtn'),
  libraryView: $('libraryView'), novelMenuList: $('novelMenuList'),
  detailView: $('detailView'), backToLibrary: $('backToLibrary'),
  detailTitle: $('detailTitle'), detailMeta: $('detailMeta'),
  sessionMenuList: $('sessionMenuList'),
  // 游戏
  app: $('app'), launcher: $('launcher'),
  storyKicker: $('storyKicker'), storyTitleLine: $('storyTitleLine'),
  reader: $('reader'), stream: $('stream'), turnPage: $('turnPage'), jumpBottom: $('jumpBottom'),
  typingIndicator: $('typingIndicator'),
  choices: $('choices'), freeForm: $('freeForm'), freeInput: $('freeInput'),
  meterFill: $('meterFill'), meterText: $('meterText'),
  progressFill: $('progressFill'),
  // 菜单
  menuPanel: $('menuPanel'), menuSummary: $('menuSummary'),
  timelinePanel: $('timelinePanel'), timelineList: $('timelineList'),
  statusPanel: $('statusPanel'), inventoryList: $('inventoryList'), flagsList: $('flagsList'),
  chatBtn: $('chatBtn'), chatPanel: $('chatPanel'), chatNpcSelect: $('chatNpcSelect'),
  chatLog: $('chatLog'), chatForm: $('chatForm'), chatInput: $('chatInput'), chatSend: $('chatSend'),
  resumeBtn: $('resumeBtn'), timelineBtn: $('timelineBtn'), statusBtn: $('statusBtn'),
  graphBtn: $('graphBtn'), homeBtn: $('homeBtn'), restartBtn: $('restartBtn'),
  backBtn: $('backBtn'), menuBtn: $('menuBtn'),
  toast: $('toast'),
  // 翻页阅读 / 场景弹窗
  tapHint: $('tapHint'),
  sceneOverlay: $('sceneOverlay'), sceneName: $('sceneName'), sceneDesc: $('sceneDesc'),
};

/** 应用全局状态（单一数据源） */
const app = {
  sessionId: null,   // 当前会话 id
  novelId: null,     // 当前小说 id
  novelTitle: '',    // 当前小说标题
  busy: false,       // SSE 进行中，禁止重复提交
  state: null,       // 最近一次后端推送的全局状态
  // 实时分页状态机
  pendingChoices: null,    // SSE 提前到达的选项，末页播完才渲染
  pager: null,             // 分页播放器状态（beginTurn 创建）
  sceneModalOpen: false,   // 场景弹窗打开时屏蔽翻页点击
  chat: { npc: '', busy: false, logs: {} },  // 私聊记录只在前端：服务端只读，不落盘
  chatRoster: null,        // 本作可私聊角色名（首次打开时拉取）
};

/* --------------------------------------------------------------------------
 * 1. 工具函数
 * -------------------------------------------------------------------------- */

/** Toast 轻提示（2.6s 自动消失） */
let toastTimer = null;
function toast(msg) {
  dom.toast.textContent = msg;
  dom.toast.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => dom.toast.classList.remove('show'), 2600);
}

/** 统一 JSON 请求封装：非 2xx 抛错并携带后端 detail */
async function api(path, { method = 'GET', body, formData } = {}) {
  const opts = { method };
  if (formData) opts.body = formData;
  else if (body !== undefined) {
    opts.headers = { 'Content-Type': 'application/json' };
    opts.body = JSON.stringify(body);
  }
  let resp;
  try {
    resp = await fetch(path, opts);
  } catch (netErr) {
    throw new Error('网络连接失败，请确认服务已启动');
  }
  if (!resp.ok) {
    let detail = `请求失败 (${resp.status})`;
    try {
      const err = await resp.json();
      if (err.detail) detail = typeof err.detail === 'string' ? err.detail : JSON.stringify(err.detail);
    } catch { /* 响应非 JSON，保持默认文案 */ }
    throw new Error(detail);
  }
  return resp.json();
}

/** 切换 明/暗 主题 */
function setColorMode(mode) {
  dom.body.dataset.colorMode = mode;
  try { localStorage.setItem('ng-color-mode', mode); } catch { /* 忽略 */ }
}
(function restoreColorMode() {
  try {
    const saved = localStorage.getItem('ng-color-mode');
    if (saved) dom.body.dataset.colorMode = saved;
  } catch { /* 忽略 */ }
})();

/* --------------------------------------------------------------------------
 * 2. 书架模块（launcher 首页）
 * -------------------------------------------------------------------------- */

/** 拉取并渲染书架：只显示书名（右对齐菜单式），点击进入该书存档页 */
async function loadLibrary() {
  dom.novelMenuList.innerHTML = '';
  const empty = document.createElement('div');
  empty.className = 'empty-hint';
  empty.textContent = '加载中…';
  dom.novelMenuList.appendChild(empty);

  try {
    const data = await api('/api/novel/list');
    const novels = data.novels || [];
    dom.novelMenuList.innerHTML = '';
    if (!novels.length) {
      empty.textContent = '还没有小说。点击上方「上传新小说」开始。';
      dom.novelMenuList.appendChild(empty);
      return;
    }
    for (const nv of novels) {
      const count = (nv.sessions || []).length;
      const item = buildMenuItem(nv.title || nv.novel_id, count ? `${count} 个存档` : '尚未开始');
      item.addEventListener('click', () => showNovelDetail(nv));
      dom.novelMenuList.appendChild(item);
    }
  } catch (e) {
    empty.textContent = `书架加载失败：${e.message}`;
    dom.novelMenuList.appendChild(empty);
  }
}

/** 构造菜单行：主文字 + 右侧箭头（Story-to-Game 风格，细线分隔） */
function buildMenuItem(label, sub) {
  const item = document.createElement('button');
  item.className = 'menu-item';
  const text = document.createElement('span');
  text.className = 'menu-label';
  text.textContent = label;
  item.appendChild(text);
  if (sub) {
    const subEl = document.createElement('span');
    subEl.className = 'menu-sub';
    subEl.textContent = sub;
    item.appendChild(subEl);
  }
  const arrow = document.createElement('span');
  arrow.className = 'menu-arrow';
  arrow.textContent = '→';
  item.appendChild(arrow);
  return item;
}

/** 书架页 ↔ 书详情页 切换 */
function showLibrary() {
  dom.detailView.hidden = true;
  dom.libraryView.hidden = false;
}

/** 进入某本小说的存档页：开始新游戏 + 全部存档记录 */
function showNovelDetail(nv) {
  dom.libraryView.hidden = true;
  dom.detailView.hidden = false;
  dom.detailTitle.textContent = nv.title || nv.novel_id;
  const sessions = nv.sessions || [];
  dom.detailMeta.textContent = sessions.length
    ? `${nv.title || nv.novel_id} · ${sessions.length} 个存档`
    : `${nv.title || nv.novel_id} · 尚未有存档`;

  dom.sessionMenuList.innerHTML = '';
  // 第一项：开始新游戏
  const newGame = buildMenuItem('开始新故事');
  newGame.classList.add('menu-item-strong');
  newGame.addEventListener('click', () => startGame(nv.novel_id, nv.title || nv.novel_id));
  dom.sessionMenuList.appendChild(newGame);

  // 全部存档记录（最近在上）
  for (const sess of [...sessions].reverse()) {
    const label = sess.last_action ? String(sess.last_action).slice(0, 16) : '（游戏开始）';
    const item = buildMenuItem(label, '继续阅读');
    item.title = sess.last_action || '继续上次进度';
    item.addEventListener('click', () => resumeGame(sess.session_id, nv.novel_id, nv.title || nv.novel_id));
    item.appendChild(buildDeleteEntry('删除这条存档', () => deleteSession(nv, sess.session_id)));
    dom.sessionMenuList.appendChild(item);
  }

  // 危险操作：删除整本小说（含全部存档与向量库）
  const delNovel = buildMenuItem('删除这本小说', '不可恢复');
  delNovel.classList.add('menu-item-danger');
  delNovel.addEventListener('click', () => deleteNovel(nv));
  dom.sessionMenuList.appendChild(delNovel);
}

/** 菜单行右侧的删除入口（用 span 而非 button：按钮不能嵌套按钮） */
function buildDeleteEntry(title, onDelete) {
  const del = document.createElement('span');
  del.className = 'menu-del';
  del.textContent = '✕';
  del.title = title;
  del.addEventListener('click', (e) => {
    e.stopPropagation();          // 不触发所在行的"继续阅读"
    onDelete();
  });
  return del;
}

/** 删除一条存档（破坏性操作：confirm 二次确认后才发请求） */
async function deleteSession(nv, sessionId) {
  const title = nv.title || nv.novel_id;
  if (!confirm(`确定删除这条存档？\n\n《${title}》\n存档：${sessionId}\n\n删除后不可恢复。`)) return;
  try {
    await api(`/api/game/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' });
    toast('存档已删除');
    await reloadNovelDetail(nv.novel_id);
  } catch (e) {
    toast(`删除失败：${e.message}`);
  }
}

/** 删除后从服务器重新拉书架并回到该书详情页（禁止用本地数组拼状态） */
async function reloadNovelDetail(novelId) {
  const data = await api('/api/novel/list');
  const nv = (data.novels || []).find((n) => n.novel_id === novelId);
  if (!nv) { showLibrary(); await loadLibrary(); return; }
  showNovelDetail(nv);
}

/** 删除整本小说（破坏性操作：confirm 二次确认后才发请求） */
async function deleteNovel(nv) {
  const title = nv.title || nv.novel_id;
  if (!confirm(`确定删除《${title}》？\n\n将同时删除这本书的全部存档与向量库，删除后不可恢复。`)) return;
  try {
    const data = await api(`/api/novel/${encodeURIComponent(nv.novel_id)}`, { method: 'DELETE' });
    toast(`已删除《${title}》（连带存档 ${data.sessions_deleted ?? 0} 条）`);
    showLibrary();
    await loadLibrary();          // 重新拉服务器书架，不用本地数组拼状态
  } catch (e) {
    toast(`删除失败：${e.message}`);
  }
}

/** 上传小说 → 自动开始新游戏 */
async function uploadNovel(file) {
  if (!file) return;
  if (file.size > 10 * 1024 * 1024) { toast('文件超过 10MB 上限'); return; }
  dom.importBtn.disabled = true;
  toast('正在上传并解析小说，约 10-20 秒…');
  try {
    const fd = new FormData();
    fd.append('file', file);
    const data = await api('/api/novel/upload', { method: 'POST', formData: fd });
    // 后端校验失败时返回 200 + {error}，需显式检查
    if (data.error) throw new Error(data.error);
    toast('上传成功，正在进入故事…');
    await startGame(data.novel_id, file.name.replace(/\.(txt|md)$/i, ''));
  } catch (e) {
    toast(`上传失败：${e.message}`);
  } finally {
    dom.importBtn.disabled = false;
    dom.fileInput.value = '';
  }
}

/* --------------------------------------------------------------------------
 * 3. 游戏模块：进入 / 恢复 / SSE 动作
 * -------------------------------------------------------------------------- */

/** 从书架进入游戏主界面 */
function enterGame(title) {
  app.novelTitle = title || app.novelTitle;
  dom.storyTitleLine.textContent = app.novelTitle;
  dom.launcher.hidden = true;
  dom.app.hidden = false;
  clearStage();
}

/** 回到书架 */
function goHome() {
  closeMenu();
  resetChat();
  dom.app.hidden = true;
  dom.launcher.hidden = false;
  app.sessionId = null;
  app.novelId = null;
  showLibrary();          // 重置到书架第一页（避免停留在详情页旧状态）
  loadLibrary();
}

/** 清空舞台：分页器、流容器、选项、加载提示、滚动跟随状态 */
function clearStage() {
  if (app.pager && app.pager.timer) clearInterval(app.pager.timer);
  app.pager = null;
  hideThinkingCursor();
  // 保留 stream-spacer 与 #turnPage 骨架，清空动态块
  dom.turnPage.innerHTML = '';
  dom.stream.querySelectorAll('.block-echo').forEach((el) => el.remove());
  dom.stream.classList.remove('paging');
  delete dom.stream.dataset.page;
  dom.choices.innerHTML = '';
  hideTyping();
  followBottom = true;
  dom.jumpBottom.hidden = true;
  // 弹窗 / 提示复位
  app.pendingChoices = null;
  app.sceneModalOpen = false;
  dom.tapHint.hidden = true;
  dom.sceneOverlay.classList.remove('show');
  dom.sceneOverlay.hidden = true;
}

/** 开始新游戏 */
async function startGame(novelId, title) {
  try {
    toast('正在生成开场…');
    const data = await api('/api/game/start', {
      method: 'POST',
      body: { novel_id: novelId },
    });
    app.novelId = novelId;
    app.sessionId = data.session_id;
    resetChat();                          // 换会话：私聊面板与角色列表重来
    enterGame(title);
    applyState(data.state);
    beginTurn({ scene: data.scene || null });
    app.pendingChoices = Array.isArray(data.choices) ? data.choices : null;
    appendSegment('scene', '', data.story || data.opening || '故事开始了。');
    app.pager.done = true;                // 非流式：内容已齐，播放器自行播完出选项
    updateProgress();
  } catch (e) {
    toast(`开始失败：${e.message}`);
  }
}

/** 恢复历史会话（契约：POST /api/game/resume {session_id}） */
async function resumeGame(sessionId, novelId, title) {
  try {
    const data = await api('/api/game/resume', {
      method: 'POST',
      body: { session_id: sessionId },
    });
    app.novelId = novelId;
    app.sessionId = data.session_id;
    resetChat();                          // 换会话：私聊面板与角色列表重来
    enterGame(title || data.novel_id);
    applyState(data.state);
    beginTurn({ scene: null });            // 恢复存档不弹场景窗
    app.pendingChoices = Array.isArray(data.choices) ? data.choices : null;
    appendSegment('scene', '', (data.last_story || '已回到上次的进度，继续你的故事。'));
    app.pager.done = true;
    toast('已恢复存档');
  } catch (e) {
    toast(`恢复失败：${e.message}`);
  }
}

/** 发送玩家动作（SSE 流式接收） */
async function sendAction(text) {
  const action = (text || '').trim();
  if (!action || app.busy || !app.sessionId) return;

  app.busy = true;
  setInteractionEnabled(false);
  clearStage();                          // 翻页：清空上一轮内容，进入新页面
  beginTurn({});                         // 新分页器：正文未到，思考光标等待
  setStageText('正在理解你的行动…');
  showTyping();                          // 底部三点脉冲
  appendEchoStatic(action);              // 玩家回显直接显示（不打字），新页开头
  showThinkingCursor();                  // 回显下方闪烁光标，DM 正在思考

  // 90s 无响应自动中断，防止 LLM 挂起时界面永久锁死
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 90000);
  try {
    const resp = await fetch('/api/game/action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: app.sessionId, novel_id: app.novelId, action }),
      signal: controller.signal,
    });
    if (!resp.ok || !resp.body) throw new Error(`服务异常 (${resp.status})`);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    // 逐块读取 SSE 流，按空行分包
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split('\n\n');
      buffer = parts.pop();                       // 最后一段可能不完整，留到下一轮
      for (const part of parts) handleSsePart(part);
    }
  } catch (e) {
    toast(e.name === 'AbortError' ? '请求超时（90秒无响应），请重试' : `请求失败：${e.message}`);
  } finally {
    clearTimeout(timeoutId);
    app.busy = false;
    setInteractionEnabled(true);
    hideThinkingCursor();
    hideTyping();
    updateProgress();
    // SSE 结束：播放器播完末页后自行 finishTurn 出选项；空内容也由 playTick 兜底
    if (app.pager) app.pager.done = true;
  }
}

/** 解析一条 SSE data帧并分发渲染 */
function handleSsePart(part) {
  const line = part.split('\n').find((l) => l.startsWith('data:'));
  if (!line) return;
  let evt;
  try { evt = JSON.parse(line.slice(5).trim()); } catch { return; }

  switch (evt.type) {
    case 'stage':
      // 阶段提示：推理过程反馈（理解行动 / 等待NPC / 推演剧情）
      setStageText(evt.text || '');
      break;
    case 'scene':
      // 增量进入分页段缓冲，由 playTick 按页播放（任何时刻只显示当前页 2-3 行）
      appendSegment('scene', '', evt.text || '');
      break;
    case 'npc':
      appendSegment('npc', evt.speaker || '', evt.text || '');
      break;
    case 'scene_change':
      // 后端保证该事件先于一切正文到达：立即弹窗并拦阻播放，关闭后从首页开始
      if (app.pager && evt.name) {
        app.pager.gated = true;
        openSceneModal(evt.name, evt.desc || '');
      }
      break;
    case 'choices':
      // 选项延迟到正文最后一页才出现
      app.pendingChoices = evt.options || [];
      break;
    case 'state':
      applyState(evt.state);
      break;
    case 'error':
      toast(evt.message || '发生错误');
      break;
    case 'done':
    default:
      break;   // done：无需处理，finally 里统一解锁
  }
}

/** 启用 / 禁用 交互（选项、输入框） */
function setInteractionEnabled(enabled) {
  dom.freeInput.disabled = !enabled;
  dom.choices.querySelectorAll('button').forEach((b) => { b.disabled = !enabled; });
}

/* --------------------------------------------------------------------------
 * 4. 实时分页播放器
 *    SSE 的 scene/npc 增量按「段」缓冲，边到达边重新分页（每页 2-3 行）；
 *    正文不再逐字打字：整页像选项卡一样从底部渐进弹出（带触底回弹），
 *    轻触后当前页反向沉回淡出，下一页弹出；末页定稿后才出现选项。
 *    场景弹窗打开期间 gated：任何文字都不显示，关闭后从首页弹出。
 * -------------------------------------------------------------------------- */
let followBottom = true;   // 等待期短内容是否跟随（保留上翻/回到底部交互）
const PAGER_TICK = 200;    // 协调心跳 ms（铺增量文字、检测定稿；非动画驱动）
const PAGE_OUT_MS = 280;   // 当前页下沉淡出时长，与 CSS pageSink 一致
const PAGE_IN_MS = 500;    // 下一页弹出时长，与 CSS pagePop 一致

/** 开启新一轮：复位分页状态机；scene 有效时先弹窗（此时正文一个字都未显示） */
function beginTurn({ scene } = {}) {
  app.pendingChoices = null;
  app.pager = {
    segs: [],          // 已到达内容段 [{kind:'npc'|'scene', speaker, text}]
    pages: [],         // 扁平化页 [{kind, speaker, text}]，每段独立分页
    idx: 0,            // 当前页序号
    done: false,       // 本轮 SSE 是否已结束
    gated: false,      // 场景弹窗拦阻中
    animating: false,  // 页面弹出/沉回动画锁
    dirty: false,      // segs 有增量，等待心跳重算分页
    finished: false,   // 末页定稿已出选项，播放器收工
    timer: null,
  };
  dom.stream.classList.add('paging');
  dom.stream.dataset.page = '0';
  dom.turnPage.innerHTML = '';
  dom.tapHint.hidden = true;
  if (scene && scene.name) {
    app.pager.gated = true;
    openSceneModal(scene.name, scene.desc || '');
  }
  app.pager.timer = setInterval(playTick, PAGER_TICK);
}

/** SSE 增量入段：同类同说话人的连续增量合并为同一段 */
function appendSegment(kind, speaker, text) {
  const p = app.pager;
  if (!p || text == null || !String(text)) return;
  const chunk = String(text);
  const spk = speaker || '';
  const tail = p.segs[p.segs.length - 1];
  if (tail && tail.kind === kind && tail.speaker === spk) tail.text += chunk;
  else p.segs.push({ kind, speaker: spk, text: chunk });
  p.dirty = true;
  hideTyping();
  hideThinkingCursor();
}

/* ---- 离屏测高分页：探针结构/宽度与真实页完全一致，零闪烁 ---- */
let measureProbe = null;
function getProbe(kind, speaker) {
  // renderPageShell 的 innerHTML='' 会清掉探针，用 isConnected 判定后重建
  if (!measureProbe || !measureProbe.isConnected) {
    measureProbe = document.createElement('div');
    measureProbe.style.cssText =
      'position:absolute;left:0;top:0;visibility:hidden;pointer-events:none;';
    dom.turnPage.appendChild(measureProbe);
  }
  measureProbe.style.width = `${dom.turnPage.clientWidth}px`;
  measureProbe.innerHTML = '';
  const el = document.createElement('div');
  el.className = `block block-${kind}`;
  if (kind === 'npc' && speaker) {
    const sp = document.createElement('div');
    sp.className = 'speaker';
    sp.textContent = speaker;
    el.appendChild(sp);
  }
  const body = document.createElement('div');
  body.className = 'story-text';
  el.appendChild(body);
  measureProbe.appendChild(el);
  return body;   // 测高用 body.parentNode.offsetHeight（含 speaker 标签高度）
}

/** 按字符二分硬切：保证任意片段测高 ≤ maxH（超长无标点句的兜底） */
function hardSplit(text, measure, maxH) {
  const out = [];
  let rest = text;
  while (rest) {
    if (measure(rest) <= maxH) { out.push(rest); break; }
    let lo = 1;
    let hi = rest.length;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (measure(rest.slice(0, mid)) <= maxH) lo = mid;
      else hi = mid - 1;
    }
    out.push(rest.slice(0, lo));
    rest = rest.slice(lo);
  }
  return out;
}

/** 单段独立分页：贪心打包完整句，每页 3 行正文；
 *  extraH（回显块高度）只让该段**首页**让位，且首页保底 2 行，不挤压成单行页 */
function paginateSeg(seg, extraH = 0) {
  const body = getProbe(seg.kind, seg.speaker);
  const shell = body.parentNode;                    // .block：含 speaker 标签的整块
  body.textContent = '国';
  const lineH = body.offsetHeight || 44;
  const chromeH = Math.max(0, shell.offsetHeight - lineH);   // 正文以外的固定占高（speaker）
  const maxH = chromeH + lineH * 3;                          // 每页 3 行正文
  const firstMaxH = extraH > 0
    ? Math.max(chromeH + lineH * 2, maxH - extraH)           // 首页让出回显，但不少于 2 行
    : maxH;
  const measure = (s) => { body.textContent = s; return shell.offsetHeight; };

  const units = [];
  splitSentences(seg.text).forEach((s) => {
    if (measure(s) <= maxH) {
      units.push(s);
      return;
    }
    // 单句超页：先按逗号等次级停顿切；仍超则按字符硬切
    (s.match(/[^，；、,;]+[，；、,;]*/g) || [s]).forEach((u) => {
      if (measure(u) <= maxH) units.push(u);
      else units.push(...hardSplit(u, measure, maxH));
    });
  });

  const texts = [];
  let cur = '';
  units.forEach((u) => {
    const limit = texts.length === 0 ? firstMaxH : maxH;   // 首页限额更小
    const cand = cur + u;
    if (!cur || measure(cand) <= limit) cur = cand;
    else { texts.push(cur.trim()); cur = u; }
  });
  if (cur.trim()) texts.push(cur.trim());
  return texts.map((text) => ({ kind: seg.kind, speaker: seg.speaker, text }));
}

/** 依据当前 segs 重新扁平化分页；已读页（idx 之前）冻结，防止尾部重排扰动已读内容 */
function rebuildPages() {
  const p = app.pager;
  if (!p) return [];
  p.dirty = false;
  const echoEl = dom.stream.querySelector('.block-echo');
  const echoH = echoEl ? echoEl.getBoundingClientRect().height + 18 : 0;  // 含回显上边距
  const flat = [];
  p.segs.forEach((seg, si) => flat.push(...paginateSeg(seg, si === 0 ? echoH : 0)));
  for (let i = 0; i < p.idx && i < p.pages.length && i < flat.length; i++) {
    flat[i] = p.pages[i];
  }
  p.pages = flat;
  return p.pages;
}

/** 构建当前页空壳（.block + .story-text[文本节点+光标]），返回文本节点；
 *  animate=true 时整块带「底部弹出+回弹」入场动画 */
function renderPageShell(animate = true) {
  const p = app.pager;
  const page = p.pages[p.idx];
  dom.turnPage.innerHTML = '';
  const el = document.createElement('div');
  el.className = `block block-${page.kind} show`;
  el.dataset.pidx = String(p.idx);
  if (page.kind === 'npc' && page.speaker) {
    const sp = document.createElement('div');
    sp.className = 'speaker';
    sp.textContent = page.speaker;
    el.appendChild(sp);
  }
  const body = document.createElement('div');
  body.className = 'story-text';
  const txt = document.createTextNode('');
  const cursor = document.createElement('span');
  cursor.className = 'cursor';
  body.appendChild(txt);
  body.appendChild(cursor);
  el.appendChild(body);
  dom.turnPage.appendChild(el);
  if (animate) el.classList.add('page-in');   // CSS 负责弹出回弹，JS 只加类
  dom.stream.dataset.page = String(p.idx);
  dom.reader.scrollTo({ top: 0, behavior: 'auto' });  // 新页从阅读区顶部开始
  return txt;
}

/** 播放器心跳：流式增量即时铺满当前页 → 页满等轻触 → 末页定稿出选项 */
function playTick() {
  const p = app.pager;
  if (!p || p.gated || p.animating || p.finished) return;
  if (p.dirty) rebuildPages();
  if (!p.pages.length) {
    if (p.done) finishTurn();    // 本轮无任何正文（异常/纯空回应）
    return;                      // 否则保留思考光标，继续等增量
  }
  const page = p.pages[p.idx];
  let txt = dom.turnPage.querySelector(`.block[data-pidx="${p.idx}"] .story-text`)?.firstChild;
  if (!txt) txt = renderPageShell(true);    // 首次出现：从底部弹出+回弹

  // 流式到达的新文字即时铺到当前页（仅改文本，不重启动画）
  if (txt.nodeValue !== page.text) txt.nodeValue = page.text;

  if (p.idx < p.pages.length - 1) {
    dom.tapHint.hidden = false;            // 后续页已在缓冲中，等轻触
  } else {
    dom.tapHint.hidden = true;
    if (p.done) finishTurn();
  }
  // 最后一页且流未结束：光标原位闪烁等待后续增量，不显示提示
}

/** 轻触翻页：当前页下沉淡出 → 下一页底部弹出（动画期间加锁，忽略连点） */
function nextPage() {
  const p = app.pager;
  if (!p || p.gated || p.animating || p.finished) return;
  if (p.dirty) rebuildPages();
  if (p.idx >= p.pages.length - 1) return;
  p.animating = true;
  dom.tapHint.hidden = true;

  const cur = dom.turnPage.querySelector(`.block[data-pidx="${p.idx}"]`);
  cur?.classList.remove('page-in');
  // 强制重排后再加 page-out，确保淡出动画从头播放
  if (cur) { void cur.offsetWidth; cur.classList.add('page-out'); }

  setTimeout(() => {
    if (!app.pager || app.pager !== p) return;   // 已被新一轮替换，放弃旧回调
    p.idx += 1;
    const txt = renderPageShell(true);           // 下一页弹出+回弹
    txt.nodeValue = p.pages[p.idx].text;
    setTimeout(() => { if (app.pager === p) p.animating = false; }, PAGE_IN_MS);
  }, PAGE_OUT_MS);
}

/** 末页播完且流结束：移除光标、渲染选项，播放器收工 */
function finishTurn() {
  const p = app.pager;
  if (!p || p.finished) return;
  p.finished = true;
  dom.tapHint.hidden = true;
  hideThinkingCursor();
  dom.turnPage.querySelectorAll('.cursor').forEach((c) => c.remove());
  // 空选项轮不补假「继续」按钮（那会让玩家误以为剧情在推进）；
  // 只清空上一轮选项 + 提示，玩家可用下方自由输入继续
  const options = Array.isArray(app.pendingChoices) ? app.pendingChoices : [];
  renderChoices(options);
  if (!options.length) toast('剧情接收不完整，可在下方自行输入行动');
  if (p.timer) { clearInterval(p.timer); p.timer = null; }
}

/** 滚动阅读区到底部（instant=true 用瞬时定位，否则平滑） */
function scrollToBottom(instant) {
  dom.reader.scrollTo({ top: dom.reader.scrollHeight, behavior: instant ? 'auto' : 'smooth' });
}

/** 用户上翻查看历史 → 停止跟随并出现「回到底部」；接近底部 → 恢复跟随 */
dom.reader.addEventListener('scroll', () => {
  const nearBottom = dom.reader.scrollHeight - dom.reader.scrollTop - dom.reader.clientHeight < 80;
  followBottom = nearBottom;
  dom.jumpBottom.hidden = nearBottom;
});
dom.jumpBottom.addEventListener('click', () => {
  followBottom = true;
  scrollToBottom(true);
});

/** 轻触阅读区翻到下一页（排除控件点击与文本选择，防误触） */
dom.reader.addEventListener('click', (e) => {
  if (!app.pager || app.sceneModalOpen) return;
  if (!dom.menuPanel.classList.contains('is-hidden')) return;
  if (e.target.closest('button, a, input, textarea, select, label')) return;
  const sel = window.getSelection();
  if (sel && sel.toString().length) return;
  nextPage();
});

/** 场景弹窗：点击任意处关闭 */
dom.sceneOverlay.addEventListener('click', closeSceneModal);

/** 键盘可访问性：空格/→/回车/PageDown 翻页，Esc 关弹窗；输入框聚焦时不劫持 */
document.addEventListener('keydown', (e) => {
  if (app.sceneModalOpen) {
    if (e.key === 'Escape' || e.key === ' ' || e.key === 'Enter') {
      e.preventDefault();
      closeSceneModal();
    }
    return;
  }
  const tag = document.activeElement && document.activeElement.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'BUTTON') return;
  if (!app.pager) return;
  if (e.key === ' ' || e.key === 'ArrowRight' || e.key === 'Enter' || e.key === 'PageDown') {
    e.preventDefault();
    nextPage();
  }
});

/** 窗口尺寸变化：按新行高重新分页（防抖，保持当前页并直接整页显示） */
let pagerResizeTimer = null;
window.addEventListener('resize', () => {
  clearTimeout(pagerResizeTimer);
  pagerResizeTimer = setTimeout(() => {
    const p = app.pager;
    if (!p || !p.pages.length) return;
    p.dirty = true;
    rebuildPages();
    p.idx = Math.min(p.idx, p.pages.length - 1);
    const txt = renderPageShell(false);          // resize 不重播弹出动画
    txt.nodeValue = p.pages[p.idx].text;
    if (p.finished) {
      dom.turnPage.querySelectorAll('.cursor').forEach((c) => c.remove());
    } else {
      dom.tapHint.hidden = p.idx >= p.pages.length - 1;
      if (p.idx >= p.pages.length - 1 && p.done) finishTurn();
    }
  }, 200);
});

/** 加载提示开关（LLM 推演等待期显示三点脉冲） */
function showTyping() { dom.typingIndicator.hidden = false; }
function hideTyping() { dom.typingIndicator.hidden = true; }
function setStageText(text) {
  const em = dom.typingIndicator.querySelector('em');
  if (em) em.textContent = text;
}

/** 思考光标：当前页区域内闪烁竖线，表示 DM 正在推演/书写（首个内容段到达即移除） */
function showThinkingCursor() {
  hideThinkingCursor();
  const el = document.createElement('div');
  el.className = 'block block-thinking';
  el.id = 'thinkingCursor';
  const body = document.createElement('div');
  body.className = 'story-text';
  const cursor = document.createElement('span');
  cursor.className = 'cursor';
  body.appendChild(cursor);
  el.appendChild(body);
  dom.turnPage.appendChild(el);
}
function hideThinkingCursor() {
  const el = document.getElementById('thinkingCursor');
  if (el) el.remove();
}

/** 玩家动作回显：直接静态显示（不打字），置于翻页容器之前，仅第 0 页可见 */
function appendEchoStatic(action) {
  const el = document.createElement('div');
  el.className = 'block block-echo show';
  const body = document.createElement('div');
  body.className = 'story-text';
  const p = document.createElement('p');
  p.textContent = `> ${action}`;
  body.appendChild(p);
  el.appendChild(body);
  dom.stream.insertBefore(el, dom.turnPage);
}

/* --------------------------------------------------------------------------
 * 4.6 切句 & 场景切换弹窗（分页主体见 4. 实时分页播放器）
 * -------------------------------------------------------------------------- */

/** 按句末标点切分完整句子（保留标点与收尾引号），换行视同断句 */
function splitSentences(text) {
  const out = [];
  const re = /([^。！？!?…\n]*[。！？!?…]+[」”’』）)]*)|([^。！？!?…\n]+)/g;
  let m;
  while ((m = re.exec(text))) {
    const seg = m[0].replace(/\n+/g, '').trim();
    if (seg) out.push(seg);
  }
  return out;
}

/** 场景切换弹窗：入场动画，关闭前正文播放器处于 gated 暂停态 */
function openSceneModal(name, desc) {
  app.sceneModalOpen = true;
  dom.sceneName.textContent = name;
  dom.sceneDesc.textContent = desc || '';
  dom.tapHint.hidden = true;
  dom.sceneOverlay.hidden = false;
  requestAnimationFrame(() => dom.sceneOverlay.classList.add('show'));
}

/** 关闭弹窗：解除 gated，playTick 从第一页开始播放（轻触提示由心跳自动管理） */
function closeSceneModal() {
  if (!app.sceneModalOpen) return;
  dom.sceneOverlay.classList.remove('show');
  app.sceneModalOpen = false;
  if (app.pager) app.pager.gated = false;
  setTimeout(() => { dom.sceneOverlay.hidden = true; }, 260);
}

/** 渲染选项按钮（→ 前缀大圆角） */
function renderChoices(options) {
  dom.choices.innerHTML = '';
  if (!Array.isArray(options) || !options.length) return;
  options.forEach((opt, idx) => {
    const label = String(opt ?? '').trim();      // 归一化，防 LLM 返回非字符串导致点击崩溃
    if (!label) return;
    const btn = document.createElement('button');
    btn.className = 'choice-btn';
    btn.style.animationDelay = `${idx * 0.07}s`;
    btn.textContent = label;                      // textContent 防注入
    btn.disabled = app.busy;
    btn.addEventListener('click', () => sendAction(label));
    dom.choices.appendChild(btn);
  });
}

/** 应用后端状态：顶部位置 + 时间线缓存 */
function applyState(state) {
  if (!state) return;
  app.state = state;
  const loc = state.player_location || state.location;
  if (loc) {
    dom.storyKicker.textContent = '当前位置';
    dom.storyTitleLine.textContent = loc;
  }
}

/** 更新进度条（已触发关键事件 / 总事件数） */
function updateProgress() {
  const total = app.state?.total || 0;
  const triggered = (app.state?.triggered || []).length;
  const pct = total ? Math.round((triggered / total) * 100) : 0;
  dom.meterFill.style.width = `${pct}%`;
  dom.progressFill.style.width = `${pct}%`;
  dom.meterText.textContent = total ? `${triggered} / ${total}` : '—';
}

/* --------------------------------------------------------------------------
 * 5. 菜单模块
 * -------------------------------------------------------------------------- */

function openMenu() {
  dom.menuSummary.textContent = app.sessionId
    ? `${app.novelTitle} · 会话进行中`
    : '当前没有正在运行的故事。';
  renderTimeline();
  dom.timelinePanel.hidden = true;
  dom.chatPanel.hidden = true;
  dom.menuPanel.classList.remove('is-hidden');
}

function closeMenu() {
  dom.menuPanel.classList.add('is-hidden');
}

/** 追加一条时间线条目 */
function addTimelineItem(cls, idxText, eventName) {
  const item = document.createElement('div');
  item.className = `tl-item ${cls}`;
  const idxEl = document.createElement('span');
  idxEl.className = 'tl-idx';
  idxEl.textContent = idxText;
  const nameEl = document.createElement('div');
  nameEl.className = 'tl-name';
  nameEl.textContent = eventName;
  item.append(idxEl, nameEl);
  dom.timelineList.appendChild(item);
}

/** 渲染剧情时间线（已触发 ✓ / 下一个灰） */
function renderTimeline() {
  dom.timelineList.innerHTML = '';
  const triggered = app.state?.triggered || [];
  const next = app.state?.next_event || null;
  if (!triggered.length && !next) {
    const hint = document.createElement('div');
    hint.className = 'empty-hint';
    hint.textContent = '暂无时间线数据';
    dom.timelineList.appendChild(hint);
    return;
  }
  // 已触发：显示事件名，标记 ✓；下一个：只显示事件名，灰色待触发
  triggered.forEach((name) => addTimelineItem('done', '✓', name));
  if (next) addTimelineItem('pending', '下一个', next.event_name);
}

/** 染行囊（inventory）与见闻（flags），空态有弱化提示 */
function renderStatus() {
  const st = app.state || {};
  // 仅接受字符串元素，防御历史脏数据（嵌套数组等）
  const items = Array.isArray(st.inventory)
    ? st.inventory.filter((x) => typeof x === 'string' && x.trim()) : [];
  // flags 必须是普通对象（非数组），只展示值为真的键
  const flags = st.flags && typeof st.flags === 'object' && !Array.isArray(st.flags)
    ? Object.keys(st.flags).filter((k) => st.flags[k] && typeof k === 'string') : [];

  dom.inventoryList.innerHTML = '';
  dom.flagsList.innerHTML = '';
  if (!items.length) {
    const hint = document.createElement('div');
    hint.className = 'empty-hint';
    hint.textContent = '行囊空空如也';
    dom.inventoryList.appendChild(hint);
  } else {
    items.forEach((name) => {
      const chip = document.createElement('span');
      chip.className = 'chip';
      chip.textContent = name;
      dom.inventoryList.appendChild(chip);
    });
  }
  if (!flags.length) {
    const hint = document.createElement('div');
    hint.className = 'empty-hint';
    hint.textContent = '尚无见闻标记';
    dom.flagsList.appendChild(hint);
  } else {
    flags.forEach((name) => {
      const chip = document.createElement('span');
      chip.className = 'chip chip-flag';
      chip.textContent = name;
      dom.flagsList.appendChild(chip);
    });
  }
}

/* --------------------------------------------------------------------------
 * 5b. 角色私聊（只读旁路问答：不推进剧情、不改状态）
 * -------------------------------------------------------------------------- */

/** 本作可私聊的角色（关系图节点去掉主角），结果缓存一次 */
async function loadChatRoster() {
  if (app.chatRoster) return app.chatRoster;
  const data = await api(`/api/novel/${encodeURIComponent(app.novelId)}/graph`);
  // 主角由玩家自己扮演，不作为私聊对象；按戏份权重排序（次要角色排后面）
  const nodes = (data.nodes || []).filter((n) => n.group !== '主角' && n.id);
  nodes.sort((a, b) => (b.weight || 0) - (a.weight || 0));
  app.chatRoster = nodes.map((n) => n.id);
  return app.chatRoster;
}

function chatHintEl(text) {
  const el = document.createElement('div');
  el.className = 'chat-empty';
  el.textContent = text;
  return el;
}

function chatBubble(role, text) {
  const el = document.createElement('div');
  el.className = `chat-msg ${role === 'user' ? 'me' : 'npc'}`;
  el.textContent = text;                    // textContent 防注入
  return el;
}

/** 重置私聊面板（切会话时调用，避免串台） */
function resetChat() {
  app.chat = { npc: '', busy: false, logs: {} };
  app.chatRoster = null;
  dom.chatNpcSelect.replaceChildren();
  dom.chatLog.replaceChildren();
  dom.chatInput.value = '';
  dom.chatPanel.hidden = true;
}

function renderChatLog() {
  const logs = app.chat.logs[app.chat.npc] || [];
  dom.chatLog.replaceChildren();
  if (!logs.length) {
    dom.chatLog.appendChild(chatHintEl(`和${app.chat.npc || '角色'}私下说点什么吧（只是聊天，不影响剧情）`));
    return;
  }
  logs.forEach((m) => dom.chatLog.appendChild(chatBubble(m.role, m.content)));
  dom.chatLog.scrollTop = dom.chatLog.scrollHeight;
}

/** 开合私聊面板（首次展开时拉角色列表） */
async function toggleChatPanel() {
  if (!app.sessionId) { toast('请先开始一个故事'); return; }
  if (!dom.chatPanel.hidden) { dom.chatPanel.hidden = true; return; }

  dom.timelinePanel.hidden = true;
  dom.statusPanel.hidden = true;
  dom.chatPanel.hidden = false;

  if (!dom.chatNpcSelect.options.length) {
    let roster = [];
    try {
      roster = await loadChatRoster();
    } catch (e) {
      dom.chatLog.replaceChildren(chatHintEl(`角色列表加载失败：${e.message}`));
      return;
    }
    if (!roster.length) {
      dom.chatLog.replaceChildren(chatHintEl('本作没有可私聊的角色'));
      return;
    }
    roster.forEach((name) => dom.chatNpcSelect.appendChild(new Option(name, name)));
    app.chat.npc = roster.includes(app.chat.npc) ? app.chat.npc : roster[0];
    dom.chatNpcSelect.value = app.chat.npc;
  }
  renderChatLog();
  dom.chatInput.focus();
}

/** 发一句私聊，拿到角色回话（服务端只读，历史由前端带回） */
async function sendChat(text) {
  const msg = text.trim();
  if (!msg || app.chat.busy || !app.chat.npc) return;

  app.chat.busy = true;
  dom.chatSend.disabled = true;
  const logs = app.chat.logs[app.chat.npc] || (app.chat.logs[app.chat.npc] = []);
  logs.push({ role: 'user', content: msg });
  renderChatLog();
  const pending = chatBubble('npc', '……');
  dom.chatLog.appendChild(pending);
  dom.chatLog.scrollTop = dom.chatLog.scrollHeight;

  try {
    const data = await api('/api/game/chat', {
      method: 'POST',
      body: {
        session_id: app.sessionId,
        npc_name: app.chat.npc,
        message: msg,
        history: logs.slice(-6),           // 近 3 个来回，仅用于延续对话
      },
    });
    logs.push({ role: 'assistant', content: data.reply || '（对方没有答话）' });
  } catch (e) {
    toast(e.message);                      // 失败：不留空的回话记录，玩家的话仍在面板上
  } finally {
    app.chat.busy = false;
    dom.chatSend.disabled = false;
  }
  renderChatLog();
  dom.chatInput.focus();
}

/* --------------------------------------------------------------------------
 * 6. 事件绑定（入口）
 * -------------------------------------------------------------------------- */

// 书架
dom.importBtn.addEventListener('click', () => dom.fileInput.click());
dom.fileInput.addEventListener('change', () => uploadNovel(dom.fileInput.files[0]));
dom.backToLibrary.addEventListener('click', showLibrary);

// 自由输入
dom.freeForm.addEventListener('submit', (e) => {
  e.preventDefault();
  const text = dom.freeInput.value;
  dom.freeInput.value = '';
  sendAction(text);
});

// 底栏与菜单
dom.menuBtn.addEventListener('click', openMenu);
dom.resumeBtn.addEventListener('click', closeMenu);
dom.homeBtn.addEventListener('click', goHome);
dom.backBtn.addEventListener('click', () => toast('剧情回溯暂不支持，可用存档恢复早期进度'));
dom.timelineBtn.addEventListener('click', () => {
  dom.timelinePanel.hidden = !dom.timelinePanel.hidden;
  if (!dom.timelinePanel.hidden) {
    dom.statusPanel.hidden = true;
    dom.chatPanel.hidden = true;
  }
});
dom.statusBtn.addEventListener('click', () => {
  dom.statusPanel.hidden = !dom.statusPanel.hidden;
  if (!dom.statusPanel.hidden) {
    renderStatus();                       // 展开前刷新，避免显示过期行囊/见闻
    dom.timelinePanel.hidden = true;
    dom.chatPanel.hidden = true;
  }
});
dom.chatBtn.addEventListener('click', toggleChatPanel);
dom.chatNpcSelect.addEventListener('change', () => {
  app.chat.npc = dom.chatNpcSelect.value;
  renderChatLog();
  dom.chatInput.focus();
});
dom.chatForm.addEventListener('submit', (e) => {
  e.preventDefault();
  const text = dom.chatInput.value;
  dom.chatInput.value = '';
  sendChat(text);
});
dom.graphBtn.addEventListener('click', () => {
  if (!app.novelId) { toast('请先开始一个故事'); return; }
  window.open(`/graphic/relation.html?novel_id=${encodeURIComponent(app.novelId)}`, '_blank');
});
dom.restartBtn.addEventListener('click', () => {
  if (!app.novelId) { toast('请先选择一本小说'); return; }
  closeMenu();
  startGame(app.novelId, app.novelTitle);
});

// 明暗切换（首页 + 褂单里共两组按钮）
document.querySelectorAll('[data-color-mode-choice]').forEach((btn) => {
  btn.addEventListener('click', () => setColorMode(btn.dataset.colorModeChoice));
});

// 菜单遮罩点击关闭
dom.menuPanel.addEventListener('click', (e) => {
  if (e.target === dom.menuPanel) closeMenu();
});

// 初始化
loadLibrary();
