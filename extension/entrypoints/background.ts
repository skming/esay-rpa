// Background service worker：桥接后端 WS（extension_bridge_service.py），把 {requestId, action}
// 转发给激活 tab 的 content script 执行并回传结果。action 与 Playwright 执行器共用同一套指令协议。

interface BridgeInstruction {
  requestId: string;
  action: {
    type: string;
    ref?: string;
    selector?: string;
    inputValue?: string;
    extractMode?: string; // browser.extract: text/count/attribute/html
    attribute?: string;
    query?: string;
    limit?: number;
    distance?: number;
    trusted?: boolean; // true 时改走 chrome.debugger(CDP) 可信输入，规避 dispatchEvent 的 isTrusted:false
    targetUrl?: string; // browser.open / tab.open
    clearStorage?: boolean; // 插件模式暂不支持，见 navigateActiveTab
    clearCookies?: boolean;
    index?: number; // browser.tab.switch：按当前窗口标签页顺序的下标
    checked?: boolean; // browser.check
    targetRef?: string; // browser.drag 落点 / browser.ensureLogin 登出态探测选择器
    targetSelector?: string;
    title?: string; // automation.group.*：tab group 标题
    x?: number; // automation.pointer：视口坐标，仅用于接管态视觉反馈
    y?: number;
    pulse?: boolean;
    blocked?: boolean; // automation.pageBlock：运行态锁定/解锁页面交互
  };
}

interface BridgeResult {
  requestId: string;
  ok: boolean;
  result?: unknown;
  error?: string;
}

const BACKEND_BASE_URL = 'http://127.0.0.1:8765';
const INITIAL_RECONNECT_DELAY_MS = 3000;
const MAX_RECONNECT_DELAY_MS = 30000;
const NAVIGATION_TIMEOUT_MS = 15000;
// MV3 worker 空闲 ~30s 会被杀；heartbeat 保活，alarm 仅低频兜底，避免与唤醒/重连叠加刷爆连接日志。
const KEEPALIVE_ALARM_NAME = 'rpa-studio-bridge-keepalive';
const KEEPALIVE_PERIOD_MINUTES = 1;
const HEARTBEAT_INTERVAL_MS = 20000;
// 后端顶替本连接时发的私有 close code：说明另一个浏览器（另一个 profile / 另一台 Chrome）也接上了同一座桥。
// 必须与普通断线区分开：普通断线该 3s 快速重连，被顶替时快速重连就是互相顶替的死循环，每次顶替都会让
// 对面正在跑的动作直接失败。故按 15s→60s 递增退避，且只有用户主动打开 popup 才清零。
const REPLACED_CONNECTION_CLOSE_CODE = 4409;
const REPLACED_CONNECTION_BACKOFF_MS = 15000;
const REPLACED_CONNECTION_MAX_BACKOFF_MS = 60000;
const HANDSHAKE_FAILURE_BACKOFF_MS = 15000;
// ERR_CONNECTION_REFUSED 由内核直接打进控制台、JS 拦不掉；桌面应用没开时只能靠退避降低刷屏。
const HANDSHAKE_FAILURE_MAX_BACKOFF_MS = 60000;
// 退避涨到 60s 后打开 popup 视为用户在等，按此间隔清零重试；不跟 2s 轮询走，否则刷爆握手。
const FOREGROUND_RETRY_INTERVAL_MS = 10000;
const CONTENT_SCRIPT_FILE = '/content-scripts/content.js';
const RECEIVING_END_MISSING_MESSAGE = 'Could not establish connection. Receiving end does not exist.';

let socket: WebSocket | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let heartbeatTimer: ReturnType<typeof setInterval> | null = null;
let reconnectDelayMs = INITIAL_RECONNECT_DELAY_MS;
let handshakeFailureDelayMs = HANDSHAKE_FAILURE_BACKOFF_MS;
let replacedConnectionDelayMs = REPLACED_CONNECTION_BACKOFF_MS;
let lastForegroundRetryAt = 0;

// "当前工作标签页"指针，对应 Playwright 的 context.page：锁定后跟着走，不再重读 OS 焦点，避免切页"串台"。
let controlledTabId: number | null = null;
let controlledTabGroupId: number | null = null;
// group.start 只登记标题，专用标签延迟到首次导航时按目标 URL 创建，避免预开 about:blank 抢焦点/残留。
let pendingGroupTitle: string | null = null;

type ResolveTabOptions = {
  requireInjectable?: boolean;
  // 只窥探已知 controlledTabId、不做重指派：否则专用标签停在 about:blank 时会把控制权误挪到用户当前页。
  passive?: boolean;
};

async function resolveControlledTab(options: ResolveTabOptions = {}): Promise<Browser.tabs.Tab | null> {
  if (controlledTabId !== null) {
    try {
      const controlled = await browser.tabs.get(controlledTabId);
      if (!options.requireInjectable || isInjectableTabUrl(controlled.url)) return controlled;
      if (options.passive) return null;
      controlledTabId = null;
    } catch {
      if (options.passive) return null;
      controlledTabId = null;
    }
  } else if (options.passive) {
    return null;
  }
  const tab = options.requireInjectable ? await findInjectableTab() : await findActiveTab();
  if (tab?.id !== undefined) {
    controlledTabId = tab.id;
    if (options.requireInjectable && tab.active !== true) {
      await browser.tabs.update(tab.id, { active: true });
    }
  }
  return tab;
}

async function findActiveTab(): Promise<Browser.tabs.Tab | null> {
  const [current] = await browser.tabs.query({ active: true, currentWindow: true });
  if (current !== undefined) return current;
  const [lastFocused] = await browser.tabs.query({ active: true, lastFocusedWindow: true });
  return lastFocused ?? null;
}

async function findInjectableTab(): Promise<Browser.tabs.Tab | null> {
  const active = await findActiveTab();
  if (active !== null && isInjectableTabUrl(active.url)) return active;

  const sameWindowTabs = active?.windowId !== undefined ? await browser.tabs.query({ windowId: active.windowId }) : [];
  const sameWindowTab = sameWindowTabs.find((tab) => isInjectableTabUrl(tab.url));
  if (sameWindowTab !== undefined) return sameWindowTab;

  const allTabs = await browser.tabs.query({});
  return allTabs.find((tab) => isInjectableTabUrl(tab.url)) ?? null;
}

function waitForTabLoad(tabId: number, timeoutMs: number): Promise<void> {
  return new Promise((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      browser.tabs.onUpdated.removeListener(listener);
      clearTimeout(timer);
      resolve();
    };
    const listener = (updatedTabId: number, changeInfo: { status?: string }) => {
      if (updatedTabId === tabId && changeInfo.status === 'complete') finish();
    };
    browser.tabs.onUpdated.addListener(listener);
    const timer = setTimeout(finish, timeoutMs);
    browser.tabs.get(tabId).then((tab) => {
      if (tab.status === 'complete') finish();
    }, finish);
  });
}

// browser.open：驱动的是用户已登录的真实浏览器，clearStorage/clearCookies 无意义，直接拒绝。
async function navigateActiveTab(action: BridgeInstruction['action']): Promise<{ ok: boolean; result?: unknown; error?: string }> {
  if (action.targetUrl === undefined) {
    return { ok: false, error: 'browser.open 需要 targetUrl' };
  }
  if (action.clearStorage === true || action.clearCookies === true) {
    return { ok: false, error: '插件执行器暂不支持 clearStorage/clearCookies，如需清理请改用 Playwright 执行器' };
  }
  // 还没有专用标签时，直接新建一个停在目标 URL 的专用标签——绝不导航用户当前正在看的标签页。
  const existing = await resolveControlledTab({ passive: true });
  const tab = existing ?? (await createControlledTab(action.targetUrl));
  if (tab?.id === undefined) {
    return { ok: false, error: '未找到可操作的浏览器标签页' };
  }
  // createControlledTab 已停在目标 URL；只有复用已存在的专用标签时才需再次导航。
  if (existing !== null) {
    await browser.tabs.update(tab.id, { url: action.targetUrl });
  }
  await waitForTabLoad(tab.id, NAVIGATION_TIMEOUT_MS);
  // 加载完统一归组：覆盖复用已存在标签的分支，也避开 create 即 group 的时序竞态（幂等）。
  await formControlledGroup(tab.id);
  const updated = await browser.tabs.get(tab.id);
  void notifyAutomationActivity(4);
  return { ok: true, result: { url: updated.url ?? action.targetUrl } };
}

async function openNewTab(action: BridgeInstruction['action']): Promise<{ ok: boolean; result?: unknown; error?: string }> {
  const newTab = await browser.tabs.create({ url: action.targetUrl, active: true });
  if (newTab.id === undefined) {
    return { ok: false, error: '创建新标签页失败' };
  }
  controlledTabId = newTab.id;
  await formControlledGroup(newTab.id);
  if (action.targetUrl !== undefined) {
    await waitForTabLoad(newTab.id, NAVIGATION_TIMEOUT_MS);
  }
  const updated = await browser.tabs.get(newTab.id);
  void notifyAutomationActivity(4);
  return { ok: true, result: { url: updated.url ?? '' } };
}

// 懒创建专用标签：直接停在 url（有目标就不经 about:blank），并按登记的标题归组。
async function createControlledTab(url: string): Promise<Browser.tabs.Tab | null> {
  const newTab = await browser.tabs.create({ url, active: true });
  if (newTab.id === undefined) return null;
  controlledTabId = newTab.id;
  await formControlledGroup(newTab.id);
  return newTab;
}

// 把专用标签并入 Easy RPA 分组；已属用户自己的分组则保留原样，不抢占/拆散。
async function formControlledGroup(tabId: number): Promise<void> {
  try {
    const groupId = await groupControlledTabs([tabId]);
    if (groupId === null) return;
    controlledTabGroupId = groupId;
    await chrome.tabGroups.update(groupId, {
      collapsed: false,
      color: 'purple',
      title: pendingGroupTitle ?? 'Easy RPA 执行中',
    });
  } catch {
    // 分组失败纯属可视化，不阻断真实执行。
  }
}

async function switchTab(action: BridgeInstruction['action']): Promise<{ ok: boolean; result?: unknown; error?: string }> {
  const index = action.index ?? 0;
  const currentTab = await resolveControlledTab();
  const tabs = await browser.tabs.query({ windowId: currentTab?.windowId });
  const target = tabs[index];
  if (target?.id === undefined) {
    return { ok: false, error: '标签页索引超出范围' };
  }
  controlledTabId = target.id;
  await browser.tabs.update(target.id, { active: true });
  if (controlledTabGroupId !== null) {
    await groupControlledTabs([target.id]);
  }
  return { ok: true, result: { url: target.url ?? '' } };
}

async function closeControlledTab(): Promise<{ ok: boolean; result?: unknown; error?: string }> {
  const tab = await resolveControlledTab();
  if (tab?.id === undefined) {
    return { ok: false, error: '未找到可操作的浏览器标签页' };
  }
  const windowTabs = await browser.tabs.query({ windowId: tab.windowId });
  if (windowTabs.length <= 1) {
    // 与 Playwright 执行器的 browser.tab.close 一致：唯一的标签页不关，避免话都说不上就断了连接。
    return { ok: true, result: { url: tab.url ?? '' } };
  }
  await browser.tabs.remove(tab.id);
  controlledTabId = null;
  // 必须 passive：controlledTabId 刚被置 null，不带这个选项就会落到 findActiveTab()，
  // 把用户当前正在看的标签页收为受控——下一次 browser.open 就直接把它导航走了。
  const remaining = await resolveControlledTab({ passive: true });
  return { ok: true, result: { url: remaining?.url ?? '' } };
}

// 只登记标题、不预开标签：专用标签延迟到首次 browser.open 按目标 URL 创建，既不占用户当前页也不留空白页。
// 登录态是 profile 级 cookie，不依赖具体标签，新标签同样拿到已登录会话。
async function ensureControlledTabGroup(title = 'Easy RPA 执行中'): Promise<{ ok: boolean; result?: unknown; error?: string }> {
  pendingGroupTitle = title;
  // 已存在的分组翻回「执行中」，否则复用同一标签的下一轮会一直停在上轮的「已完成」。
  if (controlledTabGroupId !== null) {
    try {
      await chrome.tabGroups.update(controlledTabGroupId, { collapsed: false, color: 'purple', title });
    } catch {
      controlledTabGroupId = null;
    }
  }
  return { ok: true, result: { groupId: controlledTabGroupId } };
}

type NonEmptyTabIds = [number, ...number[]];

// tabs.group() 会把 tab 从原分组挪走，故先过滤掉已属其他分组的 tab，避免跑一次流程就拆了用户的分组；空则返 null。
async function groupControlledTabs(tabIds: NonEmptyTabIds): Promise<number | null> {
  const groupableIds: number[] = [];
  for (const id of tabIds) {
    try {
      const tab = await chrome.tabs.get(id);
      const groupId = tab.groupId ?? chrome.tabGroups.TAB_GROUP_ID_NONE;
      if (groupId !== chrome.tabGroups.TAB_GROUP_ID_NONE && groupId !== controlledTabGroupId) continue;
      groupableIds.push(id);
    } catch {
      // tab 已关闭，跳过
    }
  }
  if (groupableIds.length === 0) return null;
  const safeTabIds = groupableIds as NonEmptyTabIds;
  if (controlledTabGroupId !== null) {
    try {
      return await groupTabs({ groupId: controlledTabGroupId, tabIds: safeTabIds });
    } catch {
      controlledTabGroupId = null;
    }
  }
  return await groupTabs({ tabIds: safeTabIds });
}

function groupTabs(options: chrome.tabs.GroupOptions): Promise<number> {
  return new Promise((resolve, reject) => {
    chrome.tabs.group(options, (groupId) => {
      const error = chrome.runtime.lastError;
      if (error !== undefined) {
        reject(new Error(error.message));
        return;
      }
      resolve(groupId);
    });
  });
}

async function markControlledTabGroupDone(title = 'Easy RPA 已完成'): Promise<{ ok: boolean; result?: unknown; error?: string }> {
  await setActivePageBlocked(false);
  if (controlledTabGroupId === null) {
    return { ok: true, result: { groupId: null } };
  }
  try {
    await chrome.tabGroups.update(controlledTabGroupId, {
      collapsed: false,
      color: 'grey',
      title,
    });
    return { ok: true, result: { groupId: controlledTabGroupId } };
  } catch (error) {
    controlledTabGroupId = null;
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
}

function isSocketActive(): boolean {
  return socket !== null && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING);
}

function clearReconnectTimer(): void {
  if (reconnectTimer !== null) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
}

function clearHeartbeat(): void {
  if (heartbeatTimer !== null) {
    clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }
}

function startHeartbeat(currentSocket: WebSocket): void {
  clearHeartbeat();
  heartbeatTimer = setInterval(() => {
    if (socket !== currentSocket || currentSocket.readyState !== WebSocket.OPEN) {
      clearHeartbeat();
      return;
    }
    // 后端会忽略无 requestId 的消息；这里只为产生真实 WS 流量，防止 worker 无事件休眠后反复重连。
    currentSocket.send(JSON.stringify({ type: 'keepalive', sentAt: Date.now() }));
  }, HEARTBEAT_INTERVAL_MS);
}

function scheduleReconnect(delayOverrideMs?: number): void {
  if (reconnectTimer !== null || isSocketActive()) return;
  const delayMs = delayOverrideMs ?? reconnectDelayMs;
  if (delayOverrideMs === undefined) {
    reconnectDelayMs = Math.min(reconnectDelayMs * 2, MAX_RECONNECT_DELAY_MS);
  }
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, delayMs);
}

// 清零退避立即重连。自身节流：调用方是 2s 轮询，频率远高于可接受的握手重试频率。
function retryConnectionNow(): void {
  const now = Date.now();
  if (now - lastForegroundRetryAt < FOREGROUND_RETRY_INTERVAL_MS) return;
  lastForegroundRetryAt = now;
  reconnectDelayMs = INITIAL_RECONNECT_DELAY_MS;
  handshakeFailureDelayMs = HANDSHAKE_FAILURE_BACKOFF_MS;
  // 被顶替的退避只在这里清零：用户打开了 popup，才说明他要的是当前这个浏览器。若改到 open 里清零，
  // 两个浏览器会稳定地每 15s 互相顶替一次，退避形同没有。
  replacedConnectionDelayMs = REPLACED_CONNECTION_BACKOFF_MS;
  connect();
}

// 必须保持同步：一旦这里出现 await，isSocketActive() 与 socket = nextSocket 之间就有了可中断点，
// 同一个 worker 的多个入口（装载时的 defineBackground 体 + onInstalled、alarm + onStartup）会各建一条 WS。
// 多出来的那条被所有 handler 用 socket !== nextSocket 忽略，但后端仍当它活着——反过来顶替掉真正在用的那条。
function connect(): void {
  if (isSocketActive()) return;
  clearReconnectTimer();

  const nextSocket = new WebSocket(buildBackendWebSocketUrl('/ws/extension/bridge'));
  socket = nextSocket;
  let hasOpened = false;

  nextSocket.addEventListener('open', () => {
    if (socket !== nextSocket) return;
    hasOpened = true;
    reconnectDelayMs = INITIAL_RECONNECT_DELAY_MS;
    handshakeFailureDelayMs = HANDSHAKE_FAILURE_BACKOFF_MS;
    console.log('[rpa-studio-bridge] connected to backend');
    startHeartbeat(nextSocket);
  });

  nextSocket.addEventListener('message', (event) => {
    if (socket !== nextSocket) return;
    let instruction: BridgeInstruction;
    try {
      instruction = JSON.parse(event.data as string) as BridgeInstruction;
    } catch (error) {
      // 帧解不出来就没有 requestId，回不了错——只能记一条，后端那侧走超时收场
      console.warn('[rpa-studio-bridge] dropped a malformed instruction frame', error);
      return;
    }
    void handleInstruction(instruction);
  });

  nextSocket.addEventListener('close', (event) => {
    if (socket !== nextSocket) return;
    socket = null;
    clearHeartbeat();
    if (event.code === REPLACED_CONNECTION_CLOSE_CODE) {
      // 被另一个浏览器顶替（不是网络问题）：递增退避，把两边互相顶替的频率压下来。
      console.log(`[rpa-studio-bridge] connection replaced by another browser, backing off ${replacedConnectionDelayMs / 1000}s`);
      scheduleReconnect(replacedConnectionDelayMs);
      replacedConnectionDelayMs = Math.min(replacedConnectionDelayMs * 2, REPLACED_CONNECTION_MAX_BACKOFF_MS);
      return;
    }
    if (!hasOpened || event.code === 1006) {
      // 后端重启/端口未就绪/握手被重置都会走到这里（未完成 WS open），放慢重试避免刷失败握手。
      console.warn(`[rpa-studio-bridge] websocket handshake failed, retrying in ${handshakeFailureDelayMs / 1000}s`);
      scheduleReconnect(handshakeFailureDelayMs);
      handshakeFailureDelayMs = Math.min(handshakeFailureDelayMs * 2, HANDSHAKE_FAILURE_MAX_BACKOFF_MS);
      return;
    }
    scheduleReconnect();
  });

  nextSocket.addEventListener('error', () => {
    if (socket !== nextSocket) return;
    nextSocket.close();
  });
}

function buildBackendWebSocketUrl(path: string): string {
  const wsBaseUrl = BACKEND_BASE_URL.replace(/^http:/, 'ws:').replace(/^https:/, 'wss:').replace(/\/$/, '');
  return `${wsBaseUrl}${path.startsWith('/') ? path : `/${path}`}`;
}

async function handleInstruction(instruction: BridgeInstruction): Promise<void> {
  const { action } = instruction;
  void notifyAutomationActivity();
  let result: { ok: boolean; result?: unknown; error?: string };
  try {
    if (action.type === 'browser.screenshot') {
      result = await captureActiveTabScreenshot();
    } else if (action.type === 'automation.group.start') {
      result = await ensureControlledTabGroup(action.title);
    } else if (action.type === 'automation.group.end') {
      result = await markControlledTabGroupDone(action.title);
    } else if (action.type === 'browser.open') {
      result = await navigateActiveTab(action);
    } else if (action.type === 'browser.tab.open') {
      result = await openNewTab(action);
    } else if (action.type === 'browser.tab.switch') {
      result = await switchTab(action);
    } else if (action.type === 'browser.tab.close') {
      result = await closeControlledTab();
    } else if (action.trusted === true && (action.type === 'browser.click' || action.type === 'browser.fill')) {
      result = await dispatchTrustedInput(action);
    } else {
      result = await sendToActiveTab(action);
    }
  } catch (error) {
    // 上面每个分支里的 tabs.update/create/get/remove、waitForTabLoad 都会 reject：用户中途关掉
    // 自动化标签页、目标是 chrome:// 这类禁止导航的 URL 都算常态。抛出去只会变成一条被丢弃的
    // rejection，BridgeResult 永远不发，后端等满 30s 才以「扩展执行动作超时」收场——真因整个丢掉。
    result = { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
  const response: BridgeResult = { requestId: instruction.requestId, ...result };
  if (socket === null || socket.readyState !== WebSocket.OPEN) {
    // 长动作跑完时连接可能已经换过一轮。这里只能记账：后端的 future 认 requestId，等不到就超时
    console.warn('[rpa-studio-bridge] socket unavailable, dropping result for', instruction.requestId);
    return;
  }
  socket.send(JSON.stringify(response));
}

// 纯提示、失败无所谓，但它内部的 tabs.update 会 reject：调用方一律 void，不自己兜住就是未处理的 rejection
async function notifyAutomationActivity(retries = 1): Promise<void> {
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    try {
      const result = await sendToActiveTab({ type: 'automation.activity' }, { passive: true });
      if (result.ok) return;
    } catch {
      // 忽略：活跃度提示失败不影响本次动作
    }
    if (attempt === retries) return;
    await new Promise((resolve) => setTimeout(resolve, 180));
  }
}

async function setActivePageBlocked(blocked: boolean): Promise<void> {
  const result = await sendToActiveTab({ type: 'automation.pageBlock', blocked }, { passive: true });
  if (!result.ok && !String(result.error ?? '').includes('专属标签页尚未导航到可交互页面')) {
    console.debug('[rpa-studio-bridge] 切换页面交互锁失败', result.error);
  }
}

// CDP 可信输入兜底：dispatchEvent 的 isTrusted 恒 false 会被部分站点拒绝，改用 CDP 注入原生事件，用完即 detach。
async function dispatchTrustedInput(action: BridgeInstruction['action']): Promise<{ ok: boolean; result?: unknown; error?: string }> {
  const tab = await resolveControlledTab();
  if (tab?.id === undefined) {
    return { ok: false, error: '未找到可操作的浏览器标签页' };
  }
  const tabId = tab.id;
  const debuggee = { tabId };

  const scrolled = await sendToActiveTab({ type: 'scrollIntoView', ref: action.ref, selector: action.selector });
  if (!scrolled.ok) return scrolled;
  const rectResponse = await sendToActiveTab({ type: 'resolveRect', ref: action.ref, selector: action.selector });
  if (!rectResponse.ok) return rectResponse;
  const rect = rectResponse.result as { x: number; y: number; width: number; height: number };
  const x = rect.x + rect.width / 2;
  const y = rect.y + rect.height / 2;

  try {
    await setActivePageBlocked(false);
    await chrome.debugger.attach(debuggee, '1.3');
    try {
      await chrome.debugger.sendCommand(debuggee, 'Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
      await chrome.debugger.sendCommand(debuggee, 'Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 });
      await chrome.debugger.sendCommand(debuggee, 'Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 });
      void sendToActiveTab({ type: 'automation.pointer', x, y, pulse: true });

      if (action.type === 'browser.fill') {
        if (action.inputValue === undefined) return { ok: false, error: 'browser.fill 需要 inputValue' };
        // 全选走 commands 而不是猜 modifier 位：macOS 上 Ctrl+A 是「移到行首」而非全选，
        // 光标跳到 0、Backspace 什么也删不掉，insertText 于是插在原值前面而不是替换掉它。
        // commands 让 Chrome 按自身平台绑定执行编辑命令，两个平台都对。
        await chrome.debugger.sendCommand(debuggee, 'Input.dispatchKeyEvent', { type: 'keyDown', key: 'a', code: 'KeyA', commands: ['selectAll'] });
        await chrome.debugger.sendCommand(debuggee, 'Input.dispatchKeyEvent', { type: 'keyUp', key: 'a', code: 'KeyA' });
        await chrome.debugger.sendCommand(debuggee, 'Input.dispatchKeyEvent', { type: 'keyDown', key: 'Backspace' });
        await chrome.debugger.sendCommand(debuggee, 'Input.dispatchKeyEvent', { type: 'keyUp', key: 'Backspace' });
        await chrome.debugger.sendCommand(debuggee, 'Input.insertText', { text: action.inputValue });
      }
      return { ok: true, result: { ok: true } };
    } finally {
      await chrome.debugger.detach(debuggee);
    }
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  } finally {
    await setActivePageBlocked(true);
  }
}

// 截图走 background 的 captureVisibleTab（content script 无权限），只能拍可见视口；返回 data URL 由后端落盘。
async function captureActiveTabScreenshot(): Promise<{ ok: boolean; result?: unknown; error?: string }> {
  const tab = await resolveControlledTab();
  if (tab?.windowId === undefined) {
    return { ok: false, error: '未找到可操作的浏览器标签页' };
  }
  try {
    const dataUrl = await browser.tabs.captureVisibleTab(tab.windowId, { format: 'png' });
    return { ok: true, result: { dataUrl } };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
}

async function sendToActiveTab(
  action: BridgeInstruction['action'],
  options: { passive?: boolean } = {}
): Promise<{ ok: boolean; result?: unknown; error?: string }> {
  const tab = await resolveControlledTab({ requireInjectable: true, passive: options.passive });
  if (tab?.id === undefined) {
    return {
      ok: false,
      error: options.passive ? '专属标签页尚未导航到可交互页面，跳过活跃度提示' : '未找到可操作的普通网页标签页，请先打开目标网站页面',
    };
  }
  const message = { source: 'rpa-studio-bridge', action };
  try {
    return await browser.tabs.sendMessage(tab.id, message);
  } catch (error) {
    if (isReceivingEndMissingError(error)) {
      const injected = await injectContentScript(tab);
      if (!injected.ok) return injected;
      try {
        return await browser.tabs.sendMessage(tab.id, message);
      } catch (retryError) {
        return { ok: false, error: formatTabMessageError(retryError) };
      }
    }
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
}

function isReceivingEndMissingError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return message.includes(RECEIVING_END_MISSING_MESSAGE);
}

function formatTabMessageError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  if (message.includes(RECEIVING_END_MISSING_MESSAGE)) {
    return '当前标签页未加载 Easy RPA 内容脚本，请刷新目标网页或重新打开标签页后重试';
  }
  return message;
}

function isInjectableTabUrl(url: string | undefined): boolean {
  if (url === undefined || url.trim().length === 0) return false;
  try {
    const protocol = new URL(url).protocol;
    return protocol === 'http:' || protocol === 'https:' || protocol === 'file:';
  } catch {
    return false;
  }
}

async function injectContentScript(tab: Browser.tabs.Tab): Promise<{ ok: boolean; result?: unknown; error?: string }> {
  if (tab.id === undefined) {
    return { ok: false, error: '未找到可注入内容脚本的浏览器标签页' };
  }
  if (!isInjectableTabUrl(tab.url)) {
    return { ok: false, error: `当前标签页不支持插件执行，请切换到普通网页后重试：${tab.url ?? 'unknown'}` };
  }
  try {
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: [CONTENT_SCRIPT_FILE],
    });
    return { ok: true, result: { injected: true } };
  } catch (error) {
    return { ok: false, error: `注入 Easy RPA 内容脚本失败：${error instanceof Error ? error.message : String(error)}` };
  }
}

// 接管 Banner 点「完成」后 content script 发来（页面主动事件、无 requestId，走不了 WS 协议），直接调后端 REST resume。
browser.runtime.onMessage.addListener((message) => {
  if (typeof message !== 'object' || message === null) return undefined;

  if ((message as { type?: string }).type === 'getConnectionStatus') {
    const connected = socket !== null && socket.readyState === WebSocket.OPEN;
    if (!connected) retryConnectionNow();
    return Promise.resolve({ connected, backendBaseUrl: BACKEND_BASE_URL });
  }

  if ((message as { source?: string }).source === 'rpa-studio-bridge-event') {
    const event = message as { type?: string; taskId?: string };
    if (event.type === 'takeoverResume' && typeof event.taskId === 'string') {
      void resumeHumanTakeover(event.taskId);
    }
  }
  return undefined;
});

async function resumeHumanTakeover(taskId: string): Promise<void> {
  try {
    await fetch(`${BACKEND_BASE_URL}/api/tasks/${taskId}/resume`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ resume_mode: 'next_node' }),
    });
  } catch (error) {
    console.error('[rpa-studio-bridge] 恢复人工接管失败', error);
  }
}

export default defineBackground(() => {
  connect();
  void browser.alarms.create(KEEPALIVE_ALARM_NAME, { periodInMinutes: KEEPALIVE_PERIOD_MINUTES });
  browser.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name !== KEEPALIVE_ALARM_NAME) return;
    if (!isSocketActive()) scheduleReconnect();
  });
  // onStartup 主动补连，避免浏览器刚重启、SW 还没被 alarm/事件唤醒那段时间桥接断连；onInstalled 覆盖安装/更新。
  // 这几个入口在装载时可能同一 tick 内连着触发，靠 connect() 同步判 isSocketActive() 去重。
  browser.runtime.onStartup.addListener(() => {
    connect();
  });
  browser.runtime.onInstalled.addListener(() => {
    connect();
  });
});
