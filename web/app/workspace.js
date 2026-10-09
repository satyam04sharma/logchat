'use client';

import {
  ArrowRight, ArrowUp, BookOpen, Check, ChevronDown, CircleAlert, Clipboard, Columns2, Container,
  Database, FileText, HardDrive, KeyRound, LoaderCircle, LogOut, Menu, MessagesSquare, Play,
  PanelRightClose, PanelRightOpen, Plug, Plus, RefreshCw, ScanText, Search,
  Settings2, Trash2, X,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

const API = '/api';
const timezone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
const asArray = (value) => Array.isArray(value) ? value : [];
const formatDate = (value, detail = false) => {
  if (!value) return 'Not processed yet';
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return String(value);
  return new Intl.DateTimeFormat(undefined, detail
    ? { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', second: '2-digit' }
    : { month: 'short', day: 'numeric' }).format(date);
};

async function request(path, options = {}) {
  const response = await fetch(API + path, {
    credentials: 'include', cache: 'no-store', ...options,
    headers: options.body ? { 'Content-Type': 'application/json', ...options.headers } : options.headers,
  });
  const text = await response.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!response.ok) {
    const error = new Error(typeof data?.detail === 'string' ? data.detail : `Request failed (${response.status}).`);
    error.status = response.status;
    throw error;
  }
  return data;
}

function IconButton({ label, children, className = '', ...props }) {
  return <button className={`icon-button ${className}`} aria-label={label} title={label} {...props}>{children}</button>;
}

function Empty({ icon: Icon, title, body, action }) {
  return <div className="empty-state"><Icon aria-hidden="true" /><h3>{title}</h3><p>{body}</p>{action}</div>;
}

function Notice({ error }) { return error ? <div className="error-banner" role="alert"><CircleAlert />{error}</div> : null; }

function Auth({ health, onSignedIn }) {
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function submit(event) {
    event.preventDefault(); const values = Object.fromEntries(new FormData(event.currentTarget));
    setBusy(true); setError('');
    try {
      await request('/session', { method: 'POST', body: JSON.stringify({ ...values, create: creating }) });
      await onSignedIn();
    } catch (issue) { setError(issue.message); } finally { setBusy(false); }
  }
  return <main className="auth-shell">
    <section className="auth-intro">
      <a className="brand" href="/"><ScanText aria-hidden="true" />logchat</a>
      <div><h1>Ask what changed.<br />Inspect what supports it.</h1><p>Logchat turns retained log summaries into answers you can trace back to their environment, service, and time window.</p></div>
      <p className={`stack-state ${health === 'ready' ? 'is-ready' : ''}`}><span aria-hidden="true" />{health === 'ready' ? 'Local stack ready' : health === 'checking' ? 'Checking local stack…' : 'Local stack unavailable'}</p>
    </section>
    <section className="auth-panel" aria-labelledby="auth-title">
      <div><h2 id="auth-title">{creating ? 'Create your local account' : 'Welcome back'}</h2><p>{creating ? 'Your account stays with the stack on this computer.' : 'Sign in to your local Logchat workspace.'}</p></div>
      <Notice error={error} />
      <form onSubmit={submit} className="form-stack">
        <label>Email<input name="email" type="email" autoComplete="username" required autoFocus /></label>
        <label>Password<input name="password" type="password" minLength={12} maxLength={72} autoComplete={creating ? 'new-password' : 'current-password'} required /></label>
        <button className="primary full" disabled={busy}>{busy && <LoaderCircle className="spin" />}{busy ? 'Connecting…' : creating ? 'Create account' : 'Sign in'}<ArrowRight /></button>
      </form>
      <button className="text-button" disabled={busy} onClick={() => { setCreating(!creating); setError(''); }}>{creating ? 'I already have an account' : 'Create a local account'}</button>
      <p className="privacy-note"><HardDrive />Credentials stay on this computer. Raw logs are processed in memory and never stored by Logchat.</p>
    </section>
  </main>;
}

function Onboarding({ onCreated }) {
  const [step, setStep] = useState(0);
  const [project, setProject] = useState(null);
  const [environments, setEnvironments] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function createProject(event) {
    event.preventDefault(); const { name } = Object.fromEntries(new FormData(event.currentTarget));
    setBusy(true); setError('');
    try {
      const made = await request('/projects', { method: 'POST', body: JSON.stringify({ name }) });
      const envs = asArray(made.environments).length ? made.environments : await request(`/projects/${made.id}/environments`);
      setProject(made); setEnvironments(envs); setStep(1);
    } catch (issue) { setError(issue.message); } finally { setBusy(false); }
  }
  async function connect(event) {
    event.preventDefault(); const values = Object.fromEntries(new FormData(event.currentTarget));
    setBusy(true); setError('');
    try {
      await request(`/projects/${project.id}/sources`, { method: 'POST', body: JSON.stringify({
        environment_id: values.environment_id, connector: 'docker', source_project_id: values.container,
        retention_seconds: Math.round(Number(values.retention) * 3600),
        connector_config: { container: values.container, ...(values.service ? { service: values.service } : {}) },
      }) });
      await onCreated(project.id);
    } catch (issue) { setError(issue.message); } finally { setBusy(false); }
  }
  return <main className="onboarding-shell">
    <header><a className="brand" href="/"><ScanText />logchat</a><span>Setup</span></header>
    <section className="onboarding-card">
      <div className="step-track" aria-label={`Step ${step + 1} of 2`}><span className="done" /><span className={step === 1 ? 'done' : ''} /></div>
      {step === 0 ? <>
        <h1>Start with a project.</h1><p>A project keeps its environments, sources, investigations, and evidence scope together.</p><Notice error={error} />
        <form onSubmit={createProject} className="form-stack"><label>Project name<input name="name" placeholder="Checkout service" maxLength={200} required autoFocus /></label><button className="primary" disabled={busy}>{busy ? 'Creating…' : 'Create project'}<ArrowRight /></button></form>
      </> : <>
        <h1>Connect your first source.</h1><p>Choose one Docker container. Logchat begins building searchable history from the retained logs it can reach.</p><Notice error={error} />
        <form onSubmit={connect} className="form-grid">
          <label>Environment<select name="environment_id" required>{environments.map((env) => <option key={env.id} value={env.id}>{env.name}</option>)}</select></label>
          <label>Docker container<input name="container" placeholder="checkout-api" required autoFocus /></label>
          <label>Service name <span>Optional</span><input name="service" placeholder="api" /></label>
          <label>Retained history <span>Hours</span><input name="retention" type="number" min="0.01" step="any" defaultValue="24" required /></label>
          <div className="form-actions"><button type="button" className="secondary" onClick={() => onCreated(project.id)}>Connect later</button><button className="primary" disabled={busy}>{busy ? 'Checking source…' : 'Connect source'}<ArrowRight /></button></div>
        </form>
      </>}
    </section>
  </main>;
}

function Sidebar({ open, setOpen, view, setView, project, projects, setProject, conversations, activeConversation, openConversation, newConversation, signOut, locked }) {
  const nav = [['investigations', MessagesSquare, 'Investigations'], ['compare', Columns2, 'Compare'], ['sources', Plug, 'Sources'], ['settings', Settings2, 'Settings']];
  return <aside className={`sidebar ${open ? 'is-open' : ''}`}>
    <div className="sidebar-top"><a className="brand" href="/"><ScanText />logchat</a><IconButton label="Close navigation" className="mobile-only" onClick={() => setOpen(false)}><X /></IconButton></div>
    <label className="project-picker">Project<select value={project} disabled={locked} onChange={(event) => setProject(event.target.value)}>{projects.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select><ChevronDown aria-hidden="true" /></label>
    <nav aria-label="Workspace">{nav.map(([id, Icon, label]) => <button key={id} className={view === id ? 'active' : ''} onClick={() => { setView(id); setOpen(false); }}><Icon />{label}</button>)}</nav>
    <section className="recent">
      <div className="recent-heading"><span>Recent questions</span><IconButton label="New investigation" disabled={locked} onClick={newConversation}><Plus /></IconButton></div>
      {conversations.slice(0, 8).map((item) => <button disabled={locked} className={activeConversation === item.id ? 'chosen' : ''} key={item.id} onClick={() => openConversation(item.id)}>{item.title || 'Untitled investigation'}<time>{formatDate(item.updated_at)}</time></button>)}
      {!conversations.length && <p>Your saved investigations will appear here.</p>}
    </section>
    <div className="sidebar-foot"><span><HardDrive />Local workspace</span><button onClick={signOut}><LogOut />Sign out</button></div>
  </aside>;
}

function EnvironmentPicker({ environments, selected, setSelected }) {
  return <fieldset className="environment-picker"><legend>Environments</legend>{environments.map((env) => <label key={env.id}><input type="checkbox" checked={selected.includes(env.id)} onChange={(event) => setSelected((current) => event.target.checked ? [...current, env.id] : current.filter((id) => id !== env.id))} />{env.name}</label>)}</fieldset>;
}

function EvidencePanel({ evidence, close }) {
  if (!evidence) return <aside className="evidence-panel evidence-empty"><div className="panel-heading"><span>Supporting evidence</span>{close}</div><Database /><h3>Select a citation</h3><p>Inspect its source, environment, service, and covered window without leaving the investigation.</p></aside>;
  const start = evidence.bucket_start || evidence.window_start || evidence.start, end = evidence.bucket_end || evidence.window_end || evidence.end;
  const evidenceTitle = evidence.title || `${evidence.service ? evidence.service.toUpperCase() : 'Log'} evidence`;
  const sourceLabel = [evidence.connector, evidence.source_project_id].filter(Boolean).join(' / ') || evidence.source || 'Stored summary';
  return <aside className="evidence-panel">
    <div className="panel-heading"><span>Supporting evidence</span>{close}</div><div className="evidence-id">{evidence.display_reference || (evidence.id ? `Summary ${String(evidence.id).slice(0, 8)}` : 'Evidence')}</div>
    <h3>{evidenceTitle}</h3><p className="evidence-copy">{evidence.summary || evidence.content || 'No summary was returned for this evidence item.'}</p>
    <dl><dt>Environment</dt><dd>{evidence.environment || 'Not specified'}</dd><dt>Service</dt><dd>{evidence.service || 'Not specified'}</dd><dt>Source</dt><dd>{sourceLabel}</dd><dt>Window</dt><dd>{start ? `${formatDate(start, true)}${end ? ` – ${formatDate(end, true)}` : ''}` : 'Not specified'}</dd>{evidence.cell && <><dt>Comparison cell</dt><dd>{String(evidence.cell).split(':').at(-1)}</dd></>}</dl>
    <div className="evidence-note">This is a redacted summary from retained evidence. It may describe correlation; it does not establish cause on its own.</div>
  </aside>;
}

function Answer({ result, onEvidence }) {
  if (!result) return null; const evidence = asArray(result.evidence);
  const readableAnswer = evidence.reduce((text, item, index) => item.id ? text.split(item.id).join(`E${index + 1}`) : text, result.answer || 'No answer was returned.');
  return <article className="answer"><div className="answer-label"><ScanText />What the evidence supports</div><div className="answer-copy">{readableAnswer}</div>
    {!!evidence.length && <div className="citations" aria-label="Citations">{evidence.map((item, index) => <button key={item.id || index} onClick={() => onEvidence({ ...item, display_reference: `E${index + 1}` })}>[E{index + 1}] {[item.environment, item.service].filter(Boolean).join(' · ') || 'Evidence'}</button>)}</div>}
    {asArray(result.gaps).map((gap, index) => <div className="caution" key={index}><CircleAlert />{typeof gap === 'string' ? gap : gap.summary || JSON.stringify(gap)}</div>)}
    {asArray(result.plan?.assumptions).map((item, index) => <p className="assumption" key={index}>{item}</p>)}
  </article>;
}

function Investigation({ project, environments, selected, setSelected, conversations, setConversations, activeConversation, setActiveConversation, messages, setMessages, onEvidence, onBusyChange }) {
  const [draft, setDraft] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const scopeRef = useRef({ project, activeConversation });
  const retryRef = useRef(null);
  useEffect(() => { scopeRef.current = { project, activeConversation }; }, [project, activeConversation]);
  const draftKey = `logchat:draft:${project}:${activeConversation || 'new'}`;
  useEffect(() => { setDraft(localStorage.getItem(draftKey) || ''); }, [draftKey]);
  useEffect(() => { const timer = setTimeout(() => localStorage.setItem(draftKey, draft), 250); return () => clearTimeout(timer); }, [draft, draftKey]);
  const current = conversations.find((item) => item.id === activeConversation);
  async function ask(event) {
    event.preventDefault(); const question = draft.trim(); if (!question || !selected.length || busy) return;
    setBusy(true); onBusyChange(true); setError(''); let conversationId = activeConversation; const ownerProject = project;
    try {
      if (!conversationId) {
        const conversation = await request(`/projects/${project}/conversations`, { method: 'POST', body: JSON.stringify({ title: question.slice(0, 120) }) });
        conversationId = conversation.id; scopeRef.current = { project: ownerProject, activeConversation: conversationId }; setConversations((items) => [conversation, ...items]); setActiveConversation(conversationId);
      }
      const isOwner = () => scopeRef.current.project === ownerProject && scopeRef.current.activeConversation === conversationId;
      const optimistic = { id: `draft-${Date.now()}`, role: 'user', content: question };
      const requestKey = `logchat:request:${ownerProject}:${conversationId}`;
      let stored = null; try { stored = JSON.parse(localStorage.getItem(requestKey)); } catch { localStorage.removeItem(requestKey); }
      const prior = retryRef.current || stored;
      const requestId = prior?.question === question && prior?.project === ownerProject && prior?.conversationId === conversationId ? prior.id : crypto.randomUUID();
      retryRef.current = { id: requestId, question, project: ownerProject, conversationId };
      localStorage.setItem(requestKey, JSON.stringify(retryRef.current));
      if (isOwner()) { setMessages((items) => [...items, optimistic]); setDraft(''); localStorage.removeItem(draftKey); }
      const response = await request(`/projects/${ownerProject}/conversations/${conversationId}/messages`, { method: 'POST', body: JSON.stringify({ question, environment_ids: selected, timezone: timezone(), request_id: requestId }) });
      const result = response.result || response.assistant_message?.result;
      if (isOwner()) {
        retryRef.current = null; localStorage.removeItem(requestKey);
        setMessages((items) => [...items.filter((item) => item.id !== optimistic.id), response.user_message || optimistic, { ...(response.assistant_message || {}), role: 'assistant', content: response.assistant_message?.content || result?.answer, result }]);
        setConversations((items) => items.map((item) => item.id === conversationId ? { ...item, title: item.title || question.slice(0, 120), updated_at: new Date().toISOString() } : item));
      }
    } catch (issue) {
      if (scopeRef.current.project === ownerProject && scopeRef.current.activeConversation === conversationId) { setError(issue.message); setDraft(question); setMessages((items) => items.filter((item) => !String(item.id).startsWith('draft-'))); }
    } finally { setBusy(false); onBusyChange(false); }
  }
  return <section className="conversation-view">
    <div className="conversation-head"><div><span>Investigation</span><h1>{current?.title || 'Ask your logs a question'}</h1></div><EnvironmentPicker environments={environments} selected={selected} setSelected={setSelected} /></div>
    <div className={`message-stream ${messages.length ? '' : 'is-empty'}`}>
      {!messages.length && <Empty icon={MessagesSquare} title="Begin with a change you noticed" body="Ask about an error, a slowdown, or a difference between environments. Logchat will show what the retained evidence can and cannot support." />}
      {messages.map((message) => message.role === 'user' ? <div className="user-message" key={message.id || message.created_at}>{message.content}</div> : <Answer key={message.id || message.created_at} result={message.result || { answer: message.content }} onEvidence={onEvidence} />)}
      {busy && <div className="thinking" role="status"><LoaderCircle className="spin" /><span>Searching retained evidence…</span><small>Longer questions can take a moment on a local model.</small></div>}
    </div>
    <div className="composer-wrap"><Notice error={error} /><form className="composer" onSubmit={ask}><label htmlFor="question">Ask a follow-up</label><textarea id="question" value={draft} onChange={(event) => setDraft(event.target.value)} rows={2} maxLength={2000} placeholder="Why did API requests slow down this week?" onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); event.currentTarget.form.requestSubmit(); } }} /><button className="primary composer-send" aria-label="Ask Logchat" disabled={busy || !draft.trim() || !selected.length}><ArrowUp /></button><p>{selected.length ? 'Enter to ask · Shift + Enter for a new line' : 'Select at least one environment'}</p></form></div>
  </section>;
}

function Sources({ project, environments, setEnvironments, status, refreshStatus }) {
  const [selectedId, setSelectedId] = useState(null), [query, setQuery] = useState(''), [searchEnv, setSearchEnv] = useState('');
  const [results, setResults] = useState(null), [coverage, setCoverage] = useState([]), [busy, setBusy] = useState(false), [error, setError] = useState(''), [adding, setAdding] = useState(false), [addingEnvironment, setAddingEnvironment] = useState(false);
  const sources = asArray(status?.sources), jobs = asArray(status?.recent_jobs);
  const selected = sources.find((source) => source.id === selectedId) || sources[0] || null;
  const visibleCoverage = coverage.filter((item) => !selected || !item.source_id || item.source_id === selected.id);
  useEffect(() => { if (!sources.some((source) => source.id === selectedId)) setSelectedId(sources[0]?.id || null); }, [sources, selectedId]);
  useEffect(() => { request(`/projects/${project}/coverage`).then((data) => setCoverage(asArray(data?.coverage))).catch(() => setCoverage([])); }, [project, status?.chunks]);
  const jobFor = (source) => jobs.find((job) => job.source_id === source.id);
  async function runSearch(event) {
    event.preventDefault(); setBusy(true); setError('');
    try { const params = new URLSearchParams({ q: query }); if (searchEnv) params.set('environment_id', searchEnv); const data = await request(`/projects/${project}/search?${params}`); setResults(asArray(data?.evidence)); } catch (issue) { setError(issue.message); } finally { setBusy(false); }
  }
  async function retry() { setBusy(true); setError(''); try { await request(`/projects/${project}/retry`, { method: 'POST' }); await refreshStatus(); } catch (issue) { setError(issue.message); } finally { setBusy(false); } }
  async function connect(event) {
    event.preventDefault(); const values = Object.fromEntries(new FormData(event.currentTarget)); setBusy(true); setError('');
    try { await request(`/projects/${project}/sources`, { method: 'POST', body: JSON.stringify({ environment_id: values.environment_id, connector: 'docker', source_project_id: values.container, retention_seconds: Math.round(Number(values.retention) * 3600), connector_config: { container: values.container, ...(values.service ? { service: values.service } : {}) } }) }); setAdding(false); await refreshStatus(); } catch (issue) { setError(issue.message); } finally { setBusy(false); }
  }
  async function addEnvironment(event) {
    event.preventDefault(); const values = Object.fromEntries(new FormData(event.currentTarget)); setBusy(true); setError('');
    try { const made = await request(`/projects/${project}/environments`, { method: 'POST', body: JSON.stringify({ name: values.name }) }); setEnvironments((items) => [...items.filter((item) => item.id !== made.id), made].sort((a, b) => a.name.localeCompare(b.name))); setAddingEnvironment(false); } catch (issue) { setError(issue.message); } finally { setBusy(false); }
  }
  return <section className="sources-view">
    <div className="view-title"><div><h1>Sources</h1><p>Connectivity and searchable history are separate. Check both before relying on an answer.</p></div><div className="title-actions"><button className="secondary" onClick={() => setAddingEnvironment(!addingEnvironment)}><Plus />Environment</button><button className="primary" onClick={() => setAdding(!adding)}><Plus />Connect source</button></div></div><Notice error={error} />
    {jobs.some((job) => job.status === 'failed') && <div className="attention"><CircleAlert /><div><strong>Processing needs attention.</strong><p>One or more retained windows failed. Fix the source or model issue, then retry the same window.</p></div><button className="secondary" disabled={busy} onClick={retry}><RefreshCw />Retry failed jobs</button></div>}
    {adding && <form className="source-connect" onSubmit={connect}><label>Environment<select name="environment_id">{environments.map((env) => <option key={env.id} value={env.id}>{env.name}</option>)}</select></label><label>Docker container<input name="container" placeholder="checkout-api" required /></label><label>Service <span>Optional</span><input name="service" placeholder="api" /></label><label>Retained hours<input name="retention" type="number" min="0.01" defaultValue="24" required /></label><div><button type="button" className="text-button" onClick={() => setAdding(false)}>Cancel</button><button className="primary" disabled={busy}>Check and connect</button></div></form>}
    {addingEnvironment && <form className="environment-form" onSubmit={addEnvironment}><label>Environment name<input name="name" placeholder="prod" pattern="[a-z][a-z0-9_-]{0,39}" required autoFocus /></label><button type="button" className="text-button" onClick={() => setAddingEnvironment(false)}>Cancel</button><button className="primary" disabled={busy}>Add environment</button></form>}
    <div className="source-table" role="table" aria-label="Connected sources"><div className="source-row source-header" role="row"><span>Source / service</span><span>Environment</span><span>Searchable through</span><span>Processing</span></div>
      {sources.map((source) => { const job = jobFor(source), state = job?.status || (source.cursor_ts ? 'ready' : 'waiting'); return <button role="row" className={`source-row ${selected?.id === source.id ? 'selected' : ''}`} key={source.id} onClick={() => setSelectedId(source.id)}><span><Container /> <span><strong>{source.source_project_id}</strong><small>{source.connector}</small></span></span><span>{source.environment}</span><span>{formatDate(source.cursor_ts, true)}</span><span className={`status ${state}`}>{state === 'ready' ? 'Ready' : state === 'failed' ? 'Retry needed' : state}</span></button>; })}
      {!sources.length && <Empty icon={Plug} title="No sources connected" body="Connect a Docker container to begin building searchable history." />}</div>
    {!!selected && <div className="source-detail"><section><h2>{selected.source_project_id}</h2><p>{selected.cursor_ts ? `Connected and searchable through ${formatDate(selected.cursor_ts, true)}.` : 'Connected. The first searchable window has not completed yet.'}</p><dl><dt>Environment</dt><dd>{selected.environment}</dd><dt>Connector</dt><dd>{selected.connector}</dd><dt>Next check</dt><dd>{formatDate(selected.next_check_at, true)}</dd><dt>Project searchable chunks</dt><dd>{status?.chunks ?? 0}</dd></dl></section><section><h2>Coverage</h2>{visibleCoverage.length ? <ul className="coverage-list">{visibleCoverage.slice(0, 8).map((item, index) => { const start = item.bucket_start || item.window_start || item.start, end = item.bucket_end || item.window_end || item.end; return <li key={item.id || index}><span className={`status ${item.status || ''}`}>{item.status || 'Recorded'}</span><strong>{start ? `${formatDate(start, true)} – ${formatDate(end, true)}` : 'Window not specified'}</strong>{item.gap_reason && <small>{item.gap_reason}</small>}</li>; })}</ul> : <p>No coverage windows were returned for this source yet. Processing may still be underway.</p>}</section></div>}
    <section className="search-console"><div><h2>Search evidence</h2><p>Inspect retained summaries directly, without generating an answer.</p></div><form onSubmit={runSearch}><div><Search /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="timeout, deploy, payment…" required /></div><select value={searchEnv} onChange={(event) => setSearchEnv(event.target.value)} aria-label="Filter by environment"><option value="">All environments</option>{environments.map((env) => <option key={env.id} value={env.id}>{env.name}</option>)}</select><button className="secondary" disabled={busy}>{busy ? 'Searching…' : 'Search'}</button></form>{results && <div className="search-results">{results.length ? results.map((item, index) => <article key={item.id || index}><div><span>{item.environment || 'Environment unspecified'}</span><span>{item.service || 'Service unspecified'}</span></div><p>{item.summary || item.content}</p><small>{item.id}</small></article>) : <p>No retained evidence matched this search and scope.</p>}</div>}</section>
  </section>;
}

function Compare({ project, environments, onEvidence }) {
  const [mode, setMode] = useState('time'), [busy, setBusy] = useState(false), [error, setError] = useState(''), [result, setResult] = useState(null);
  async function submit(event) {
    event.preventDefault(); const values = Object.fromEntries(new FormData(event.currentTarget)); setBusy(true); setError(''); setResult(null);
    try {
      const environment_ids = mode === 'environment' ? [values.environment_a, values.environment_b].filter((value, index, list) => value && list.indexOf(value) === index) : [values.environment_a];
      if (mode === 'environment' && environment_ids.length < 2) throw new Error('Choose two different environments to compare.');
      const body = { question: values.question, environment_ids, timezone: timezone(), start: new Date(values.start).toISOString(), end: new Date(values.end).toISOString() };
      if (mode === 'time') { body.compare_start = new Date(values.compare_start).toISOString(); body.compare_end = new Date(values.compare_end).toISOString(); }
      setResult(await request(`/projects/${project}/ask`, { method: 'POST', body: JSON.stringify(body) }));
    } catch (issue) { setError(issue.message); } finally { setBusy(false); }
  }
  const evidence = asArray(result?.evidence), split = evidence.reduce((groups, item) => { const cell = asArray(result?.plan?.cells).find((cell) => cell.id === item.cell); const key = cell ? `${cell.environment} · ${cell.window.label}` : item.environment || 'Retrieved evidence'; (groups[key] ||= []).push(item); return groups; }, {});
  return <section className="compare-view">
    <div className="view-title"><div><h1>Compare evidence</h1><p>Keep scope, measurements, retrieved evidence, and gaps aligned.</p></div><div className="segmented"><button className={mode === 'time' ? 'active' : ''} onClick={() => setMode('time')}>Time windows</button><button className={mode === 'environment' ? 'active' : ''} onClick={() => setMode('environment')}>Environments</button></div></div>
    <form className="compare-form" onSubmit={submit}><label>Question<input name="question" defaultValue="What changed between these scopes?" maxLength={2000} required /></label><div className="compare-inputs">
      <section><h2>A · Baseline</h2><label>Environment<select name="environment_a">{environments.map((env) => <option key={env.id} value={env.id}>{env.name}</option>)}</select></label>{mode === 'time' ? <><label>Start<input name="compare_start" type="datetime-local" required /></label><label>End<input name="compare_end" type="datetime-local" required /></label></> : <><label>Shared start<input name="start" type="datetime-local" required /></label><label>Shared end<input name="end" type="datetime-local" required /></label></>}</section>
      <section><h2>B · Current</h2>{mode === 'environment' ? <><label>Environment<select name="environment_b">{environments.map((env) => <option key={env.id} value={env.id}>{env.name}</option>)}</select></label><p className="locked-scope"><Check />Same time window</p></> : <><p className="locked-scope"><Check />Same environment</p><label>Start<input name="start" type="datetime-local" required /></label><label>End<input name="end" type="datetime-local" required /></label></>}</section>
    </div><Notice error={error} /><button className="primary" disabled={busy || !environments.length}>{busy && <LoaderCircle className="spin" />}{busy ? 'Comparing evidence…' : 'Run comparison'}<ArrowRight /></button></form>
    {result && <div className="comparison-result"><div className="comparison-metrics">{asArray(result.plan?.cells).map((cell) => <section key={cell.id}><h2>{cell.environment} · {cell.window?.label}</h2><dl><dt>Observed events</dt><dd>{cell.coverage?.observed_events ?? 0}</dd><dt>Mean duration</dt><dd>{cell.coverage?.observed_mean_duration_ms == null ? 'Not measured' : `${Math.round(cell.coverage.observed_mean_duration_ms)} ms`}</dd><dt>Measured events</dt><dd>{cell.coverage?.measured_events ?? 0}</dd><dt>Coverage</dt><dd>{cell.coverage?.complete ? 'Complete' : 'Incomplete'}</dd></dl></section>)}</div><p className="assumption">These are measurements from retained chunks, not complete traffic totals, rates or latency percentiles.</p><div className="comparison-columns">{Object.entries(split).map(([label, items]) => <section key={label}><h2>{label}</h2>{items.map((item, index) => <button className="comparison-evidence" key={item.id || index} onClick={() => onEvidence(item)}><span>{item.service || item.environment || 'Evidence'}</span><p>{item.summary}</p><small>{item.id}</small></button>)}</section>)}{!evidence.length && <p>No comparable evidence was returned for these scopes.</p>}</div><article className="verdict"><Columns2 /><div><h2>What the evidence supports</h2><p>{result.answer}</p>{asArray(result.gaps).map((gap, index) => <div className="caution" key={index}><CircleAlert />{typeof gap === 'string' ? gap : gap.summary || JSON.stringify(gap)}</div>)}</div></article></div>}
  </section>;
}

function McpServerPanel({ server, details, busy, setBusy, onError }) {
  const tools = asArray(details?.tools);
  const [toolName, setToolName] = useState(tools[0]?.name || ''), [argumentsText, setArgumentsText] = useState('{}'), [toolResult, setToolResult] = useState(null);
  const [resources, setResources] = useState(null), [resourceResult, setResourceResult] = useState(null), [notice, setNotice] = useState('');
  useEffect(() => { if (!tools.some((tool) => tool.name === toolName)) setToolName(tools[0]?.name || ''); }, [tools, toolName]);
  async function callTool(event) {
    event.preventDefault(); setBusy(`tool:${server.id}`); onError(''); setToolResult(null);
    try {
      let parsed; try { parsed = JSON.parse(argumentsText || '{}'); } catch { throw new Error('Tool arguments must be valid JSON.'); }
      const data = await request(`/settings/mcp/${server.id}/tools/call`, { method: 'POST', body: JSON.stringify({ name: toolName, arguments: parsed }) });
      setToolResult(data); setNotice(data.notice || 'External tool output is unverified and is not added to Logchat evidence.');
    } catch (issue) { onError(issue.message); } finally { setBusy(''); }
  }
  async function loadResources() {
    setBusy(`resources:${server.id}`); onError('');
    try { const data = await request(`/settings/mcp/${server.id}/resources`); setResources(asArray(data.resources)); setNotice(data.notice || 'External resources are unverified and are not added to Logchat evidence.'); } catch (issue) { onError(issue.message); } finally { setBusy(''); }
  }
  async function readResource(uri) {
    setBusy(`resource:${server.id}`); onError(''); setResourceResult(null);
    try { const data = await request(`/settings/mcp/${server.id}/resources/read`, { method: 'POST', body: JSON.stringify({ uri }) }); setResourceResult(data.result); setNotice(data.notice || 'External resource content is unverified and is not added to Logchat evidence.'); } catch (issue) { onError(issue.message); } finally { setBusy(''); }
  }
  return <div className="mcp-inspector">
    <div className="tool-list"><span>{details.server?.name} {details.server?.version}</span>{tools.map((tool) => <p key={tool.name}><strong>{tool.name}</strong><span>{tool.description}</span><em>{tool.read_only_declared ? 'Read-only declared' : 'Write capability possible'}</em></p>)}</div>
    {tools.length > 0 && <form className="tool-runner" onSubmit={callTool}><label>Tool<select value={toolName} onChange={(event) => setToolName(event.target.value)}>{tools.map((tool) => <option key={tool.name} value={tool.name}>{tool.name}</option>)}</select></label><label>JSON arguments<textarea value={argumentsText} onChange={(event) => setArgumentsText(event.target.value)} rows={4} spellCheck="false" /></label><button className="secondary" disabled={!!busy || !toolName}>{busy === `tool:${server.id}` ? 'Running…' : <><Play />Run selected tool</>}</button></form>}
    <div className="resource-browser"><button className="secondary" disabled={!!busy} onClick={loadResources}><FileText />{busy === `resources:${server.id}` ? 'Loading…' : 'List resources'}</button>{resources && <ul>{resources.length ? resources.map((resource) => <li key={resource.uri}><div><strong>{resource.name || resource.uri}</strong><span>{resource.uri}</span></div><button className="text-button" disabled={!!busy} onClick={() => readResource(resource.uri)}>Read</button></li>) : <li>No resources were reported by this server.</li>}</ul>}</div>
    {notice && <p className="external-notice"><CircleAlert />{notice}</p>}
    {toolResult !== null && <div className="external-output"><strong>Tool output</strong><pre>{JSON.stringify(toolResult, null, 2)}</pre></div>}
    {resourceResult !== null && <div className="external-output"><strong>Resource content</strong><pre>{typeof resourceResult === 'string' ? resourceResult : JSON.stringify(resourceResult, null, 2)}</pre></div>}
  </div>;
}

function Settings({ status, project }) {
  const [model, setModel] = useState(null), [servers, setServers] = useState([]), [serverDetails, setServerDetails] = useState({});
  const [guide, setGuide] = useState(''), [agentConfig, setAgentConfig] = useState(null), [agentNotice, setAgentNotice] = useState('');
  const [adding, setAdding] = useState(false), [busy, setBusy] = useState(''), [error, setError] = useState(''), [message, setMessage] = useState('');
  const load = useCallback(async () => {
    try { const [nextModel, nextServers, nextGuide] = await Promise.all([request('/settings/models'), request('/settings/mcp'), request('/guide')]); setModel(nextModel); setServers(asArray(nextServers)); setGuide(nextGuide.guide || ''); }
    catch (issue) { setError(issue.message); }
  }, []);
  useEffect(() => { load(); }, [load]);
  async function modelBody(event) { const values = Object.fromEntries(new FormData(event.currentTarget)); return { provider: values.provider, chat_model: values.chat_model, ...(values.base_url ? { base_url: values.base_url } : {}), ...(values.api_key ? { api_key: values.api_key } : {}) }; }
  async function saveModel(event) { event.preventDefault(); setBusy('model'); setError(''); setMessage(''); try { setModel(await request('/settings/models', { method: 'PUT', body: JSON.stringify(await modelBody(event)) })); setMessage('Model settings saved.'); event.currentTarget.api_key.value = ''; } catch (issue) { setError(issue.message); } finally { setBusy(''); } }
  async function checkModel(event) { event.preventDefault(); setBusy('check'); setError(''); setMessage(''); try { const data = await request('/settings/models/check', { method: 'POST', body: JSON.stringify(await modelBody(event)) }); setMessage(data.detail || `${data.model} is reachable.`); } catch (issue) { setError(issue.message); } finally { setBusy(''); } }
  async function pullModel() { setBusy('pull'); setError(''); try { const data = await request('/settings/models/pull', { method: 'POST', body: JSON.stringify({ model: model?.chat_model || status?.models?.chat_model || 'qwen2.5:1.5b' }) }); setMessage(`${data.model} is ready.`); await load(); } catch (issue) { setError(issue.message); } finally { setBusy(''); } }
  async function addServer(event) { event.preventDefault(); const values = Object.fromEntries(new FormData(event.currentTarget)); setBusy('server'); setError(''); try { const made = await request('/settings/mcp', { method: 'POST', body: JSON.stringify({ name: values.name, url: values.url, transport: values.transport, enabled: true, ...(values.api_key ? { api_key: values.api_key } : {}) }) }); setServers((items) => [...items, made]); setAdding(false); } catch (issue) { setError(issue.message); } finally { setBusy(''); } }
  async function checkServer(id) { setBusy(id); setError(''); try { const data = await request(`/settings/mcp/${id}/check`, { method: 'POST' }); setServerDetails((items) => ({ ...items, [id]: data })); } catch (issue) { setError(issue.message); } finally { setBusy(''); } }
  async function removeServer(id) { setBusy(id); setError(''); try { await request(`/settings/mcp/${id}`, { method: 'DELETE' }); setServers((items) => items.filter((item) => item.id !== id)); } catch (issue) { setError(issue.message); } finally { setBusy(''); } }
  async function generateAgentConfig() { setBusy('agent'); setError(''); try { const data = await request(`/projects/${project}/agent-config`, { method: 'POST' }); setAgentConfig(data.mcpServers); setAgentNotice(data.notice || 'Configuration generated for this signed-in project session.'); } catch (issue) { setError(issue.message); } finally { setBusy(''); } }
  async function copyAgentConfig() { try { await navigator.clipboard.writeText(JSON.stringify({ mcpServers: agentConfig }, null, 2)); setMessage('Agent configuration copied.'); } catch { setError('Could not copy automatically. Select the JSON and copy it manually.'); } }
  return <section className="settings-view">
    <div className="view-title"><div><h1>Settings</h1><p>Choose how answers are generated and manage optional external connections.</p></div></div><Notice error={error} />{message && <div className="success-banner" role="status"><Check />{message}</div>}
    <section className="settings-section"><div><h2>Answer model</h2><p>Embeddings stay local. External providers receive only redacted summaries when you explicitly select them.</p></div>{model ? <form onSubmit={saveModel} onReset={checkModel} className="settings-form">
      <label>Provider<select name="provider" defaultValue={model.provider}><option value="ollama">Local · Ollama</option><option value="openai_compatible">OpenAI-compatible</option></select></label>
      <label>Chat model<input name="chat_model" defaultValue={model.chat_model} required /></label>
      <label>Base URL <span>Optional for local</span><input name="base_url" type="url" defaultValue={model.base_url || ''} placeholder="http://ollama:11434" /></label>
      <label>API key <span>{model.has_api_key ? 'Stored · enter to replace' : 'Optional'}</span><input name="api_key" type="password" autoComplete="off" /></label>
      <div className="form-actions"><button type="reset" className="secondary" disabled={!!busy}>{busy === 'check' ? 'Checking…' : 'Check connection'}</button><button className="primary" disabled={!!busy}>{busy === 'model' ? 'Saving…' : 'Save settings'}</button></div>
    </form> : <LoaderCircle className="spin" />}</section>
    <section className="settings-section"><div><h2>Local model readiness</h2><p>The embedding model stays local for every provider.</p></div><div className="readiness"><p><span className={status?.models?.chat ? 'ready-dot' : 'missing-dot'} />Pipeline chat model<strong>{status?.models?.chat ? 'Ready' : 'Missing'}</strong></p><p><span className={status?.models?.embedding ? 'ready-dot' : 'missing-dot'} />Embedding model<strong>{status?.models?.embedding ? 'Ready' : 'Missing'}</strong></p>{(model?.provider === 'ollama' || !status?.models?.embedding) && <button className="secondary" onClick={pullModel} disabled={!!busy}>{busy === 'pull' ? 'Pulling model…' : 'Pull selected model'}</button>}</div></section>
    <section className="settings-section"><div><h2>Knowledge & agent</h2><p>Read the evidence-handling guide or create an MCP configuration bound to this signed-in project session.</p></div><div className="agent-knowledge"><details><summary><BookOpen />Agent evidence guide</summary><pre>{guide || 'The guide is unavailable.'}</pre></details><a className="secondary download-link" href="/api/guide/skill" download><HardDrive />Download agent skill</a><button className="primary" disabled={!!busy} onClick={generateAgentConfig}>{busy === 'agent' ? 'Generating…' : 'Generate agent configuration'}</button>{agentNotice && <p>{agentNotice}</p>}{agentConfig && <div className="agent-config"><div><strong>Configuration JSON</strong><button className="text-button" onClick={copyAgentConfig}><Clipboard />Copy</button></div><pre>{JSON.stringify({ mcpServers: agentConfig }, null, 2)}</pre></div>}</div></section>
    <section className="settings-section"><div><h2>MCP connections</h2><p>Inspect and explicitly run tools from optional external servers. Their output stays separate from verified Logchat evidence.</p></div><div>{servers.map((server) => <article className="server-row" key={server.id}><div><strong>{server.name}</strong><span>{server.url} · {server.transport}</span></div><span className={`status ${server.enabled ? 'ready' : 'waiting'}`}>{server.enabled ? 'Enabled' : 'Disabled'}</span><button className="secondary" disabled={!!busy} onClick={() => checkServer(server.id)}>{busy === server.id ? 'Checking…' : 'Check'}</button><IconButton label={`Remove ${server.name}`} disabled={!!busy} onClick={() => removeServer(server.id)}><Trash2 /></IconButton>{serverDetails[server.id] && <McpServerPanel server={server} details={serverDetails[server.id]} busy={busy} setBusy={setBusy} onError={setError} />}</article>)}</div>
      {adding ? <form className="mcp-form" onSubmit={addServer}><label>Name<input name="name" required placeholder="Internal tools" /></label><label>Server URL<input name="url" type="url" required placeholder="https://mcp.example.com" /></label><label>Transport<select name="transport"><option value="streamable_http">Streamable HTTP</option><option value="sse">SSE</option></select></label><label>API key <span>Optional</span><input name="api_key" type="password" autoComplete="off" /></label><div className="form-actions"><button type="button" className="text-button" onClick={() => setAdding(false)}>Cancel</button><button className="primary" disabled={!!busy}>{busy === 'server' ? 'Connecting…' : 'Add server'}</button></div></form> : <button className="secondary" onClick={() => setAdding(true)}><Plus />Add MCP server</button>}
    </section>
  </section>;
}

export default function Workspace() {
  const [health, setHealth] = useState('checking'), [signed, setSigned] = useState(null), [projects, setProjects] = useState([]), [project, setProject] = useState('');
  const [environments, setEnvironments] = useState([]), [selectedEnvironments, setSelectedEnvironments] = useState([]), [status, setStatus] = useState(null), [conversations, setConversations] = useState([]), [activeConversation, setActiveConversation] = useState(null), [messages, setMessages] = useState([]);
  const [view, setView] = useState('investigations'), [evidence, setEvidence] = useState(null), [evidenceOpen, setEvidenceOpen] = useState(true), [menuOpen, setMenuOpen] = useState(false), [loading, setLoading] = useState(true), [investigationBusy, setInvestigationBusy] = useState(false);
  const activeProjectLoad = useRef(0), activeConversationLoad = useRef(0), projectRef = useRef(project);
  useEffect(() => { projectRef.current = project; }, [project]);
  const loadProjects = useCallback(async () => { try { const rows = await request('/projects'); setProjects(asArray(rows)); setSigned(true); setProject((current) => current && rows.some((item) => item.id === current) ? current : rows[0]?.id || ''); return rows; } catch { setSigned(false); return []; } finally { setLoading(false); } }, []);
  const refreshStatus = useCallback(async () => { if (!project) return; const next = await request(`/projects/${project}/status`); setStatus(next); return next; }, [project]);
  const loadProject = useCallback(async (id) => {
    const token = ++activeProjectLoad.current; activeConversationLoad.current += 1; setLoading(true); setActiveConversation(null); setMessages([]); setEvidence(null);
    try { const [envs, nextStatus, nextConversations] = await Promise.all([request(`/projects/${id}/environments`), request(`/projects/${id}/status`), request(`/projects/${id}/conversations`)]); if (token !== activeProjectLoad.current) return; setEnvironments(asArray(envs)); setSelectedEnvironments(asArray(envs).slice(0, 1).map((env) => env.id)); setStatus(nextStatus); setConversations(asArray(nextConversations)); } finally { if (token === activeProjectLoad.current) setLoading(false); }
  }, []);
  const loadConversation = useCallback(async (id) => { const ownerProject = project; const token = ++activeConversationLoad.current; const data = await request(`/projects/${ownerProject}/conversations/${id}`); if (token !== activeConversationLoad.current || projectRef.current !== ownerProject) return; setMessages(asArray(data?.messages)); const priorAnswer = asArray(data?.messages).filter((message) => message.role === 'assistant').at(-1); const scopedIds = [...new Set(asArray(priorAnswer?.result?.plan?.cells).map((cell) => cell.id?.split(':')[0]).filter(Boolean))]; if (scopedIds.length) setSelectedEnvironments(scopedIds); setEvidence(null); setActiveConversation(id); setView('investigations'); setMenuOpen(false); }, [project]);
  useEffect(() => { request('/health/ready').then(() => setHealth('ready')).catch(() => setHealth('unavailable')); loadProjects(); }, [loadProjects]);
  useEffect(() => { if (project) loadProject(project).catch(() => setLoading(false)); }, [project, loadProject]);
  useEffect(() => { const pending = asArray(status?.recent_jobs).some((job) => ['queued', 'leased', 'running', 'pending'].includes(job.status)); if (!pending || !project) return; const timer = setInterval(() => refreshStatus().catch(() => {}), 8000); return () => clearInterval(timer); }, [project, status?.recent_jobs, refreshStatus]);
  useEffect(() => { const media = window.matchMedia('(max-width: 1000px)'); const adapt = () => { if (media.matches) setEvidenceOpen(false); }; adapt(); media.addEventListener('change', adapt); return () => media.removeEventListener('change', adapt); }, []);
  useEffect(() => { const close = (event) => { if (event.key === 'Escape') { setMenuOpen(false); setEvidenceOpen(false); } }; window.addEventListener('keydown', close); return () => window.removeEventListener('keydown', close); }, []);
  const projectName = useMemo(() => projects.find((item) => item.id === project)?.name || '', [projects, project]);
  async function signOut() { await request('/session', { method: 'DELETE' }).catch(() => {}); setSigned(false); setProjects([]); setProject(''); }
  async function onCreated(id) { await loadProjects(); setProject(id); setSigned(true); }
  function selectEvidence(item) { setEvidence(item); setEvidenceOpen(true); }
  if (signed === null) return <div className="boot"><ScanText /><LoaderCircle className="spin" /><span>Opening local workspace…</span></div>;
  if (!signed) return <Auth health={health} onSignedIn={loadProjects} />;
  if (!projects.length) return <Onboarding onCreated={onCreated} />;
  const supportsEvidence = view === 'investigations' || view === 'compare';
  return <div className={`app-shell ${evidenceOpen && supportsEvidence ? 'with-evidence' : ''}`}>
    <Sidebar open={menuOpen} setOpen={setMenuOpen} view={view} setView={(next) => { setView(next); setEvidence(null); }} project={project} projects={projects} setProject={setProject} conversations={conversations} activeConversation={activeConversation} openConversation={(id) => loadConversation(id).catch(() => {})} newConversation={() => { activeConversationLoad.current += 1; setActiveConversation(null); setMessages([]); setEvidence(null); setView('investigations'); setMenuOpen(false); }} signOut={signOut} locked={investigationBusy} />
    {menuOpen && <button className="scrim" aria-label="Close navigation" onClick={() => setMenuOpen(false)} />}
    <header className="topbar"><IconButton label="Open navigation" className="mobile-only" onClick={() => setMenuOpen(true)}><Menu /></IconButton><span>{projectName} / {view}</span><span className={`sync-state ${health === 'ready' ? 'ready' : ''}`}><i />{health === 'ready' ? 'Local · connected' : health === 'checking' ? 'Local · checking' : 'Local · unavailable'}</span>{supportsEvidence && <IconButton label={evidenceOpen ? 'Close evidence panel' : 'Open evidence panel'} onClick={() => setEvidenceOpen(!evidenceOpen)}>{evidenceOpen ? <PanelRightClose /> : <PanelRightOpen />}</IconButton>}</header>
    <main className="workspace-main" aria-busy={loading}>{loading ? <div className="loading-view"><LoaderCircle className="spin" />Loading workspace…</div> : <>{view === 'investigations' && <Investigation project={project} environments={environments} selected={selectedEnvironments} setSelected={setSelectedEnvironments} conversations={conversations} setConversations={setConversations} activeConversation={activeConversation} setActiveConversation={setActiveConversation} messages={messages} setMessages={setMessages} onEvidence={selectEvidence} onBusyChange={setInvestigationBusy} />}{view === 'sources' && <Sources project={project} environments={environments} setEnvironments={setEnvironments} status={status} refreshStatus={refreshStatus} />}{view === 'compare' && <Compare project={project} environments={environments} onEvidence={selectEvidence} />}{view === 'settings' && <Settings status={status} project={project} />}</>}</main>
    {evidenceOpen && supportsEvidence && <EvidencePanel evidence={evidence} close={<IconButton label="Close evidence panel" onClick={() => setEvidenceOpen(false)}><X /></IconButton>} />}
  </div>;
}
