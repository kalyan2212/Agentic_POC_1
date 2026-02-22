/**
 * J.A.R.V.I.S. EXECUTION PLATFORM — CORE JAVASCRIPT ENGINE
 * Cloud Migration Intelligence · Azure → Google Cloud
 * 
 * Core modules:
 *  - JarvisAPI    : REST calls to FastAPI backend
 *  - JarvisWS     : WebSocket for real-time agent streams
 *  - JarvisChat   : Floating AI assistant
 *  - JarvisPanel  : Detail panel management
 *  - JarvisAuth   : Session / persona management
 *  - JarvisNotify : Toast notifications
 *  - JarvisNav    : Tab navigation
 */

'use strict';

/* ════════════════════════════════════════════════════════
   CONFIG
   ════════════════════════════════════════════════════════ */
const JARVIS_CONFIG = {
  API_BASE:  window.JARVIS_API_BASE  || 'http://localhost:8010/api',
  WS_BASE:   window.JARVIS_WS_BASE   || 'ws://localhost:8010/ws',
  VERSION:   '1.0.0',
  MAX_RETRY: 3,
  TIMEOUT:   30000,
};

/* ════════════════════════════════════════════════════════
   AUTH — Persona / Session management
   ════════════════════════════════════════════════════════ */
const JarvisAuth = (() => {
  const PERSONA_META = {
    assessment: {
      id: 'assessment',
      label: 'Assessment Engine',
      initials: 'AE',
      gradient: 'linear-gradient(135deg,#3B82F6,#22D3EE)',
      color: '#22D3EE',
      page: 'assessment.html',
      capabilities: ['repo-scan','agent-run','dependency-graph','pattern-classify','bundle-plan']
    },
    migration: {
      id: 'migration',
      label: 'Migration Engine',
      initials: 'ME',
      gradient: 'linear-gradient(135deg,#8B5CF6,#3B82F6)',
      color: '#8B5CF6',
      page: 'migration.html',
      capabilities: ['wave-exec','terraform-gen','pipeline-mod','gcp-deploy','approval-flow']
    },
    testing: {
      id: 'testing',
      label: 'Testing Engine',
      initials: 'TE',
      gradient: 'linear-gradient(135deg,#10B981,#22D3EE)',
      color: '#10B981',
      page: 'testing.html',
      capabilities: ['smoke-test','sit','perf-test','uat','issue-create']
    },
    pmo: {
      id: 'pmo',
      label: 'PMO Reporting Engine',
      initials: 'PMO',
      gradient: 'linear-gradient(135deg,#F59E0B,#EF4444)',
      color: '#F59E0B',
      page: 'pmo.html',
      capabilities: ['exec-dashboard','risk','budget','gantt','consolidated-report']
    },
  };

  let _current = null;

  function init() {
    const stored = sessionStorage.getItem('jarvis_persona');
    if (stored) {
      try { _current = JSON.parse(stored); } catch(e) { _current = null; }
    }
    return _current;
  }

  function setPersona(id) {
    const meta = PERSONA_META[id];
    if (!meta) throw new Error(`Unknown persona: ${id}`);
    _current = { ...meta, loginTime: new Date().toISOString() };
    sessionStorage.setItem('jarvis_persona', JSON.stringify(_current));
    document.dispatchEvent(new CustomEvent('jarvis:persona-change', { detail: _current }));
    return _current;
  }

  function getPersona() { return _current; }
  function isLoggedIn() { return !!_current; }

  function logout() {
    _current = null;
    sessionStorage.removeItem('jarvis_persona');
    window.location.href = '../index.html';
  }

  function getAll() { return PERSONA_META; }
  function requireAuth() {
    if (!isLoggedIn()) { window.location.href = '../index.html'; return false; }
    return true;
  }

  return { init, setPersona, getPersona, isLoggedIn, logout, getAll, requireAuth };
})();

/* ════════════════════════════════════════════════════════
   API CLIENT
   ════════════════════════════════════════════════════════ */
const JarvisAPI = (() => {
  let _retries = {};

  async function request(method, path, body = null, opts = {}) {
    const url = `${JARVIS_CONFIG.API_BASE}${path}`;
    const headers = {
      'Content-Type': 'application/json',
      'X-Persona': JarvisAuth.getPersona()?.id || 'anonymous',
    };

    const cfg = { method, headers };
    if (body) cfg.body = JSON.stringify(body);

    try {
      const res = await fetch(url, cfg);
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new JarvisAPIError(res.status, err.detail || 'API error', path);
      }
      return await res.json();
    } catch(e) {
      if (e instanceof JarvisAPIError) throw e;
      throw new JarvisAPIError(0, e.message, path);
    }
  }

  class JarvisAPIError extends Error {
    constructor(status, message, path) {
      super(message);
      this.status = status;
      this.path = path;
      this.name = 'JarvisAPIError';
    }
  }

  /* ── Assessment ── */
  const assessment = {
    getRepos: (githubUser) => request('GET', `/assessment/repos?user=${githubUser}`),
    startScan: (repos) => request('POST', '/assessment/scan', { repos }),
    getScanStatus: (runId) => request('GET', `/assessment/scan/${runId}`),
    getScans: (limit = 10) => request('GET', `/assessment/scans?limit=${limit}`),
    getApplications: (params = {}) => request('GET', `/assessment/applications?${new URLSearchParams(params)}`),
    getApplication: (id) => request('GET', `/assessment/applications/${id}`),
    getDependencyGraph: (runId) => request('GET', `/assessment/graph/${runId}`),
    getInsights: (runId) => request('GET', `/assessment/insights/${runId}`),
    getBundles: (runId) => request('GET', `/assessment/bundles/${runId}`),
    approveBundle: (bundleId, approve) => request('POST', `/assessment/bundles/${bundleId}/approve`, { approve }),
    getMigrationPlan: (bundleId) => request('GET', `/assessment/migration-plan/${bundleId}`),
    getPatternInstructions: () => request('GET', '/assessment/pattern-instructions'),
    savePatternInstructions: (patternId, instructions) => request('POST', '/assessment/pattern-instructions', { pattern_id: patternId, instructions }),
    semanticSearch: (query) => request('POST', '/assessment/semantic-search', { query }),
    uploadRepo: (formData) => {
      const url = `${JARVIS_CONFIG.API_BASE}/assessment/upload`;
      return fetch(url, { method: 'POST', body: formData, headers: { 'X-Persona': JarvisAuth.getPersona()?.id || '' } }).then(r => r.json());
    }
  };

  /* ── Migration ── */
  const migration = {
    getWaves: () => request('GET', '/migration/waves'),
    getWave: (id) => request('GET', `/migration/waves/${id}`),
    startWave: (id) => request('POST', `/migration/waves/${id}/start`),
    getPatternAgents: () => request('GET', '/migration/agents'),
    runMigrationAgent: (app, pattern) => request('POST', '/migration/run', { app_id: app, pattern }),
    getMigrationStatus: (jobId) => request('GET', `/migration/jobs/${jobId}`),
    generateTerraform: (appId) => request('POST', `/migration/terraform/${appId}`),
    generatePipeline: (appId) => request('POST', `/migration/pipeline/${appId}`),
    approveStep: (stepId, approve, comment='') => request('POST', `/migration/approve/${stepId}`, { approve, comment }),
    getMigrationDiff: (appId) => request('GET', `/migration/diff/${appId}`),
    createIssue: (appId, error) => request('POST', '/migration/issue', { app_id: appId, error }),
  };

  /* ── Testing ── */
  const testing = {
    getSuites: () => request('GET', '/testing/suites'),
    runSuite: (suiteId, appId) => request('POST', `/testing/suites/${suiteId}/run`, { app_id: appId }),
    getRunStatus: (runId) => request('GET', `/testing/runs/${runId}`),
    getResults: (appId) => request('GET', `/testing/results/${appId}`),
    runAll: (appId) => request('POST', `/testing/run-all`, { app_id: appId }),
    createIssue: (testId, failure) => request('POST', '/testing/issue', { test_id: testId, failure }),
    getSyntheticData: (appId) => request('GET', `/testing/synthetic-data/${appId}`),
  };

  /* ── PMO ── */
  const pmo = {
    getDashboard: () => request('GET', '/pmo/dashboard'),
    getPhaseStatus: () => request('GET', '/pmo/phases'),
    getRisks: () => request('GET', '/pmo/risks'),
    getBudget: () => request('GET', '/pmo/budget'),
    getTimeline: () => request('GET', '/pmo/timeline'),
    getReport: (type) => request('GET', `/pmo/reports/${type}`),
    generateReport: (type, params) => request('POST', '/pmo/reports/generate', { type, ...params }),
  };

  /* ── GitHub ── */
  const github = {
    connect: (token, user) => request('POST', '/github/connect', { token, user }),
    getProfile: () => request('GET', '/github/profile'),
    listRepos: (user) => request('GET', `/github/repos/${user}`),
    getRepoContent: (user, repo, path='') => request('GET', `/github/content/${user}/${repo}?path=${path}`),
    disconnect: () => request('DELETE', '/github/connect'),
  };

  /* ── System ── */
  const system = {
    health: () => request('GET', '/health'),
    getKPIs: () => request('GET', '/system/kpis'),
    getAgentStatus: () => request('GET', '/system/agents'),
    getSettings: () => request('GET', '/system/settings'),
    saveSettings: (s) => request('POST', '/system/settings', s),
  };

  /* ── Integrations ── */
  const integrations = {
    list: () => request('GET', '/integrations/services'),
    get: (service) => request('GET', `/integrations/${service}`),
    save: (service, enabled, config = {}, status = 'connected') => request('POST', `/integrations/${service}`, { enabled, config, status }),
    test: (service) => request('POST', `/integrations/${service}/test`),
  };

  return { request, assessment, migration, testing, pmo, github, system, integrations, JarvisAPIError };
})();

/* ════════════════════════════════════════════════════════
   WEBSOCKET — Real-time agent streams
   ════════════════════════════════════════════════════════ */
const JarvisWS = (() => {
  let _connections = {};

  function connect(channel, onMessage, onClose) {
    if (_connections[channel]) disconnect(channel);

    const url = `${JARVIS_CONFIG.WS_BASE}/${channel}`;
    const ws = new WebSocket(url);

    ws.onopen = () => {
      console.log(`[JarvisWS] Connected: ${channel}`);
      document.dispatchEvent(new CustomEvent('jarvis:ws-open', { detail: { channel } }));
    };

    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        onMessage(data);
      } catch(err) {
        onMessage({ type: 'raw', content: e.data });
      }
    };

    ws.onerror = (e) => {
      console.error(`[JarvisWS] Error on ${channel}`, e);
      document.dispatchEvent(new CustomEvent('jarvis:ws-error', { detail: { channel } }));
    };

    ws.onclose = () => {
      delete _connections[channel];
      if (onClose) onClose();
      document.dispatchEvent(new CustomEvent('jarvis:ws-close', { detail: { channel } }));
    };

    _connections[channel] = ws;
    return ws;
  }

  function disconnect(channel) {
    if (_connections[channel]) {
      _connections[channel].close();
      delete _connections[channel];
    }
  }

  function send(channel, data) {
    if (_connections[channel]?.readyState === WebSocket.OPEN) {
      _connections[channel].send(JSON.stringify(data));
    }
  }

  function disconnectAll() {
    Object.keys(_connections).forEach(disconnect);
  }

  function isConnected(channel) {
    return _connections[channel]?.readyState === WebSocket.OPEN;
  }

  return { connect, disconnect, send, disconnectAll, isConnected };
})();

/* ════════════════════════════════════════════════════════
   AGENT STREAM — Renders real-time agent log lines
   ════════════════════════════════════════════════════════ */
const JarvisStream = (() => {
  function appendLine(containerId, { time, agent, text, type = 'info' }) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const now = time || new Date().toLocaleTimeString('en-US', { hour12: false, hour:'2-digit', minute:'2-digit', second:'2-digit' });
    const div = document.createElement('div');
    div.className = 'agent-stream-line';
    div.innerHTML = `
      <span class="stream-time">${now}</span>
      <span class="stream-agent stream-${type}">${escHtml(agent || 'SYSTEM')}</span>
      <span class="stream-text">${escHtml(text)}</span>
    `;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
  }

  function clear(containerId) {
    const c = document.getElementById(containerId);
    if (c) c.innerHTML = '';
  }

  function subscribeToRun(runId, containerId, onComplete) {
    const channel = `agent/${runId}`;
    JarvisWS.connect(channel, (msg) => {
      appendLine(containerId, {
        agent: msg.agent_name || msg.agent || 'ORCHESTRATOR',
        text:  msg.content   || msg.message || JSON.stringify(msg),
        type:  msg.type      || 'info',
      });
      if (msg.type === 'complete' || msg.status === 'done') {
        JarvisWS.disconnect(channel);
        if (onComplete) onComplete(msg);
      }
    }, () => { if (onComplete) onComplete({ status: 'closed' }); });
  }

  return { appendLine, clear, subscribeToRun };
})();

/* ════════════════════════════════════════════════════════
   PANEL — Detail panel overlay
   ════════════════════════════════════════════════════════ */
const JarvisPanel = (() => {
  let _stack = [];

  function open(title, renderFn, breadcrumbs = []) {
    const panel    = document.getElementById('detail-panel');
    const backdrop = document.getElementById('panel-backdrop');
    if (!panel || !backdrop) return;

    const content  = panel.querySelector('.panel-content');
    const bc       = panel.querySelector('.panel-breadcrumbs');

    if (content) content.innerHTML = renderFn();

    // Breadcrumbs
    if (bc) {
      bc.innerHTML = [...breadcrumbs, title].map((b, i, arr) => {
        if (i === arr.length - 1) return `<span class="breadcrumb-current">${escHtml(b)}</span>`;
        return `<span class="breadcrumb" onclick="JarvisPanel.back(${i})">${escHtml(b)}</span><span class="breadcrumb-sep">/</span>`;
      }).join('');
    }

    _stack.push({ title, renderFn, breadcrumbs });
    panel.classList.add('open');
    backdrop.classList.add('open');

    // Post-render hook
    if (typeof window._panelPostRender === 'function') window._panelPostRender();
  }

  function close() {
    const panel    = document.getElementById('detail-panel');
    const backdrop = document.getElementById('panel-backdrop');
    if (panel)    panel.classList.remove('open');
    if (backdrop) backdrop.classList.remove('open');
    _stack = [];
  }

  function back(level) {
    const target = _stack[level];
    if (target) { _stack = _stack.slice(0, level + 1); open(target.title, target.renderFn, target.breadcrumbs); }
  }

  return { open, close, back };
})();

/* ════════════════════════════════════════════════════════
   NOTIFICATIONS — Toast system
   ════════════════════════════════════════════════════════ */
const JarvisNotify = (() => {
  let _container = null;

  function _ensure() {
    if (!_container) {
      _container = document.createElement('div');
      _container.id = 'jarvis-toast-container';
      Object.assign(_container.style, {
        position: 'fixed', bottom: '96px', right: '28px',
        zIndex: '999', display: 'flex', flexDirection: 'column',
        gap: '8px', alignItems: 'flex-end', maxWidth: '360px',
      });
      document.body.appendChild(_container);
    }
  }

  const ICONS = { success: '✓', error: '✕', warn: '⚠', info: '◆', agent: '◉' };
  const COLORS = {
    success: 'var(--accent-emerald)',
    error:   'var(--accent-red)',
    warn:    'var(--accent-amber)',
    info:    'var(--jarvis-glow)',
    agent:   'var(--accent-purple)',
  };

  function show(message, type = 'info', duration = 4500) {
    _ensure();
    const toast = document.createElement('div');
    const color = COLORS[type] || COLORS.info;

    Object.assign(toast.style, {
      display: 'flex', alignItems: 'flex-start', gap: '10px',
      padding: '12px 16px',
      background: 'var(--bg-surface)', border: `1px solid ${color}33`,
      borderLeft: `3px solid ${color}`,
      borderRadius: 'var(--radius-md)',
      boxShadow: '0 4px 24px rgba(0,0,0,0.4)',
      maxWidth: '360px', minWidth: '260px',
      fontFamily: 'var(--font-body)', fontSize: '13px',
      color: 'var(--text-secondary)',
      animation: 'slideUp 0.3s ease forwards',
      cursor: 'pointer', transition: 'opacity 0.3s',
    });
    toast.innerHTML = `
      <span style="color:${color};font-size:14px;flex-shrink:0;margin-top:1px">${ICONS[type]||'◆'}</span>
      <span style="flex:1;line-height:1.45">${escHtml(message)}</span>
      <span style="color:var(--text-muted);font-size:14px;flex-shrink:0;line-height:1" onclick="this.parentElement.remove()">×</span>
    `;
    toast.addEventListener('click', () => toast.remove());
    _container.appendChild(toast);

    if (duration > 0) setTimeout(() => { toast.style.opacity = '0'; setTimeout(() => toast.remove(), 300); }, duration);
    return toast;
  }

  return {
    success: (msg, d) => show(msg, 'success', d),
    error:   (msg, d) => show(msg, 'error',   d || 7000),
    warn:    (msg, d) => show(msg, 'warn',     d),
    info:    (msg, d) => show(msg, 'info',     d),
    agent:   (msg, d) => show(msg, 'agent',    d),
  };
})();

/* ════════════════════════════════════════════════════════
   JARVIS CHAT ASSISTANT — AI-powered contextual assistant
   ════════════════════════════════════════════════════════ */
const JarvisChat = (() => {
  let _open = false;
  let _history = [];
  let _context = null;
  const MAX_INPUT_WORDS = 100;

  function setContext(ctx) {
    _context = ctx;
    const ctxEl = document.getElementById('jarvis-chat-context');
    if (ctxEl) ctxEl.textContent = ctx ? `Context: ${ctx}` : 'Engine — Cloud Migration Intelligence';
  }

  function toggle() {
    _open ? close() : open();
  }

  function open() {
    _open = true;
    document.getElementById('jarvis-chat')?.classList.add('open');
    document.getElementById('jarvis-fab')?.classList.add('hidden');
    document.getElementById('jarvis-chat-input-field')?.focus();
  }

  function close() {
    _open = false;
    document.getElementById('jarvis-chat')?.classList.remove('open');
    document.getElementById('jarvis-fab')?.classList.remove('hidden');
  }

  function _appendMsg(role, content) {
    const msgsEl = document.getElementById('jarvis-chat-messages');
    if (!msgsEl) return;
    const div = document.createElement('div');
    div.className = `jarvis-msg ${role}`;
    div.innerHTML = `<div>${formatMarkdown(content)}</div>`;
    msgsEl.appendChild(div);
    msgsEl.scrollTop = msgsEl.scrollHeight;
    return div;
  }

  function _showTyping() {
    const msgsEl = document.getElementById('jarvis-chat-messages');
    if (!msgsEl) return null;
    const div = document.createElement('div');
    div.className = 'jarvis-typing';
    div.id = '_jarvis-typing';
    div.innerHTML = '<div class="jarvis-typing-dot"></div><div class="jarvis-typing-dot"></div><div class="jarvis-typing-dot"></div>';
    msgsEl.appendChild(div);
    msgsEl.scrollTop = msgsEl.scrollHeight;
    return div;
  }

  async function sendMessage(text) {
    if (!text.trim()) return;
    const words = text.trim().split(/\s+/).filter(Boolean);
    if (words.length > MAX_INPUT_WORDS) {
      JarvisNotify.warn(`Please keep chat input within ${MAX_INPUT_WORDS} words.`);
      return;
    }
    const inputEl = document.getElementById('jarvis-chat-input-field');
    if (inputEl) inputEl.value = '';

    _appendMsg('user', text);
    _history.push({ role: 'user', content: text });

    const typing = _showTyping();

    try {
      const res = await JarvisAPI.request('POST', '/jarvis/chat', {
        message: text,
        context: _context,
        persona: JarvisAuth.getPersona()?.id,
        history: _history.slice(-10),
      });

      typing?.remove();
      const reply = res.reply || res.content || 'I apologize, I could not generate a response.';
      _appendMsg('assistant', reply);
      _history.push({ role: 'assistant', content: reply });

    } catch(e) {
      typing?.remove();
      const errMsg = `I couldn't reach the live AI provider. ${e?.message || 'Please verify backend /api/jarvis/chat, Ollama, and MCP agent services.'}`;
      _appendMsg('assistant', errMsg);
      _history.push({ role: 'assistant', content: errMsg });
    }
  }

  function sendSuggestion(text) {
    const inputEl = document.getElementById('jarvis-chat-input-field');
    if (inputEl) inputEl.value = text;
    sendMessage(text);
  }

  function init() {
    // FAB click
    const fab = document.getElementById('jarvis-fab');
    if (fab) fab.addEventListener('click', toggle);

    // Close button
    const closeBtn = document.getElementById('jarvis-chat-close');
    if (closeBtn) closeBtn.addEventListener('click', close);

    // Input enter
    const inputEl = document.getElementById('jarvis-chat-input-field');
    if (inputEl) {
      inputEl.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(inputEl.value); }
      });
    }

    // Send btn
    const sendBtn = document.getElementById('jarvis-chat-send');
    if (sendBtn) sendBtn.addEventListener('click', () => sendMessage(document.getElementById('jarvis-chat-input-field')?.value || ''));
  }

  return { init, open, close, toggle, setContext, sendMessage, sendSuggestion };
})();

/* ════════════════════════════════════════════════════════
   NAVIGATION — Tab-based page navigation
   ════════════════════════════════════════════════════════ */
const JarvisNav = (() => {
  let _current = null;

  function showPage(id) {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.nav-tab').forEach(t => {
      t.classList.toggle('active', t.dataset.page === id);
    });
    const page = document.getElementById(`page-${id}`);
    if (page) {
      page.classList.add('active');
      _current = id;
      document.dispatchEvent(new CustomEvent('jarvis:page-change', { detail: { page: id } }));
    }
  }

  function init(defaultPage) {
    document.querySelectorAll('.nav-tab').forEach(tab => {
      tab.addEventListener('click', () => showPage(tab.dataset.page));
    });
    const hash = window.location.hash.slice(1);
    showPage(hash || defaultPage);
  }

  function current() { return _current; }

  return { showPage, init, current };
})();

/* ════════════════════════════════════════════════════════
   GRAPH RENDERER — vis.js dependency graph wrapper
   ════════════════════════════════════════════════════════ */
const JarvisGraph = (() => {
  let _networks = {};

  const NODE_COLORS = {
    app:         { background: '#1E3A5F', border: '#3B82F6', highlight: { background: '#1E4A7F', border: '#60A5FA' } },
    db:          { background: '#1A2F1A', border: '#10B981', highlight: { background: '#1A3F1A', border: '#34D399' } },
    messaging:   { background: '#2D1F47', border: '#8B5CF6', highlight: { background: '#3D2F57', border: '#A78BFA' } },
    integration: { background: '#2F2500', border: '#F59E0B', highlight: { background: '#3F3500', border: '#FBBF24' } },
    external:    { background: '#1F1A00', border: '#EF4444', highlight: { background: '#2F2A00', border: '#F87171' } },
    gcp:         { background: '#001F2F', border: '#22D3EE', highlight: { background: '#002F3F', border: '#67E8F9' } },
  };

  function render(containerId, graphData, options = {}) {
    if (typeof vis === 'undefined') {
      document.getElementById(containerId).innerHTML = `<div style="padding:40px;text-align:center;color:var(--text-muted);font-family:var(--font-display)">
        <div style="font-size:32px;margin-bottom:12px">⊛</div>
        <div>Graph visualization requires vis.js</div>
        <div style="font-size:12px;margin-top:4px">Loading dependency data...</div>
      </div>`;
      return null;
    }

    const nodes = new vis.DataSet(graphData.nodes.map(n => ({
      id:    n.id,
      label: n.label,
      title: _buildTooltip(n),
      color: NODE_COLORS[n.type] || NODE_COLORS.app,
      shape: _getShape(n.type),
      size:  _getSize(n),
      font:  { color: '#F1F5F9', size: 12, face: 'Outfit' },
      borderWidth: n.selected ? 3 : 1.5,
      shadow: { enabled: true, color: 'rgba(0,0,0,0.5)', x: 2, y: 2, size: 6 },
      ...n,
    })));

    const edges = new vis.DataSet(graphData.edges.map(e => ({
      id:    e.id || `${e.from}-${e.to}`,
      from:  e.from,
      to:    e.to,
      label: e.label || '',
      arrows: { to: { enabled: true, scaleFactor: 0.7 } },
      color:  { color: _getEdgeColor(e.type), opacity: 0.6 },
      width:  e.weight || 1,
      dashes: e.type === 'async',
      smooth: { type: 'curvedCW', roundness: 0.15 },
      font:   { color: '#94A3B8', size: 10, face: 'JetBrains Mono' },
      ...e,
    })));

    const network = new vis.Network(document.getElementById(containerId),
      { nodes, edges },
      {
        physics: { stabilization: { iterations: 150 }, barnesHut: { gravitationalConstant: -5000, centralGravity: 0.3, springLength: 120 } },
        interaction: { hover: true, tooltipDelay: 200, navigationButtons: true, keyboard: true },
        layout: options.hierarchical ? {
          hierarchical: { direction: 'LR', sortMethod: 'directed', nodeSpacing: 100, levelSeparation: 180 }
        } : {},
        ...options,
      }
    );

    _networks[containerId] = network;

    network.on('click', (params) => {
      if (params.nodes.length > 0) {
        const nodeId = params.nodes[0];
        const node = graphData.nodes.find(n => n.id === nodeId);
        document.dispatchEvent(new CustomEvent('jarvis:graph-click', { detail: { containerId, node } }));
      }
    });

    return network;
  }

  function _getShape(type) {
    const shapes = { app: 'box', db: 'database', messaging: 'diamond', integration: 'dot', external: 'triangle', gcp: 'star' };
    return shapes[type] || 'box';
  }

  function _getSize(n) {
    return n.critical ? 28 : (n.connections > 5 ? 22 : 18);
  }

  function _getEdgeColor(type) {
    const colors = { api: '#3B82F6', db: '#10B981', msg: '#8B5CF6', event: '#F59E0B', batch: '#94A3B8' };
    return colors[type] || '#334155';
  }

  function _buildTooltip(n) {
    return `<div style="font-family:Outfit;font-size:12px;color:#F1F5F9;background:#111827;border:1px solid #1E293B;border-radius:8px;padding:10px 14px;max-width:220px">
      <strong style="font-size:13px">${n.label}</strong><br/>
      <span style="color:#64748B;font-size:11px">${n.type?.toUpperCase() || 'NODE'}</span>
      ${n.pattern ? `<br/><span style="color:#22D3EE;font-size:11px">Pattern: ${n.pattern}</span>` : ''}
      ${n.bundle  ? `<br/><span style="color:#F59E0B;font-size:11px">Bundle: ${n.bundle}</span>` : ''}
      ${n.tech    ? `<br/><span style="color:#94A3B8;font-size:11px">${n.tech}</span>` : ''}
    </div>`;
  }

  function highlight(containerId, nodeIds) {
    const net = _networks[containerId];
    if (!net) return;
    net.selectNodes(nodeIds);
  }

  function destroy(containerId) {
    _networks[containerId]?.destroy();
    delete _networks[containerId];
  }

  return { render, highlight, destroy };
})();

/* ════════════════════════════════════════════════════════
   DIFF VIEWER — Source vs target code/config diff
   ════════════════════════════════════════════════════════ */
const JarvisDiff = (() => {
  function render(containerId, diffData) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const lines = diffData.lines || [];
    container.innerHTML = lines.map((line, i) => {
      const cls = line.type === '+' ? 'add' : line.type === '-' ? 'del' : line.type === '@' ? 'meta' : '';
      return `<div class="diff-line ${cls}">
        <span class="diff-line-num">${line.lineNo || (i+1)}</span>
        <span class="diff-line-content">${escHtml(line.content)}</span>
      </div>`;
    }).join('');
  }

  return { render };
})();

/* ════════════════════════════════════════════════════════
   CHARTS — Canvas-based lightweight charts
   ════════════════════════════════════════════════════════ */
const JarvisChart = (() => {
  function donut(canvasId, segments, opts = {}) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const total = segments.reduce((s, seg) => s + seg.value, 0);
    if (total === 0) return;

    const cx = canvas.width / 2, cy = canvas.height / 2;
    const r = opts.outerRadius || Math.min(cx, cy) * 0.85;
    const ir = opts.innerRadius || r * 0.6;
    let angle = -Math.PI / 2;

    ctx.clearRect(0, 0, canvas.width, canvas.height);

    segments.forEach(seg => {
      const sweep = (seg.value / total) * Math.PI * 2;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.arc(cx, cy, r, angle, angle + sweep);
      ctx.arc(cx, cy, ir, angle + sweep, angle, true);
      ctx.closePath();
      ctx.fillStyle = seg.color;
      ctx.fill();
      ctx.strokeStyle = '#060B18';
      ctx.lineWidth = 2;
      ctx.stroke();
      angle += sweep;
    });

    // Center text
    if (opts.center) {
      ctx.fillStyle = '#F1F5F9';
      ctx.font = `700 ${opts.centerSize || 24}px 'JetBrains Mono', monospace`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(opts.center, cx, cy - 6);
      if (opts.centerSub) {
        ctx.fillStyle = '#64748B';
        ctx.font = `400 11px 'Outfit', sans-serif`;
        ctx.fillText(opts.centerSub, cx, cy + 16);
      }
    }
  }

  function burndown(canvasId, planned, actual) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const w = canvas.width, h = canvas.height;
    const pad = { t: 10, r: 20, b: 24, l: 36 };

    ctx.clearRect(0, 0, w, h);

    const maxVal = Math.max(...planned, ...actual, 1);
    const pw = w - pad.l - pad.r;
    const ph = h - pad.t - pad.b;

    function px(i) { return pad.l + (i / (planned.length - 1)) * pw; }
    function py(v) { return pad.t + ph - (v / maxVal) * ph; }

    // Grid
    ctx.strokeStyle = '#1E293B'; ctx.lineWidth = 1;
    [0.25, 0.5, 0.75, 1].forEach(f => {
      ctx.beginPath(); ctx.moveTo(pad.l, py(maxVal * f)); ctx.lineTo(pad.l + pw, py(maxVal * f)); ctx.stroke();
    });

    // Planned line
    ctx.beginPath(); ctx.strokeStyle = '#3B82F6'; ctx.lineWidth = 2; ctx.setLineDash([4, 3]);
    planned.forEach((v, i) => i === 0 ? ctx.moveTo(px(i), py(v)) : ctx.lineTo(px(i), py(v)));
    ctx.stroke(); ctx.setLineDash([]);

    // Actual line
    const actualFilled = actual.filter(v => v !== null && v !== undefined);
    if (actualFilled.length > 0) {
      ctx.beginPath(); ctx.strokeStyle = '#10B981'; ctx.lineWidth = 2.5;
      actual.forEach((v, i) => {
        if (v === null || v === undefined) return;
        i === 0 || actual[i-1] === null ? ctx.moveTo(px(i), py(v)) : ctx.lineTo(px(i), py(v));
      });
      ctx.stroke();
    }
  }

  return { donut, burndown };
})();

/* ════════════════════════════════════════════════════════
   HELPERS
   ════════════════════════════════════════════════════════ */
function escHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function formatMarkdown(text) {
  if (!text) return '';
  return String(text)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/`(.+?)`/g, `<code style="font-family:var(--font-mono);font-size:11px;background:var(--bg-elevated);padding:1px 5px;border-radius:3px;color:var(--accent-cyan)">$1</code>`)
    .replace(/\n/g, '<br/>');
}

function formatDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function formatDateTime(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function formatRelative(iso) {
  if (!iso) return '—';
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes/1024).toFixed(1)} KB`;
  return `${(bytes/1048576).toFixed(1)} MB`;
}

function patternLabel(code) {
  const patterns = {
    'a': 'Ext · Web-DMZ · DB + Msg',
    'b': 'Ext · Web-DMZ · DB · No Msg',
    'c': 'Ext · Web-DMZ · Msg · No DB',
    'd': 'Ext · Web-DMZ · No DB · No Msg',
    'e': 'Int · DB + Msg',
    'f': 'Int · DB · No Msg',
    'g': 'Int · Msg · No DB',
    'h': 'Int · No DB · No Msg → GCE',
    'i': 'PCF → GKE',
  };
  return patterns[code] || code;
}

function migrationStrategy(pattern) {
  const strats = {
    'a': 'Rebuild Web+App on GCE · Replicate DB · Flow-based Msg migration · DMZ setup',
    'b': 'Rebuild Web+App on GCE · Database replication · No messaging changes',
    'c': 'Rebuild Web+App on GCE · No DB changes · Flow-based messaging migration · DMZ setup',
    'd': 'Rebuild Web+App on GCE · DMZ configuration only',
    'e': 'Rebuild App on GCE · DB replication · Messaging migration · No DMZ',
    'f': 'Rebuild App on GCE · Database replication · No DMZ',
    'g': 'Rebuild App on GCE · Messaging migration · No DMZ',
    'h': 'Rebuild App to new GCE environment',
    'i': 'PCF to GKE migration · Container re-platforming',
  };
  return strats[pattern] || 'Standard migration approach';
}

function patternColor(code) {
  if (['a','b','c','d'].includes(code)) return 'var(--accent-blue)';
  if (['e','f','g','h'].includes(code)) return 'var(--accent-emerald)';
  if (code === 'i') return 'var(--accent-purple)';
  return 'var(--text-muted)';
}

/* ════════════════════════════════════════════════════════
   PERSONA TOPBAR SETUP
   ════════════════════════════════════════════════════════ */
function initTopBar() {
  const persona = JarvisAuth.getPersona();
  if (!persona) return;

  const avatarEl  = document.getElementById('persona-avatar');
  const labelEl   = document.getElementById('persona-label');
  const logoutBtn = document.getElementById('btn-logout');

  if (avatarEl)  { avatarEl.style.background = persona.gradient; avatarEl.textContent = persona.initials; }
  if (labelEl)   labelEl.textContent = persona.label;
  if (logoutBtn) logoutBtn.addEventListener('click', JarvisAuth.logout);

  // System status clock
  const clockEl = document.getElementById('system-clock');
  if (clockEl) {
    const tick = () => { clockEl.textContent = new Date().toUTCString().slice(0, 25) + ' UTC'; };
    tick(); setInterval(tick, 1000);
  }
}

/* ════════════════════════════════════════════════════════
   CLOCK / UPTIME
   ════════════════════════════════════════════════════════ */
function initSystemStatus() {
  const uptimeStart = Date.now();
  const uptimeEl = document.getElementById('system-uptime');
  if (uptimeEl) {
    setInterval(() => {
      const s = Math.floor((Date.now() - uptimeStart) / 1000);
      const h = String(Math.floor(s / 3600)).padStart(2,'0');
      const m = String(Math.floor((s % 3600) / 60)).padStart(2,'0');
      const sec = String(s % 60).padStart(2,'0');
      uptimeEl.textContent = `${h}:${m}:${sec}`;
    }, 1000);
  }
}

/* ════════════════════════════════════════════════════════
   GLOBAL INIT
   ════════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {
  JarvisAuth.init();
  JarvisChat.init();
  initTopBar();
  initSystemStatus();

  // Global panel close via backdrop
  document.getElementById('panel-backdrop')?.addEventListener('click', JarvisPanel.close);
  document.getElementById('panel-close-btn')?.addEventListener('click', JarvisPanel.close);

  // Keyboard shortcuts
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') JarvisPanel.close();
    if (e.key === 'k' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); JarvisChat.toggle(); }
  });
});

// Expose globally
window.JarvisAuth    = JarvisAuth;
window.JarvisAPI     = JarvisAPI;
window.JarvisWS      = JarvisWS;
window.JarvisStream  = JarvisStream;
window.JarvisPanel   = JarvisPanel;
window.JarvisNotify  = JarvisNotify;
window.JarvisChat    = JarvisChat;
window.JarvisNav     = JarvisNav;
window.JarvisGraph   = JarvisGraph;
window.JarvisDiff    = JarvisDiff;
window.JarvisChart   = JarvisChart;
window.escHtml       = escHtml;
window.formatMarkdown= formatMarkdown;
window.formatDate    = formatDate;
window.formatDateTime= formatDateTime;
window.formatRelative= formatRelative;
window.patternLabel  = patternLabel;
window.migrationStrategy = migrationStrategy;
window.patternColor  = patternColor;
