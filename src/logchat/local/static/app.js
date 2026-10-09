(() => {
  'use strict';

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const asArray = (value) => Array.isArray(value) ? value : [];
  const textValue = (value, fallback = '') => value == null || value === '' ? fallback : String(value);
  const timezone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  const uuid = () => crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const viewLabels = { investigations: 'Investigations', agents: 'Connect agent', settings: 'Settings' };
  const evidenceOverlayQuery = '(max-width: 900px)';
  const evidenceOverlayMedia = window.matchMedia(evidenceOverlayQuery);

  const state = {
    view: location.hash.slice(1) in viewLabels ? location.hash.slice(1) : 'investigations',
    projects: [],
    project: null,
    status: null,
    environments: [],
    conversations: [],
    activeConversation: null,
    selectedEnvironmentIds: null,
    messages: [],
    discovery: null,
    coverage: [],
    selectedEvidence: null,
    connectionInstructions: null,
    environmentsEnabled: false,
    evidenceReturnFocus: null,
    busy: false,
    poll: null,
    timelineCategory: '',
    timelineLoading: false,
  };

  function el(tag, options = {}, children = []) {
    const node = document.createElement(tag);
    if (options.className) node.className = options.className;
    if (options.text != null) node.textContent = String(options.text);
    if (options.type) node.type = options.type;
    if (options.name) node.name = options.name;
    if (options.value != null) node.value = String(options.value);
    if (options.placeholder) node.placeholder = options.placeholder;
    if (options.title) node.title = options.title;
    if (options.id) node.id = options.id;
    if (options.hidden) node.hidden = true;
    if (options.disabled) node.disabled = true;
    if (options.required) node.required = true;
    if (options.checked) node.checked = true;
    if (options.attrs) Object.entries(options.attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
    const list = Array.isArray(children) ? children : [children];
    list.filter((child) => child != null).forEach((child) => node.append(child.nodeType ? child : document.createTextNode(String(child))));
    return node;
  }

  function icon(name) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('aria-hidden', 'true');
    const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    use.setAttribute('href', `#i-${name}`);
    svg.append(use);
    return svg;
  }

  function button(label, className = 'secondary', onClick, iconName) {
    const children = [];
    if (iconName) children.push(icon(iconName));
    children.push(label);
    const node = el('button', { type: 'button', className }, children);
    if (onClick) node.addEventListener('click', onClick);
    return node;
  }

  function field(labelText, control, optional = false) {
    const label = el('label', {}, [labelText]);
    if (optional) label.append(el('span', { text: 'Optional' }));
    label.append(control);
    return label;
  }

  function notice(message, kind = 'error') {
    const node = el('div', { className: `${kind}-banner`, attrs: { role: kind === 'error' ? 'alert' : 'status' } }, [icon(kind === 'error' ? 'alert' : 'check'), el('span', { text: message })]);
    return node;
  }

  function emptyState(title, body, action) {
    const node = el('div', { className: 'empty-state' }, [icon('source'), el('h2', { text: title }), el('p', { text: body })]);
    if (action) node.append(action);
    return node;
  }

  function formatDate(value, detail = false) {
    if (!value) return 'Not yet';
    const date = new Date(value);
    if (Number.isNaN(date.valueOf())) return String(value);
    return new Intl.DateTimeFormat(undefined, detail
      ? { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }
      : { month: 'short', day: 'numeric' }).format(date);
  }

  function dateInputValue(date) {
    const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
    return local.toISOString().slice(0, 16);
  }

  function projectId(project = state.project) {
    return project && (project.id || project.project_id);
  }

  function projectPath(project = state.project) {
    return textValue(project && (project.path || project.root || project.project_path), '/absolute/path');
  }

  async function request(path, options = {}) {
    const response = await fetch(path, {
      credentials: 'include',
      cache: 'no-store',
      ...options,
      headers: options.body ? { 'Content-Type': 'application/json', ...(options.headers || {}) } : options.headers,
    });
    const raw = await response.text();
    let data = null;
    try { data = raw ? JSON.parse(raw) : null; } catch { data = raw ? { detail: raw } : null; }
    if (!response.ok) {
      const detail = data && (data.detail || data.error || data.message);
      throw new Error(typeof detail === 'string' ? detail : `Request failed (${response.status}).`);
    }
    return data;
  }

  function toast(message) {
    const node = el('div', { className: 'toast', text: message });
    $('#toast-region').append(node);
    setTimeout(() => node.remove(), 3200);
  }

  async function copyText(value) {
    try {
      await navigator.clipboard.writeText(value);
      toast('Copied to clipboard.');
    } catch {
      toast('Copy failed. Select the command and copy it manually.');
    }
  }

  function updateChrome() {
    $('#view-name').textContent = viewLabels[state.view];
    $('#project-name').textContent = state.project ? textValue(state.project.name, 'Local project') : 'No project';
    $('#project-path').textContent = state.project ? projectPath() : 'Choose a local project.';
    $$('.nav-item').forEach((item) => item.classList.toggle('active', item.dataset.view === state.view));
    const sync = $('#sync-state');
    sync.className = 'sync-state';
    const chunks = Number(state.status && state.status.chunks) || 0;
    if (state.view === 'timeline') {
      $('span', sync).textContent = 'Host timeline';
    } else if (!state.project) {
      sync.classList.add('waiting');
      $('span', sync).textContent = 'Create or choose a project';
    } else if (chunks > 0) {
      sync.classList.add('ready');
      $('span', sync).textContent = `${chunks} searchable ${chunks === 1 ? 'chunk' : 'chunks'}`;
    } else {
      sync.classList.add('waiting');
      $('span', sync).textContent = 'Waiting for evidence';
    }
    renderProjectPicker();
  }

  function renderProjectPicker() {
    const picker = $('#project-picker');
    const current = projectId();
    picker.replaceChildren();
    if (!state.projects.length) picker.append(el('option', { text: 'No projects yet', value: '' }));
    state.projects.forEach((project) => picker.append(el('option', { text: textValue(project.name, 'Untitled project'), value: projectId(project) })));
    picker.value = current || '';
    picker.disabled = !state.projects.length;
  }

  function renderConversations() {
    const list = $('#conversation-list');
    list.replaceChildren();
    state.conversations.slice(0, 10).forEach((conversation) => {
      const item = el('button', { type: 'button', className: `conversation-item${state.activeConversation === conversation.id ? ' active' : ''}` }, [
        textValue(conversation.title, 'Untitled investigation'),
        el('small', { text: formatDate(conversation.updated_at || conversation.created_at) }),
      ]);
      item.addEventListener('click', () => openConversation(conversation.id));
      list.append(item);
    });
    if (!state.conversations.length) list.append(el('p', { className: 'recent-empty', text: 'Questions you save will appear here.' }));
  }

  async function loadProjects() {
    const data = await request('/projects');
    state.projects = asArray(data && data.projects).length ? data.projects : asArray(data);
    const saved = localStorage.getItem('logchat.project');
    state.project = state.projects.find((project) => projectId(project) === saved) || state.projects[0] || null;
  }

  async function loadProjectData() {
    if (!state.project) {
      state.status = null;
      state.environments = [];
      state.conversations = [];
      return;
    }
    const id = encodeURIComponent(projectId());
    const results = await Promise.allSettled([
      request(`/projects/${id}/status`),
      request(`/projects/${id}/environments`),
      request(`/projects/${id}/conversations`),
      request('/settings/ui'),
    ]);
    state.environmentsEnabled = results[3].status === 'fulfilled' && results[3].value.environments_enabled === true;
    state.status = results[0].status === 'fulfilled' ? results[0].value : null;
    const envData = results[1].status === 'fulfilled' ? results[1].value : [];
    state.environments = asArray(envData && envData.environments).length ? envData.environments : asArray(envData);
    const conversationData = results[2].status === 'fulfilled' ? results[2].value : [];
    state.conversations = asArray(conversationData && conversationData.conversations).length ? conversationData.conversations : asArray(conversationData);
  }

  async function refreshStatus(render = true) {
    if (!state.project) return;
    const expected = projectId();
    try {
      const data = await request(`/projects/${encodeURIComponent(expected)}/status`);
      if (projectId() !== expected) return;
      state.status = data;
      updateChrome();
      if (render && state.view === 'sources') renderView();
    } catch {
      const sync = $('#sync-state');
      sync.className = 'sync-state error';
      $('span', sync).textContent = 'Local service unavailable';
    }
  }

  function startPolling() {
    clearInterval(state.poll);
    state.poll = setInterval(() => { refreshStatus(state.view === 'sources'); if(state.view === 'timeline') loadTimeline(); }, 5000);
  }

  async function chooseProject(id) {
    state.project = state.projects.find((project) => projectId(project) === id) || null;
    localStorage.setItem('logchat.project', id);
    state.activeConversation = null;
    state.selectedEnvironmentIds = null;
    state.messages = [];
    state.selectedEvidence = null;
    state.connectionInstructions = null;
    closeEvidence();
    await loadProjectData();
    updateChrome();
    renderConversations();
    renderView();
  }

  async function createProject(form) {
    const values = Object.fromEntries(new FormData(form));
    const body = { name: values.name.trim() };
    if (values.path.trim()) body.path = values.path.trim();
    const made = await request('/projects', { method: 'POST', body: JSON.stringify(body) });
    state.projects.push(made);
    form.reset();
    form.hidden = true;
    await chooseProject(projectId(made));
    toast('Project created.');
  }

  function showNoProject() {
    const view = el('section', { className: 'view narrow' });
    const action = button('Create a project', 'primary', () => {
      $('#project-form').hidden = false;
      $('#project-form input[name="name"]').focus();
    }, 'plus');
    view.append(el('div', { className: 'welcome' }, [
      el('h1', { text: 'Start with a local project.' }),
      el('p', { text: 'A project keeps its sources, evidence, and investigations together. You can point it at an existing folder or let Logchat use its default location.' }),
      action,
    ]));
    $('#workspace').replaceChildren(view);
  }

  function viewHeader(title, description, actions = []) {
    return el('div', { className: 'view-head' }, [
      el('div', {}, [el('h1', { text: title }), el('p', { text: description })]),
      actions.length ? el('div', { className: 'view-actions' }, actions) : null,
    ]);
  }

  function environmentChecks() {
    const wrap = el('div', { className: 'environment-checks' });
    const envs = state.environments.length ? state.environments : [{ id: 'dev', name: 'dev' }];
    envs.forEach((environment) => {
      const input = el('input', { type: 'checkbox', name: 'environment_ids', value: environment.id,
        checked: state.selectedEnvironmentIds === null || state.selectedEnvironmentIds.includes(environment.id) });
      input.addEventListener('change', () => {
        state.selectedEnvironmentIds = $$('input[name="environment_ids"]:checked', wrap).map((item) => item.value);
      });
      const label = el('label', {}, [input, ` ${textValue(environment.name, environment.id)}`]);
      label.style.marginRight = '12px';
      wrap.append(label);
    });
    return wrap;
  }

  function normalizeMessage(item) {
    if (item.result) return { role: item.role || 'assistant', question: item.question, result: item.result };
    if (item.role === 'user') return { role: 'user', question: item.content || item.question || '' };
    return { role: item.role || 'assistant', result: { answer: item.answer || item.content || '', evidence: item.evidence, gaps: item.gaps, plan: item.plan } };
  }

  function shellQuote(value) {
    return "'" + String(value).replaceAll("'", "'\\''") + "'";
  }

  function answerProvenance(result) {
    const provenance = result.provenance;
    if (!provenance) return { label: 'Answer provenance not recorded', computed_provider_role: { provider: null, role: 'not_recorded' } };
    if (provenance.embedding && Array.isArray(provenance.stages)) {
      const remapper = provenance.stages.find((stage) => stage.stage === 'remapper');
      return {
        label: `Embeddings: ${provenance.embedding.model} · Relevance: ${remapper && remapper.model || 'unavailable'} (${remapper && remapper.status || 'not recorded'}) · Context and metrics: Logchat`,
        computed_provider_role: { provider: 'deterministic', role: 'stored_metrics_and_cited_context' },
      };
    }
    const selected = String(result.model_output_policy || '').startsWith('evidence_selection_only') && provenance.status === 'model';
    const computed = provenance.computed_findings_provider || (provenance.provider === 'extractive' ? 'extractive' : null);
    return {
      label: selected
        ? `Evidence selected by ${provenance.provider} · ${provenance.model}. Facts computed by Logchat · ${computed || 'not recorded'}`
        : provenance.provider === 'extractive'
          ? `Facts computed by Logchat · extractive · ${provenance.model}${provenance.status === 'fallback' ? ' · fallback' : ''}`
          : `Recorded provider: ${provenance.provider} · ${provenance.model}; role not recorded`,
      computed_provider_role: { provider: computed, role: computed ? 'deterministic_computed_facts' : 'not_recorded' },
    };
  }

  function copyAnswer(result) {
    // Explicit public response fields only. Never serialize settings or connection state.
    return JSON.stringify({ answer: result.answer, findings: asArray(result.findings),
      plan: result.plan, gaps: asArray(result.gaps), provenance: result.provenance,
      evidence: asArray(result.evidence).map((row) => memorySnapshot(row, result).memory),
      computed_provider_role: answerProvenance(result).computed_provider_role,
      cited_evidence_ids: asArray(result.cited_evidence_ids), model_output_policy: result.model_output_policy, context_metadata: result.context_metadata,
      agent_context: result.agent_context, candidate_evidence: result.candidate_evidence }, null, 2);
  }

  function renderEvidenceButtons(result) {
    return el('div', { className: 'citation-map' }, [
      el('span', { className: 'truth-note', text: `${asArray(result.evidence).length} supporting memories` }),
      button('Supporting memories', 'secondary', () => openMemories(result)),
      button('Copy answer', 'text-button', () => copyText(copyAnswer(result)), 'copy'),
    ]);
  }

  function gapText(gap) {
    if (typeof gap === 'string') return gap;
    return textValue(gap && (gap.summary || gap.detail || gap.reason), 'Coverage gap reported.');
  }

  function messageNode(item) {
    const message = normalizeMessage(item);
    if (message.role === 'user') {
      return el('article', { className: 'message' }, [
        el('div', { className: 'message-label' }, [icon('chat'), 'Question']),
        el('p', { className: 'message-question', text: message.question }),
      ]);
    }
    const result = message.result || {};
    const findings = asArray(result.findings);
    const article = el('article', { className: 'message' }, [
      el('div', { className: 'message-label' }, [icon('mark'), 'What the evidence supports']),
    ]);
    if (findings.length) {
      findings.forEach((finding) => {
        const scope = finding.scope || {};
        const window = scope.window || {};
        article.append(el('section', { className: 'finding' }, [
          el('small', { text: [scope.environment, scope.service, window.label, window.start, window.end, scope.timezone].filter(Boolean).join(' · ') }),
          el('p', { className: 'answer-copy', text: finding.observation }),
          el('p', { text: finding.interpretation }),
        ]));
      });
    } else {
      article.append(el('div', { className: 'answer-copy', text: textValue(result.answer, 'No answer was returned.') }));
    }
    article.append(el('p', { className: 'answer-provenance', text: answerProvenance(result).label }));
    if (result.context_metadata) {
      const context = result.context_metadata;
      article.append(el('details', { className: 'conversation-context' }, [
        el('summary', { text: 'Conversation context' }),
        el('p', { text: `${context.turn_count || 0} prior turns; bounded user-only context. Concrete topic recall uses shared words; metric-only followups retain the last concrete topic. Compressed or ambiguous details may need restating. Conversation memory is not log evidence.` }),
      ]));
    }
    const citations = renderEvidenceButtons(result);
    if (citations) article.append(citations);
    const gaps = asArray(result.gaps);
    if (gaps.length) {
      const list = el('ul');
      gaps.forEach((gap) => list.append(el('li', { text: gapText(gap) })));
      article.append(el('div', { className: 'gaps' }, [el('strong', { text: 'Missing or partial coverage' }), list]));
    }
    return article;
  }

  function renderInvestigations() {
    const layout = el('section', { className: 'investigation-layout' });
    const transcript = el('div', { className: 'transcript' });
    if (!state.messages.length) {
      const prompts = [
        ['What changed around the latest errors?', 'Uses only evidence already captured for this project.'],
        ['Which services show repeated failures?', 'Surfaces supporting summaries and missing coverage.'],
        ['What evidence exists for this morning?', 'Keeps the answer scoped to a concrete time window.'],
        ['Where is coverage incomplete?', 'Shows what Logchat cannot support yet.'],
      ];
      const promptList = el('div', { className: 'prompt-list' });
      prompts.forEach(([title, subtitle]) => {
        const item = el('button', { type: 'button', className: 'prompt-button' }, [title, el('span', { text: subtitle })]);
        item.addEventListener('click', () => { $('#question').value = title; $('#question').focus(); });
        promptList.append(item);
      });
      transcript.append(el('div', { className: 'welcome' }, [
        el('h1', { text: 'Ask what the evidence supports.' }),
        el('p', { text: 'Answers stay tied to captured summaries, explicit scope, and visible gaps. A connected port alone is only metadata; evidence appears after stdout is captured or structured events are pushed.' }),
        promptList,
      ]));
    } else {
      state.messages.forEach((message) => transcript.append(messageNode(message)));
    }

    const question = el('textarea', { id: 'question', name: 'question', placeholder: 'Ask about errors, deploys, latency, or a specific time window…', required: true, attrs: { maxlength: '2000', rows: '1' } });
    const submit = button('Ask', 'primary', null, 'arrow');
    submit.type = 'submit';
    const form = el('form', { className: 'composer' }, [question, submit]);
    form.addEventListener('submit', askQuestion);
    const composer = el('div', { className: 'composer-wrap' }, [
      form,
      state.environmentsEnabled ? el('div', { className: 'composer-meta' }, [environmentChecks()]) : null,
    ]);
    layout.append(transcript, composer);
    $('#workspace').replaceChildren(layout);
  }

  async function newConversation() {
    closeNav();
    state.activeConversation = null;
    state.selectedEnvironmentIds = null;
    state.messages = [];
    state.view = 'investigations';
    location.hash = 'investigations';
    updateChrome();
    renderConversations();
    renderInvestigations();
    requestAnimationFrame(() => $('#question') && $('#question').focus());
  }

  async function openConversation(id) {
    if (!state.project || state.busy) return;
    closeNav();
    state.busy = true;
    try {
      const data = await request(`/projects/${encodeURIComponent(projectId())}/conversations/${encodeURIComponent(id)}`);
      state.activeConversation = id;
      state.messages = asArray(data && data.messages);
      if (data.page && data.page.has_older) toast('Showing the latest 500 messages. Earlier topics remain available to bounded conversation recall.');
      const lastResult = [...state.messages].reverse().find((item) => item.result && item.result.plan);
      const names = new Set(asArray(lastResult && lastResult.result.plan.cells).map((cell) => cell.environment));
      state.selectedEnvironmentIds = names.size ? state.environments.filter((env) => names.has(env.name)).map((env) => env.id) : null;
      state.view = 'investigations';
      location.hash = 'investigations';
      updateChrome();
      renderConversations();
      renderInvestigations();
    } catch (error) { toast(error.message); } finally { state.busy = false; }
  }

  async function askQuestion(event) {
    event.preventDefault();
    if (state.busy) return;
    const form = event.currentTarget;
    const question = form.question.value.trim();
    if (!question) return;
    const environment_ids = state.environmentsEnabled
      ? $$('input[name="environment_ids"]:checked', form.parentElement).map((input) => input.value)
      : (state.selectedEnvironmentIds || [((state.environments.find((env) => env.name === 'dev') || state.environments[0]) || {}).id].filter(Boolean));
    state.selectedEnvironmentIds = environment_ids;
    state.busy = true;
    form.querySelector('button').disabled = true;
    state.messages.push({ role: 'user', content: question });
    renderInvestigations();
    try {
      if (!state.activeConversation) {
        const made = await request(`/projects/${encodeURIComponent(projectId())}/conversations`, { method: 'POST', body: JSON.stringify({ title: question.slice(0, 90) }) });
        state.activeConversation = made.id;
        state.conversations.unshift(made);
      }
      const result = await request(`/projects/${encodeURIComponent(projectId())}/conversations/${encodeURIComponent(state.activeConversation)}/messages`, {
        method: 'POST',
        body: JSON.stringify({ question, environment_ids, timezone: timezone(), request_id: uuid() }),
      });
      state.messages.push({ role: 'assistant', result: result.result || result });
      const current = state.conversations.find((conversation) => conversation.id === state.activeConversation);
      if (current) current.updated_at = new Date().toISOString();
    } catch (error) {
      state.messages.push({ role: 'assistant', result: { answer: `The question could not be completed: ${error.message}`, evidence: [], gaps: ['No new evidence was added to this answer.'] } });
    } finally {
      state.busy = false;
      renderConversations();
      renderInvestigations();
      const transcript = $('.transcript');
      if (transcript) transcript.scrollIntoView({ block: 'end' });
    }
  }

  function sourceChunkCount(source) {
    const candidates = [source.chunk_count, source.chunks, source.searchable_chunks];
    const value = candidates.find((item) => Number.isFinite(Number(item)));
    return value == null ? 0 : Number(value);
  }

  function sourceStatus(source) {
    if (source.collection_status === 'rate_limited') return ['waiting', 'Provider rate limit; retry scheduled'];
    if (source.collection_status === 'collection_failed') return ['failed', 'Collection failed'];
    if (source.status === 'failed' || source.error) return ['failed', 'Needs attention'];
    const observed = state.coverage.some((item) => item.source_id === source.id && Number(item.event_count) > 0);
    if (sourceChunkCount(source) > 0 || source.cursor_ts || source.searchable_through || observed) return ['ready', 'Evidence ready'];
    if (source.collection_cursor) return ['waiting', 'Checked; no observations'];
    return ['waiting', 'Waiting for chunks'];
  }

  function commandPanel(kind, port) {
    const path = shellQuote(projectPath());
    const commands = kind === 'process'
      ? [
        ['Attach to metadata', `logchat local attach --project ${path} --port ${port || 3000}`],
        ['Capture stdout on next start', `logchat local run --project ${path} --port ${port || 3000} -- npm run dev`],
      ]
      : [['Generate push integration', `logchat local integration --project ${path}`]];
    const panel = el('div');
    commands.forEach(([label, command]) => {
      const copy = button('', 'icon-button', () => copyText(command), 'copy');
      copy.setAttribute('aria-label', `Copy ${label}`);
      copy.title = `Copy ${label}`;
      panel.append(el('p', { className: 'truth-note', text: label }), el('div', { className: 'code-row' }, [el('pre', { className: 'command', text: command }), copy]));
    });
    panel.append(el('p', { className: 'truth-note', text: kind === 'process'
      ? 'Attaching records the port and process metadata. It does not collect prior logs. Captured stdout requires restarting the app through the run wrapper; structured SDK pushes are another option.'
      : 'Requires an existing local binding in the selected directory (.logchat/local.toml). The browser cannot verify that binding. If unbound, first use logchat local attach with the intended instance state directory; attaching can create a project/source. To access existing evidence, use its already-bound directory instead. Integration only prints configuration; it does not attach. Credentials stay in protected local files.' }));
    return panel;
  }

  function discoveryRow(process) {
    const port = process.port || process.local_port;
    const row = el('div', { className: 'process-row' }, [
      el('div', {}, [el('strong', { text: textValue(process.name || process.process, `Process on ${port}`) }), el('small', { text: textValue(process.path || process.command, 'Path unavailable') })]),
      el('span', { className: 'tabular process-port', text: port ? `:${port}` : 'No port' }),
      el('span', { className: 'tabular muted process-pid', text: process.pid ? `PID ${process.pid}` : 'PID unknown' }),
    ]);
    const attach = button('Connect', 'secondary compact', () => showConnectForm({ port, name: process.name || process.process || `port-${port}`, kind: 'process' }), 'plus');
    attach.disabled = !port;
    row.append(attach);
    return row;
  }

  function showConnectForm(defaults = {}) {
    const panel = $('#connect-panel');
    panel.hidden = false;
    panel.querySelector('[name="name"]').value = defaults.name || '';
    panel.querySelector('[name="port"]').value = defaults.port || '';
    panel.querySelector('[name="kind"]').value = defaults.kind || 'process';
    panel.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  function createConnectPanel() {
    const name = el('input', { name: 'name', placeholder: 'checkout-dev', required: true, attrs: { maxlength: '120' } });
    const environment = el('select', { name: 'environment', required: true });
    const envs = state.environments.length ? state.environments : [{ id: 'dev', name: 'dev' }];
    envs.forEach((item) => environment.append(el('option', { value: item.id || item.name, text: textValue(item.name, item.id) })));
    const kind = el('select', { name: 'kind' }, [el('option', { value: 'process', text: 'Running process metadata' }), el('option', { value: 'push', text: 'Structured push' })]);
    const port = el('input', { type: 'number', name: 'port', placeholder: '3000', attrs: { min: '1', max: '65535' } });
    const fields = el('div', { className: 'connect-grid' }, [field('Source name', name), field('Environment', environment), field('Connection', kind), field('Port', port, true)]);
    const cancel = button('Cancel', 'text-button', () => { form.hidden = true; });
    const submit = button('Add source', 'primary', null, 'arrow'); submit.type = 'submit';
    const form = el('form', { id: 'connect-panel', className: 'connect-panel', hidden: true }, [
      el('div', {}, [el('h2', { text: 'Add a local source' }), el('p', { text: 'This creates source metadata. Follow the generated CLI instruction to begin capture or structured push.' })]),
      fields,
      el('div', { className: 'form-actions' }, [cancel, submit]),
    ]);
    form.addEventListener('submit', connectSource);
    kind.addEventListener('change', () => { port.closest('label').hidden = kind.value !== 'process'; });
    return form;
  }

  async function connectSource(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = Object.fromEntries(new FormData(form));
    const body = { name: values.name.trim(), environment: values.environment, kind: values.kind };
    if (values.kind === 'process' && values.port) body.port = Number(values.port);
    const submit = form.querySelector('button[type="submit"]'); submit.disabled = true;
    try {
      await request(`/projects/${encodeURIComponent(projectId())}/sources`, { method: 'POST', body: JSON.stringify(body) });
      state.connectionInstructions = { kind: values.kind, port: body.port };
      form.reset();
      form.hidden = true;
      toast('Source added. It will stay waiting until evidence chunks arrive.');
      await refreshStatus(false);
      renderSources();
    } catch (error) { form.prepend(notice(error.message)); submit.disabled = false; }
  }

  async function loadSourcesAux() {
    const id = encodeURIComponent(projectId());
    const results = await Promise.allSettled([request('/discovery'), request(`/projects/${id}/coverage`)]);
    state.discovery = results[0].status === 'fulfilled' ? results[0].value : { projects: [], notice: 'Running-port discovery is unavailable.' };
    const coverageData = results[1].status === 'fulfilled' ? results[1].value : [];
    state.coverage = asArray(coverageData && coverageData.coverage).length ? coverageData.coverage : asArray(coverageData);
    if (state.view === 'sources') renderSources();
  }

  function renderSources() {
    const sources = asArray(state.status && state.status.sources);
    const processes = asArray(state.discovery && state.discovery.projects);
    const view = el('section', { className: 'view' });
    const addEnvironment = button('Environment', 'secondary', () => showEnvironmentForm(), 'plus');
    const add = button('Add source', 'primary', () => showConnectForm(), 'plus');
    const discover = button('Refresh discovery', 'secondary', async () => { discover.disabled = true; await loadSourcesAux(); }, 'refresh');
    view.append(viewHeader('Sources & search', 'Discover running local ports, connect capture, and inspect what is actually searchable.', [discover, addEnvironment, add]));
    view.append(el('div', { className: 'discovery-note' }, [icon('info'), el('p', { text: textValue(state.discovery && state.discovery.notice, 'Discovery reports process metadata only. It does not mean logs are being collected.') })]));
    view.append(createConnectPanel());
    if (state.connectionInstructions) view.append(commandPanel(state.connectionInstructions.kind, state.connectionInstructions.port));

    const processSection = el('section', { className: 'section' }, [
      el('div', { className: 'section-head' }, [el('div', {}, [el('h2', { text: 'Running local ports' }), el('p', { text: 'Candidates found on this computer. Connecting one records metadata only.' })])]),
    ]);
    const processList = el('div', { className: 'process-list' });
    processes.forEach((process) => processList.append(discoveryRow(process)));
    processSection.append(processes.length ? processList : emptyState('No running ports found', 'Start a local app, then refresh discovery. You can also add a structured push source manually.'));
    view.append(processSection);

    const sourceSection = el('section', { className: 'section' }, [el('div', { className: 'section-head' }, [el('div', {}, [el('h2', { text: 'Connected sources' }), el('p', { text: 'Source state reflects captured evidence, not just a known port.' })])])]);
    const sourceList = el('div', { className: 'source-list' });
    sources.forEach((source) => {
      const [statusClass, statusLabel] = sourceStatus(source);
      const port = source.port || (source.metadata && source.metadata.port);
      sourceList.append(el('div', { className: 'source-row' }, [
        el('div', {}, [el('strong', { text: textValue(source.name || source.source_name, 'Local source') }), el('small', { text: textValue(source.kind, 'push') })]),
        el('span', { className: 'source-env', text: textValue(source.environment || source.environment_name, 'dev') }),
        el('span', { className: 'tabular muted source-port', text: port ? `:${port}` : 'Push source' }),
        el('span', { className: `status ${statusClass}`, text: statusLabel }),
      ]));
    });
    sourceSection.append(sources.length ? sourceList : emptyState('No sources connected', 'Choose a discovered port or add a structured push source.'));
    view.append(sourceSection);

    const coverageSection = el('section', { className: 'section' }, [el('div', { className: 'section-head' }, [el('div', {}, [el('h2', { text: 'Coverage' }), el('p', { text: 'Recorded windows and known gaps returned by the local store.' })])])]);
    const coverageList = el('ul', { className: 'coverage-list' });
    state.coverage.slice(0, 20).forEach((item) => {
      const status = textValue(item.status, item.gap_reason ? 'gap' : 'recorded');
      const start = item.bucket_start || item.window_start || item.start;
      const end = item.bucket_end || item.window_end || item.end;
      coverageList.append(el('li', {}, [
        el('span', { className: `status ${status === 'complete' || status === 'recorded' ? 'ready' : 'waiting'}`, text: status }),
        el('span', { text: start ? `${formatDate(start, true)} – ${formatDate(end, true)}` : 'Window unavailable' }),
        el('small', { className: 'muted', text: item.event_count == null ? textValue(item.gap_reason, '') : `${item.event_count} observed` }),
      ]));
    });
    coverageSection.append(state.coverage.length ? coverageList : el('p', { text: 'No coverage windows have been recorded yet.' }));
    view.append(coverageSection, createSearchSection());
    $('#workspace').replaceChildren(view);
  }

  function showEnvironmentForm() {
    const view = $('.view');
    const existing = $('#environment-form');
    if (existing) { existing.querySelector('input').focus(); return; }
    const input = el('input', { name: 'name', placeholder: 'staging', required: true, attrs: { pattern: '[A-Za-z0-9_.-]+' } });
    const cancel = button('Cancel', 'text-button', () => form.remove());
    const submit = button('Add environment', 'primary'); submit.type = 'submit';
    const form = el('form', { id: 'environment-form', className: 'connect-panel' }, [
      el('h2', { text: 'Add environment' }), field('Environment name', input), el('div', { className: 'form-actions' }, [cancel, submit]),
    ]);
    form.addEventListener('submit', async (event) => {
      event.preventDefault(); submit.disabled = true;
      try {
        const made = await request(`/projects/${encodeURIComponent(projectId())}/environments`, { method: 'POST', body: JSON.stringify({ name: input.value.trim() }) });
        state.environments.push(made); toast('Environment added.'); renderSources();
      } catch (error) { form.prepend(notice(error.message)); submit.disabled = false; }
    });
    view.insertBefore(form, view.children[2] || null);
    input.focus();
  }

  function createSearchSection() {
    const input = el('input', { name: 'q', placeholder: 'timeout, deploy, payment…', required: true });
    const submit = button('Search', 'secondary', null, 'search'); submit.type = 'submit';
    const form = el('form', { className: 'search-form' }, [input, submit]);
    const results = el('div', { className: 'search-results', hidden: true });
    form.addEventListener('submit', async (event) => {
      event.preventDefault(); submit.disabled = true; results.hidden = false; results.replaceChildren(el('p', { text: 'Searching captured evidence…' }));
      try {
        const params = new URLSearchParams({ q: input.value.trim() });
        const data = await request(`/projects/${encodeURIComponent(projectId())}/search?${params}`);
        const evidence = asArray(data && data.evidence);
        results.replaceChildren();
        evidence.forEach((item, index) => {
          const inspect = button(`Inspect E${index + 1}`, 'text-button', () => openEvidence(item, `E${index + 1}`));
          results.append(el('article', { className: 'search-result' }, [
            el('small', { text: [item.environment, item.service].filter(Boolean).join(' · ') || 'Scope unavailable' }),
            el('p', { text: textValue(item.summary || item.content, 'No summary returned.') }),
            inspect,
          ]));
        });
        if (!evidence.length) results.append(el('p', { text: 'No captured evidence matched this search.' }));
      } catch (error) { results.replaceChildren(notice(error.message)); } finally { submit.disabled = false; }
    });
    return el('section', { className: 'section' }, [el('h2', { text: 'Search captured evidence' }), el('p', { text: 'Inspect retained summaries directly without generating an answer.' }), form, results]);
  }

  function renderCompare() {
    const now = new Date();
    const oneHour = new Date(now.getTime() - 3600000);
    const twoHours = new Date(now.getTime() - 7200000);
    const threeHours = new Date(now.getTime() - 10800000);
    const question = el('input', { name: 'question', value: 'What changed between these windows?', required: true, attrs: { maxlength: '2000' } });
    const environment = el('select', { name: 'environment' });
    const envs = state.environments.length ? state.environments : [{ id: 'dev', name: 'dev' }];
    envs.forEach((item) => environment.append(el('option', { value: item.id, text: textValue(item.name, item.id) })));
    const input = (name, value) => el('input', { type: 'datetime-local', name, value: dateInputValue(value), required: true });
    const cells = el('div', { className: 'compare-inputs' }, [
      el('section', {}, [el('h2', { text: 'A · Baseline' }), field('Start', input('compare_start', threeHours)), field('End', input('compare_end', twoHours))]),
      el('section', {}, [el('h2', { text: 'B · Current' }), field('Start', input('start', oneHour)), field('End', input('end', now))]),
    ]);
    const submit = button('Run comparison', 'primary', null, 'arrow'); submit.type = 'submit';
    const form = el('form', { className: 'compare-form' }, [field('Question', question), field('Environment', environment), cells, submit]);
    const result = el('div', { id: 'compare-result' });
    form.addEventListener('submit', (event) => runComparison(event, result));
    const view = el('section', { className: 'view' }, [viewHeader('Compare evidence', 'Compare two explicit time windows without hiding scope, measured counts, or missing coverage.'), form, result]);
    $('#workspace').replaceChildren(view);
  }

  async function runComparison(event, output) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = Object.fromEntries(new FormData(form));
    const submit = form.querySelector('button[type="submit"]'); submit.disabled = true;
    output.replaceChildren(el('p', { text: 'Comparing captured windows…' }));
    try {
      const result = await request(`/projects/${encodeURIComponent(projectId())}/ask`, { method: 'POST', body: JSON.stringify({
        question: values.question,
        environment_ids: [values.environment],
        timezone: timezone(),
        start: new Date(values.start).toISOString(),
        end: new Date(values.end).toISOString(),
        compare_start: new Date(values.compare_start).toISOString(),
        compare_end: new Date(values.compare_end).toISOString(),
      }) });
      renderComparisonResult(output, result.result || result);
    } catch (error) { output.replaceChildren(notice(error.message)); } finally { submit.disabled = false; }
  }

  function metricValue(value, suffix = '') {
    return value == null ? 'Not measured' : `${value}${suffix}`;
  }

  function renderComparisonResult(output, result) {
    output.replaceChildren();
    const cells = asArray(result.plan && result.plan.cells);
    const metricGrid = el('div', { className: 'comparison-metrics' });
    cells.forEach((cell) => {
      const metrics = cell.metrics || cell.coverage || {};
      const label = [cell.environment, cell.window && cell.window.label].filter(Boolean).join(' · ') || 'Comparison cell';
      const dl = el('dl');
      const observed = metrics.events ?? metrics.observed_events ?? 'Unknown';
      const measured = metrics.duration_count ?? metrics.measured_events ?? 'Unknown';
      const mean = metrics.duration_mean_ms ?? metrics.observed_mean_duration_ms;
      [['Observed events', observed], ['Observed errors', metrics.errors ?? 'Not reported'], ['Measured durations', measured], ['Mean duration', metricValue(mean, ' ms')]].forEach(([key, value]) => dl.append(el('dt', { text: key }), el('dd', { text: value })));
      metricGrid.append(el('section', { className: 'metric-cell' }, [el('h2', { text: label }), dl]));
    });
    if (cells.length) output.append(metricGrid);
    output.append(el('p', { className: 'assumption', text: 'Observed counts cover selected retained chunks. Missing or partial evidence is unknown; it does not prove the real system had no traffic.' }));

    const evidence = asArray(result.evidence);
    const groups = new Map();
    evidence.forEach((item, evidenceIndex) => {
      item.__displayReference = `E${evidenceIndex + 1}`;
      const matching = cells.filter((candidate) => candidate.id === item.cell || asArray(candidate.evidence_ids).includes(item.id));
      const labels = matching.length ? matching.map((cell) => [cell.environment, cell.window && cell.window.label].filter(Boolean).join(' · ')) : [textValue(item.environment, 'Retrieved evidence')];
      labels.forEach((key) => {
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(item);
      });
    });
    const columns = el('div', { className: 'comparison-columns' });
    groups.forEach((items, label) => {
      const column = el('section', { className: 'comparison-column' }, [el('h2', { text: label })]);
      items.forEach((item) => {
        const itemButton = el('button', { type: 'button', className: 'comparison-evidence' }, [
          el('small', { text: textValue(item.service || item.environment, 'Evidence') }),
          el('p', { text: textValue(item.summary, 'No summary returned.') }),
          el('small', { text: item.__displayReference }),
        ]);
        itemButton.addEventListener('click', () => openEvidence(item, item.__displayReference));
        column.append(itemButton);
      });
      columns.append(column);
    });
    if (groups.size) output.append(columns);
    let answer = textValue(result.answer, 'No comparison answer was returned.');
    evidence.forEach((item) => { if (item.id) answer = answer.split(`[${item.id}]`).join(`[${item.__displayReference}]`); });
    const verdict = el('article', { className: 'verdict' }, [icon('compare'), el('div', {}, [el('h2', { text: 'What the evidence supports' }), el('p', { text: answer })])]);
    const assumptions = asArray(result.plan && result.plan.assumptions);
    if (assumptions.length) {
      const assumptionBox = el('div', { className: 'gaps' }, [el('strong', { text: 'Plan assumptions' })]);
      const list = el('ul'); assumptions.forEach((item) => list.append(el('li', { text: textValue(item) }))); assumptionBox.append(list);
      $('div', verdict).append(assumptionBox);
    }
    const gaps = asArray(result.gaps);
    if (gaps.length) {
      const gapBox = el('div', { className: 'gaps' }, [el('strong', { text: 'Missing or partial coverage' })]);
      const list = el('ul'); gaps.forEach((gap) => list.append(el('li', { text: gapText(gap) }))); gapBox.append(list);
      $('div', verdict).append(gapBox);
    }
    output.append(verdict);
  }

  async function renderSettings() {
    const view = el('section', { className: 'view narrow' }, [viewHeader('Settings', 'Configure log retention and the shared model connection.')]);
    const loading = el('p', { text: 'Loading local settings…' }); view.append(loading); $('#workspace').replaceChildren(view);
    const results = await Promise.allSettled([request('/settings/models'), request('/guide'), request('/settings/capture'), request('/settings/ui')]);
    loading.remove();
    const model = results[0].status === 'fulfilled' ? results[0].value : { provider: 'extractive', chat_model: '' };
    const guide = results[1].status === 'fulfilled' ? textValue(results[1].value && results[1].value.guide) : '';
    const capture = results[2].status === 'fulfilled' ? results[2].value : null;
    view.append(captureSettings(capture), modelSettings(model), visibilitySettings(results[3].status === 'fulfilled' ? results[3].value : null), ...(state.project ? [integrationSettings()] : []), el('section', { className: 'settings-section' }, [
      el('div', {}, [el('h2', { text: 'Agent guide' }), el('p', { text: 'Canonical notes about available evidence and its limits.' })]),
      guide ? el('pre', { className: 'guide', text: guide }) : el('p', { text: 'The local guide is unavailable.' }),
    ]));
  }

  function captureSettings(data) {
    const result = el('div', { attrs: { 'aria-live': 'polite' } });
    if (!data) return el('section', { className: 'settings-section' }, [el('h2', { text: 'Log retention' }), notice('Capture settings could not load. Reload Settings to try again.')]);
    const mode = el('select', { name: 'mode' }, [
      el('option', { value: 'summary_only', text: 'Summaries only' }),
      el('option', { value: 'retain_until_summarized', text: 'Retain originals until summarized' }),
    ]); mode.value = data.mode;
    const limit = el('input', { type: 'number', value: String(data.max_bytes / 1048576), attrs: { min: 1, max: 1024, step: 1 } });
    const hours = el('input', { type: 'number', value: String(data.retention_seconds / 3600), attrs: { min: 1 / 60, max: 720, step: 'any' } });
    const detail = el('p', { className: 'truth-note' });
    const update = () => {
      const raw = mode.value === 'retain_until_summarized'; limit.disabled = !raw; hours.disabled = !raw;
      detail.textContent = raw ? 'Original logs may contain sensitive values. They stay on this device until compact summaries are queued or the retention limit expires. Capture pauses when the size limit is reached. Pending logs are not searchable context.' : 'Originals are not stored for new intake. Capture needs the configured model; file sources can retry from their log file. Existing pending originals continue processing or expire.';
    }; mode.addEventListener('change', update); update();
    const status = data.status || {};
    const pending = el('p', { text: `${Number(status.pending_events || 0)} pending events · ${Number(status.pending_bytes || 0).toLocaleString()} bytes. ${Number(status.expired_events || 0)} expired events; ${Number(status.rejected_events || 0)} rejected events. ${data.model_configured ? 'A shared model is configured.' : 'Configure a model with logchat install to create searchable context.'}` });
    const retry = el('p', { className: 'truth-note', text: Number(status.pending_failed_events || 0) ? `${Number(status.pending_failed_events)} pending events await retry after a summarization failure. Their originals remain until processing succeeds or retention expires.` : 'Pending batches are processed automatically when the shared model is available.' });
    const save = button('Save retention settings', 'primary', null); save.type = 'submit';
    const form = el('form', { className: 'settings-form' }, [field('Capture policy', mode), field('Temporary size limit (MiB)', limit), field('Retention limit (hours)', hours), detail, pending, retry, result, el('div', { className: 'form-actions' }, [save])]);
    form.addEventListener('submit', async (event) => {
      event.preventDefault(); save.disabled = true; result.replaceChildren();
      try {
        const response = await request('/settings/capture', { method: 'PUT', body: JSON.stringify({ mode: mode.value, max_bytes: Math.round(Number(limit.value) * 1048576), retention_seconds: Math.round(Number(hours.value) * 3600) }) });
        result.append(notice('Retention settings saved.', 'success'));
        pending.textContent = `${Number(response.status.pending_events || 0)} pending events · ${Number(response.status.pending_bytes || 0).toLocaleString()} bytes.`;
      } catch (error) { result.append(notice(error.message)); } finally { save.disabled = false; }
    });
    return el('section', { className: 'settings-section' }, [el('div', {}, [el('h2', { text: 'Log retention' }), el('p', { text: 'Choose whether capture can temporarily keep originals while summarization is unavailable.' })]), form]);
  }

  function sharedModelSettings(model) {
    const current = el('p', { text: model.chat_model ? `Currently ${model.chat_model}. Select another installed local model for all generation stages.` : 'Choose a local model to turn pending logs into searchable context.' });
    const modelName = el('input', { value: textValue(model.chat_model), placeholder: 'Installed model name', required: true });
    const endpoint = el('input', { value: textValue(model.base_url, 'http://127.0.0.1:11434'), required: true });
    const choices = el('select', { attrs: { 'aria-label': 'Installed generation models' } }, [el('option', { value: '', text: 'Load installed models to choose one' })]);
    const result = el('div', { attrs: { 'aria-live': 'polite' } });
    const list = button('Load installed models', 'secondary', null); list.type = 'button';
    const save = button('Use shared model', 'primary', null); save.type = 'submit';
    const form = el('form', { className: 'settings-form' }, [field('Local model endpoint', endpoint), field('Shared generation model', modelName), choices,
      el('p', { className: 'truth-note', text: 'One selected model handles grouping, summarization and relevance. Internal checks run before saving. Saved memories and the embedding index are preserved; changing models does not rewrite old summaries.' }), result, el('div', { className: 'form-actions' }, [list, save])]);
    choices.addEventListener('change', () => { if (choices.value) modelName.value = choices.value; });
    list.addEventListener('click', async () => {
      list.disabled = true; result.replaceChildren();
      try {
        const data = await request('/settings/models/available');
        choices.replaceChildren(el('option', { value: '', text: 'Choose an installed model' }));
        asArray(data.models).forEach((item) => choices.append(el('option', { value: item.name, text: item.name })));
        result.append(notice(data.models.length ? 'Installed generation models loaded from the configured endpoint.' : 'No compatible generation models were found at the configured endpoint.', data.models.length ? 'success' : 'error'));
      } catch (error) { result.append(notice(error.message)); } finally { list.disabled = false; }
    });
    form.addEventListener('submit', async (event) => {
      event.preventDefault(); save.disabled = true; list.disabled = true; result.replaceChildren(notice('Checking the selected model connection…', 'info'));
      try {
        const data = await request('/settings/shared-model', { method: 'PUT', body: JSON.stringify({ endpoint: endpoint.value.trim(), model: modelName.value.trim() }) });
        modelName.value = data.generation_model; endpoint.value = data.endpoint;
        current.textContent = `Currently ${data.generation_model}. Shared by all generation stages.`;
        result.replaceChildren(notice(`Shared model saved: ${data.generation_model}.`, 'success'));
      } catch (error) { result.replaceChildren(notice(error.message)); } finally { save.disabled = false; list.disabled = false; }
    });
    return el('section', { className: 'settings-section' }, [el('div', {}, [el('h2', { text: 'Shared model' }), current]), form]);
  }

  function visibilitySettings(data) {
    if (!data) return notice('Environment visibility settings could not load. Reload Settings to retry.');
    const enabled = el('input', { type: 'checkbox', checked: data.environments_enabled });
    const result = el('div', { attrs: { 'aria-live': 'polite' } });
    const save = button('Save visibility', 'primary', null); save.type = 'submit';
    const form = el('form', { className: 'settings-form' }, [field('Show environment controls', enabled), el('p', { text: 'Controls stay hidden by default. Visibility does not change which environments an agent can query.' }), result, el('div', { className: 'form-actions' }, [save])]);
    form.addEventListener('submit', async event => {
      event.preventDefault(); save.disabled = true; result.replaceChildren();
      try {
        const value = await request('/settings/ui', { method: 'PUT', body: JSON.stringify({ environments_enabled: enabled.checked }) });
        state.environmentsEnabled = value.environments_enabled; result.append(notice('Visibility saved.', 'success'));
      } catch (error) { result.append(notice(error.message)); } finally { save.disabled = false; }
    });
    return el('section', { className: 'settings-section' }, [el('div', {}, [el('h2', { text: 'Environment visibility' }), el('p', { text: 'Choose whether scope controls appear in investigations.' })]), form]);
  }

  function modelSettings(model) {
    if (model.read_only) return sharedModelSettings(model);
    const provider = el('select', { name: 'provider' }, [
      el('option', { value: 'extractive', text: 'Extractive · evidence only' }),
      el('option', { value: 'ollama', text: 'Ollama · local' }),
      el('option', { value: 'openai_compatible', text: 'OpenAI-compatible' }),
    ]); provider.value = model.provider || 'extractive';
    const chatModel = el('input', { name: 'chat_model', value: textValue(model.chat_model), placeholder: 'Model name' });
    const baseUrl = el('input', { name: 'base_url', value: textValue(model.base_url), placeholder: 'http://localhost:11434' });
    const apiKey = el('input', { type: 'password', name: 'api_key', placeholder: model.has_api_key ? 'Saved · leave blank to keep' : 'Optional for local providers', attrs: { autocomplete: 'new-password' } });
    const fields = el('div', { className: 'provider-fields' }, [field('Provider', provider), field('Chat model', chatModel, true), field('Base URL', baseUrl, true), field('API key', apiKey, true)]);
    const check = button('Check saved connection', 'secondary', null); check.type = 'button';
    const save = button('Save model settings', 'primary', null); save.type = 'submit';
    const result = el('div');
    const form = el('form', { className: 'settings-form' }, [fields, el('p', { className: 'provider-note', text: 'Extractive mode needs no AI provider. Public model APIs should use HTTPS; keyless localhost URLs are supported.' }), result, el('div', { className: 'form-actions' }, [check, save])]);
    const body = () => {
      const values = Object.fromEntries(new FormData(form));
      const payload = { provider: values.provider, chat_model: values.chat_model.trim(), base_url: values.base_url.trim() };
      if (values.api_key) payload.api_key = values.api_key;
      return payload;
    };
    provider.addEventListener('change', () => {
      const disabled = provider.value === 'extractive';
      chatModel.disabled = disabled; baseUrl.disabled = disabled; apiKey.disabled = disabled;
    });
    provider.dispatchEvent(new Event('change'));
    check.addEventListener('click', async () => {
      check.disabled = true; result.replaceChildren();
      try { const data = await request('/settings/models/check', { method: 'POST' }); result.append(notice(textValue(data && (data.detail || data.message), data && data.ok === false ? 'Saved provider is unavailable.' : 'Saved provider is reachable.'), data && data.ok === false ? 'error' : 'success')); }
      catch (error) { result.append(notice(error.message)); } finally { check.disabled = false; }
    });
    form.addEventListener('submit', async (event) => {
      event.preventDefault(); save.disabled = true; result.replaceChildren();
      try { await request('/settings/models', { method: 'PUT', body: JSON.stringify(body()) }); apiKey.value = ''; result.append(notice('Model settings saved.', 'success')); }
      catch (error) { result.append(notice(error.message)); } finally { save.disabled = false; }
    });
    return el('section', { className: 'settings-section' }, [
      el('div', {}, [el('h2', { text: 'Answer model' }), el('p', { text: 'Optional models receive the retrieved redacted summaries used for an answer. Extractive mode remains the local default.' })]),
      form,
    ]);
  }

  function integrationSettings() {
    const command = `logchat local integration --project ${shellQuote(projectPath())}`;
    const copy = button('Copy command', 'secondary', () => copyText(command), 'copy');
    return el('section', { className: 'settings-section' }, [
      el('div', {}, [el('h2', { text: 'Coding-agent integration' }), el('p', { text: 'Print MCP configuration for an already-bound local directory. The browser cannot verify a local binding.' })]),
      el('div', {}, [el('div', { className: 'code-row' }, [el('pre', { className: 'command', text: command }), copy]), el('p', { className: 'truth-note', text: 'Run in a local terminal only after this directory has a .logchat/local.toml binding. If unbound, first run logchat local attach with the intended instance state directory (this can create a project/source), or use an existing binding directory to access existing evidence. Integration does not attach. Project credentials stay in protected local files.' })]),
    ]);
  }

  function memorySnapshot(evidence, result) {
    const fields = ['id', 'project_id', 'environment_id', 'environment', 'source_id', 'source', 'source_kind',
      'service', 'level', 'release', 'fingerprint', 'summary', 'bucket_start', 'bucket_end',
      'event_count', 'duration_count', 'duration_sum_ms', 'duration_min_ms', 'duration_max_ms', 'status_counts',
      'compression_version', 'loss_notes', 'revalidation'];
    const memory = Object.fromEntries(fields.filter((key) => evidence[key] != null).map((key) => [key, evidence[key]]));
    memory.project_id = memory.project_id || (result.plan && result.plan.project_id) || projectId();
    const description = result.provenance && result.provenance.embedding
      ? 'Immutable compressed memory returned with this answer; relevance does not establish cause.'
      : 'Supporting aggregate as returned with this answer; retained buckets may grow.';
    return { memory, provenance: { snapshot: true, description, answer: result.provenance || null }, scope: result.plan || null };
  }

  function openMemories(result) {
    state.evidenceReturnFocus = document.activeElement;
    const evidence = asArray(result.evidence);
    const content = $('#evidence-content');
    const select = el('select', { id: 'supporting-memory-select', attrs: { 'aria-label': 'Select supporting memory' } });
    evidence.forEach((item, index) => select.append(el('option', { value: index, text: `E${index + 1} · ${item.service || item.source || 'Stored aggregate'}` })));
    const details = el('div', { className: 'memory-details' });
    const show = () => {
      const item = evidence[Number(select.value)];
      if (!item) return;
      state.selectedEvidence = item;
      const snapshot = memorySnapshot(item, result);
      const dl = el('dl');
      Object.entries(snapshot.memory).filter(([key]) => key !== 'summary').forEach(([key, value]) => {
        dl.append(el('dt', { text: key.replaceAll('_', ' ') }), el('dd', { text: typeof value === 'object' ? JSON.stringify(value) : value }));
      });
      details.replaceChildren(el('h2', { text: item.service || 'Supporting memory' }),
        el('p', { className: 'evidence-summary', text: item.summary || 'No summary was returned.' }), dl,
        el('p', { className: 'evidence-caution', text: snapshot.provenance.description + ' Correlation does not establish cause.' }),
        el('p', { text: answerProvenance(result).label }),
        button('Copy memory', 'secondary', () => copyText(JSON.stringify(snapshot, null, 2)), 'copy'));
    };
    select.addEventListener('change', show);
    content.replaceChildren(evidence.length ? field('Supporting memory', select) : el('p', { text: 'No supporting memories were retrieved for this answer.' }), details);
    show();
    $('#evidence-panel').hidden = false;
    syncEvidenceLayout();
    $('#app').classList.add('with-evidence');
    (evidence.length ? select : $('#close-evidence')).focus();
  }

  function syncEvidenceLayout(moveFocus = false) {
    const panel = $('#evidence-panel');
    if (panel.hidden) return;
    const overlay = evidenceOverlayMedia.matches;
    panel.setAttribute('role', overlay ? 'dialog' : 'complementary');
    panel.setAttribute('aria-modal', String(overlay));
    if (moveFocus && overlay && !panel.contains(document.activeElement)) {
      (panel.querySelector('select') || $('#close-evidence')).focus();
    }
  }

  function openEvidence(evidence, label) {
    openMemories({ evidence: [evidence] });
  }

  function closeEvidence() {
    $('#evidence-panel').hidden = true;
    $('#app').classList.remove('with-evidence');
    state.selectedEvidence = null;
    if (state.evidenceReturnFocus && state.evidenceReturnFocus.isConnected) state.evidenceReturnFocus.focus();
    state.evidenceReturnFocus = null;
  }

  function renderAgents() {
    $('#workspace').replaceChildren(el('section', { className: 'view narrow' }, [
      viewHeader('Connect your agent.', 'Give a coding agent read-only access to this project’s summarized evidence.'),
      integrationSettings(),
      el('p', { text: 'Sources, comparisons, host observations and optional configuration remain available through the authenticated CLI, client and API.' }),
    ]));
  }

  function renderTimeline() {
    const select=el('select', {attrs:{'aria-label':'Timeline source'}});
    [['','All sources'],['application','Selected application'],['system','System'],['network','Network'],['gpu','GPU'],['process','Processes']].forEach(([value,label])=>select.append(el('option',{value,text:label})));
    select.value=state.timelineCategory;
    select.addEventListener('change',()=>{state.timelineCategory=select.value;loadTimeline();});
    const refresh=button('Refresh','secondary',()=>loadTimeline(),'refresh');
    const view=el('section',{className:'view timeline-view'},[
      viewHeader('Your machine, in context.','Automatic observations and connected application evidence, ordered by time.',[select,refresh]),
      el('p',{className:'assumption',text:'Collection starts with Logchat. Network counters describe activity, not request contents. Existing app output appears after a logging connection.'}),
      el('section',{id:'capture-status',className:'capture-status',attrs:{'aria-label':'Capture availability'}},[el('p',{text:'Checking available sources…'})]),
      el('p',{id:'timeline-scope',className:'assumption'}),
      el('ol',{id:'timeline-events',className:'timeline-events',attrs:{'aria-label':'Recorded observations'}}),
      el('p',{id:'timeline-error',attrs:{role:'status'}}),
    ]);
    $('#workspace').replaceChildren(view);
    loadTimeline();
  }

  async function loadTimeline() {
    if(state.timelineLoading || state.view!=='timeline') return;
    const expected=projectId();const category=state.timelineCategory;
    state.timelineLoading=true;
    try {
      const parameters=new URLSearchParams({category,limit:'100'});
      if(expected) parameters.set('project_id',expected);
      const data=await request('/host/timeline?'+parameters.toString());
      if(state.view!=='timeline' || projectId()!==expected || state.timelineCategory!==category) return;
      const capture=data.capture||{};
      const statuses=el('dl');
      const reasons={permission_limited:'Some processes require additional access',process_limit_reached:'Process scan limit reached',nvidia_smi_not_found:'NVIDIA tooling is not installed',platform_unsupported:'Not supported on this platform',collector_exited:'Collector stopped; access may be restricted',collector_failed:'Collector failed; other sources remain available',not_started:'Capture has not started',not_checked:'Not checked yet'};
      Object.entries(capture.capabilities||{}).forEach(([name,item])=>{
        statuses.append(el('dt',{text:name==='os_logs'?'OS error stream':name==='gpu'?'GPU':name.charAt(0).toUpperCase()+name.slice(1)}),el('dd',{text:[item.status,reasons[item.reason]||item.reason?.replaceAll('_',' ')].filter(Boolean).join(' · ')}));
      });
      $('#capture-status').replaceChildren(el('h2',{text:'Capture availability'}),statuses);
      $('#timeline-scope').textContent=(expected?'Application scope: '+state.project.name+'. ':'No application selected. ')+(capture.dropped_events?`${capture.dropped_events} observation(s) dropped; history is incomplete. `:'')+'Showing up to 100 recent observations. Host observations are shared within this OS user. Correlation does not establish cause.';
      const rows=asArray(data.events);
      const list=$('#timeline-events');list.replaceChildren();
      rows.forEach((item)=>{
        const stamp=value=>new Intl.DateTimeFormat(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit',second:'2-digit'}).format(new Date(value));
        const application=item.category==='application';
        const timeLabel=application?`${stamp(item.bucket_start)} – ${stamp(item.bucket_end)} · aggregate window`:`${stamp(item.timestamp)} · observation`;
        const meta=el('div',{className:'timeline-meta'},[el('time',{text:timeLabel,attrs:{datetime:item.timestamp}}),el('span',{text:item.category}),el('span',{text:item.level||'info'}),item.process?el('span',{text:item.process}):null,item.pid?el('span',{text:'PID '+item.pid}):null]);
        const details=el('dl',{className:'timeline-measurements'});
        Object.entries(item.metrics||{}).forEach(([key,value])=>details.append(el('dt',{text:key.replaceAll('_',' ')}),el('dd',{text:typeof value==='number'?Number(value.toFixed(2)).toLocaleString():String(value)})));
        list.append(el('li',{},[meta,el('p',{text:item.summary}),details,el('small',{className:'timeline-reference',text:'Evidence '+item.id})]));
      });
      if(!rows.length)list.append(el('li',{},[el('h2',{text:'Waiting for observations'}),el('p',{text:'Available collectors run in the background. Unavailable sources stay marked above; application output needs a logging connection.'})]));
      $('#timeline-error').textContent=asArray(data.limitations).join(' ');
    } catch(error) {
      if(state.view==='timeline' && $('#timeline-error')) $('#timeline-error').textContent=error.message+' Refresh to retry.';
    } finally {state.timelineLoading=false; if(state.view==='timeline' && (projectId()!==expected || state.timelineCategory!==category))loadTimeline();}
  }

  function renderView() {
    updateChrome();
    renderConversations();
    if (state.view === 'settings') { renderSettings(); return; }
    if (!state.project) return showNoProject();
    if (state.view === 'investigations') renderInvestigations();
    if (state.view === 'agents') renderAgents();
    $('#workspace').focus({ preventScroll: true });
  }

  function bindChrome() {
    $('#project-picker').addEventListener('change', (event) => chooseProject(event.target.value));
    $('#show-project-form').addEventListener('click', () => {
      const form = $('#project-form'); form.hidden = !form.hidden;
      if (!form.hidden) form.querySelector('input').focus();
    });
    $('[data-action="cancel-project"]').addEventListener('click', () => { $('#project-form').hidden = true; });
    $('#project-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      const submit = event.currentTarget.querySelector('button[type="submit"]'); submit.disabled = true;
      try { await createProject(event.currentTarget); } catch (error) { event.currentTarget.prepend(notice(error.message)); } finally { submit.disabled = false; }
    });
    $$('.nav-item').forEach((item) => item.addEventListener('click', () => {
      state.view = item.dataset.view; location.hash = state.view; closeNav(); renderView();
    }));
    $('#new-conversation').addEventListener('click', newConversation);
    $('#close-evidence').addEventListener('click', closeEvidence);
    evidenceOverlayMedia.addEventListener('change', () => syncEvidenceLayout(true));
    $('#evidence-panel').addEventListener('keydown', (event) => {
      if (event.key !== 'Tab' || !evidenceOverlayMedia.matches) return;
      const controls = $$('button, select, [tabindex="0"]', $('#evidence-panel')).filter((node) => !node.disabled);
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    });
    $('#open-nav').addEventListener('click', openNav);
    $('#close-nav').addEventListener('click', closeNav);
    $('#nav-scrim').addEventListener('click', closeNav);
    window.addEventListener('hashchange', () => {
      const next = location.hash.slice(1); if (next in viewLabels && next !== state.view) { state.view = next; renderView(); }
    });
    window.addEventListener('keydown', (event) => { if (event.key === 'Escape') { closeNav(); closeEvidence(); } });
  }

  function openNav() { $('#sidebar').classList.add('is-open'); $('#nav-scrim').hidden = false; }
  function closeNav() { $('#sidebar').classList.remove('is-open'); $('#nav-scrim').hidden = true; }

  async function init() {
    bindChrome();
    try {
      await loadProjects();
      await loadProjectData();
      if (!state.project && !location.hash) state.view='investigations';
      $('#boot').hidden = true;
      $('#app').hidden = false;
      updateChrome();
      renderView();
      startPolling();
    } catch (error) {
      $('#boot').replaceChildren(icon('alert'), el('strong', { text: 'Local Logchat could not start.' }), el('span', { text: error.message }), button('Try again', 'secondary', () => location.reload(), 'refresh'));
    }
  }

  init();
})();
