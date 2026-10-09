// Execute the shipped renderer with a small DOM double; this is not browser QA.
const fs = require('node:fs');
const vm = require('node:vm');
const { execFileSync } = require('node:child_process');
const assert = require('node:assert/strict');
let document;
class Node {
  constructor(tag) { this.tagName = tag; this.children = []; this.attrs = {}; this.style = {}; this.listeners = {}; this.nodeType = 1; this.isConnected = true; this.value = ''; this.hidden = false; this.classList = { add() {}, remove() {}, toggle() {} }; }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return (this.text || '') + this.children.map(n => n.textContent).join(''); }
  setAttribute(key, value) { this.attrs[key] = value; }
  append(...nodes) { this.children.push(...nodes); if (this.tagName === 'select' && this.children.length && !this.value) this.value = this.children[0].value; }
  replaceChildren(...nodes) { this.children = nodes; this.text = ''; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  focus() { document.activeElement = this; }
  remove() {}
  contains(node) { return node === this || this.children.some(child => child.contains(node)); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  querySelectorAll(selector) {
    const all = this.children.flatMap(n => [n, ...n.querySelectorAll('*')]);
    return all.filter(n => selector === '*' || selector.split(',').some(s => {
      s = s.trim();
      if (s.startsWith('#')) return n.id === s.slice(1);
      if (s.startsWith('.')) return (n.className || '').split(' ').includes(s.slice(1));
      if (s.includes('input[')) return n.tagName === 'input' && n.name === 'environment_ids' && (!s.endsWith(':checked') || n.checked);
      return n.tagName === s;
    }));
  }
}
const ids = {};
document = { activeElement: null, createElement: tag => new Node(tag), createElementNS: (_ns, tag) => new Node(tag), createTextNode: text => { const n = new Node('text'); n.textContent = text; return n; },
  querySelector: key => ids[key] ||= new Node('div'), querySelectorAll: () => [] };
let clipboard;
let viewportWidth = 600;
const mediaQueries = new Map();
function resizeViewport(width) {
  viewportWidth = width;
  for (const media of mediaQueries.values()) {
    const matches = width <= media.maxWidth;
    if (matches !== media.matches) {
      media.matches = matches;
      for (const listener of media.listeners) listener({ matches });
    }
  }
}
const sandbox = { document, location: { hash: '' }, window: { matchMedia: query => {
  const maxWidth = /^\(max-width: (\d+)px\)$/.exec(query);
  assert.ok(maxWidth, `Unsupported media query: ${query}`);
  if (!mediaQueries.has(query)) {
    mediaQueries.set(query, { maxWidth: Number(maxWidth[1]), matches: viewportWidth <= Number(maxWidth[1]), listeners: [],
      addEventListener(name, listener) { assert.equal(name, 'change'); this.listeners.push(listener); } });
  }
  return mediaQueries.get(query);
}, addEventListener() {} }, navigator: { clipboard: { writeText: async text => { clipboard = text; } } }, crypto: {}, Intl, Date, setTimeout: () => {}, clearInterval() {}, setInterval() {} };
const source = fs.readFileSync('src/logchat/local/static/app.js', 'utf8').replace('  init();\n})();', '  globalThis.testUI = { state, viewLabels, messageNode, renderInvestigations, openMemories, closeEvidence, bindChrome, shellQuote, integrationSettings, commandPanel };\n})();');
vm.runInNewContext(source, sandbox);
const ui = sandbox.testUI;
function find(node, tag, text) { return node.querySelectorAll(tag).find(n => n.textContent === text); }
(async () => {
  assert.deepEqual(Object.keys(ui.viewLabels), ['investigations', 'agents', 'settings']);
  ui.state.project = { id: 'project-bound', name: 'Synthetic' };
  ui.state.environments = [{ id: 'dev-id', name: 'dev' }, { id: 'prod-id', name: 'prod' }];
  ui.renderInvestigations();
  assert.equal(ids['#workspace'].querySelectorAll('input').length, 0);
  assert.ok(!ids['#workspace'].textContent.includes('model'));
  ui.state.environmentsEnabled = true;
  ui.renderInvestigations();
  assert.equal(ids['#workspace'].querySelectorAll('input').length, 2);
  const result = { answer: 'Scoped counts', provenance: { provider: 'extractive', model: 'deterministic_aggregate' }, plan: { project_id: 'project-bound', timezone: 'UTC', cells: [] }, gaps: [], cited_evidence_ids: ['m0'], findings: [{ observation: '25 observed events', interpretation: 'No cause established', evidence_ids: ['m0'] }],
    token: 'must-not-copy', evidence: Array.from({ length: 25 }, (_, i) => ({ id: `m${i}`, summary: `Aggregate ${i}`, environment: 'prod', service: 'api', bucket_start: '2026-10-01T00:00:00Z', bucket_end: '2026-10-01T00:15:00Z', event_count: 1, token: 'must-not-copy' })) };
  const answer = ui.messageNode({ result });
  assert.equal(answer.querySelectorAll('button').filter(n => n.textContent === 'Supporting memories').length, 1);
  assert.equal(answer.querySelectorAll('button').length, 2);
  assert.ok(answer.textContent.includes('Facts computed by Logchat · extractive · deterministic_aggregate'));
  assert.ok(answer.textContent.includes('25 supporting memories'));
  assert.ok(!answer.textContent.includes('Scoped counts'));
  assert.equal(answer.textContent.split('25 observed events').length - 1, 1);
  assert.ok(!answer.textContent.includes('Evidence: E'));
  assert.ok(!answer.textContent.includes('m24'));
  const supporting = find(answer, 'button', 'Supporting memories');
  supporting.focus(); supporting.listeners.click();
  const panel = ids['#evidence-content'];
  const select = panel.querySelector('select');
  assert.equal(select.children.length, 25);
  assert.equal(document.activeElement, select);
  select.value = '24'; select.listeners.change();
  assert.equal(ui.state.selectedEvidence.id, 'm24');
  await find(panel, 'button', 'Copy memory').listeners.click();
  const copiedMemory = JSON.parse(clipboard);
  assert.equal(copiedMemory.memory.id, 'm24');
  assert.equal(copiedMemory.memory.project_id, 'project-bound');
  assert.equal(copiedMemory.memory.environment, 'prod');
  assert.equal(copiedMemory.provenance.snapshot, true);
  assert.ok(!clipboard.includes('must-not-copy'));
  await find(answer, 'button', 'Copy answer').listeners.click();
  assert.equal(JSON.parse(clipboard).provenance.provider, 'extractive');
  assert.equal(JSON.parse(clipboard).plan.project_id, 'project-bound');
  assert.equal(JSON.parse(clipboard).evidence.length, 25);
  assert.equal(JSON.parse(clipboard).evidence[24].id, 'm24');
  assert.equal(JSON.parse(clipboard).computed_provider_role.role, 'deterministic_computed_facts');
  // Selection provenance cannot imply authorship of computed facts.
  const modelResult = { ...result, model_output_policy: 'evidence_selection_only; unsupported generated prose is not published', provenance: { provider: 'openai_compatible', model: 'fixture', status: 'model', computed_findings_provider: 'extractive' }, evidence: result.evidence.slice(0, 20), findings: [{ ...result.findings[0], evidence_ids: result.evidence.slice(0, 20).map(row => row.id) }] };
  const modelAnswer = ui.messageNode({ result: modelResult });
  assert.ok(modelAnswer.textContent.includes('Evidence selected by openai_compatible · fixture'));
  assert.ok(modelAnswer.textContent.includes('Facts computed by Logchat · extractive'));
  assert.ok(modelAnswer.textContent.includes('20 supporting memories'));
  assert.ok(!modelAnswer.textContent.includes('E20'));
  assert.ok(!modelAnswer.textContent.includes('Answered by'));
  assert.ok(!modelAnswer.textContent.includes('Scoped counts'));
  assert.equal(modelAnswer.querySelectorAll('button').filter(n => n.textContent === 'Supporting memories').length, 1);
  await find(modelAnswer, 'button', 'Copy answer').listeners.click();
  assert.equal(JSON.parse(clipboard).model_output_policy, modelResult.model_output_policy);
  assert.equal(JSON.parse(clipboard).computed_provider_role.provider, 'extractive');
  assert.equal(JSON.parse(clipboard).cited_evidence_ids[0], 'm0');
  assert.equal(JSON.parse(clipboard).evidence.length, 20);
  const emptyAnswer = ui.messageNode({ result: { answer: 'No matching evidence; behavior unknown', findings: [], evidence: [], gaps: ['Missing coverage'], provenance: { provider: 'extractive', model: 'deterministic_aggregate', status: 'extractive', reason: 'empty_evidence' } } });
  assert.ok(emptyAnswer.textContent.includes('behavior unknown'));
  assert.ok(emptyAnswer.textContent.includes('Missing coverage'));
  assert.ok(emptyAnswer.textContent.includes('Facts computed by Logchat · extractive'));
  const fallback = ui.messageNode({ result: { ...result, provenance: { provider: 'extractive', model: 'deterministic_aggregate', status: 'fallback' } } });
  assert.ok(fallback.textContent.includes('extractive · deterministic_aggregate · fallback'));
  const legacy = ui.messageNode({ role: 'assistant', content: 'Historical answer' });
  assert.ok(legacy.textContent.includes('Historical answer'));
  assert.ok(legacy.textContent.includes('Answer provenance not recorded'));
  assert.ok(!clipboard.includes('must-not-copy'));
  ui.closeEvidence();
  assert.equal(document.activeElement, supporting);
  ui.openMemories({ evidence: [] });
  assert.ok(panel.textContent.includes('No supporting memories'));
  assert.equal(document.activeElement, ids['#close-evidence']);
  ui.closeEvidence();
  // Use the actual shipped CSS breakpoint, so JS/CSS drift fails this contract.
  const css = fs.readFileSync('src/logchat/local/static/style.css', 'utf8');
  const overlayWidth = Number(css.match(/@media \(max-width: (\d+)px\)\s*\{[^}]*\}[^@]*?\.evidence-panel\s*\{\s*position: fixed;/)[1]);
  assert.equal(overlayWidth, 900);
  ui.bindChrome();
  const inspector = ids['#evidence-panel'];
  const close = ids['#close-evidence'];
  close.tagName = 'button';
  inspector.replaceChildren(close, panel);
  for (const width of [600, 850, overlayWidth, overlayWidth + 1, 1200]) {
    resizeViewport(width);
    supporting.focus();
    supporting.listeners.click();
    const overlay = width <= overlayWidth;
    assert.equal(inspector.attrs.role, overlay ? 'dialog' : 'complementary', `role at ${width}px`);
    assert.equal(inspector.attrs['aria-modal'], String(overlay), `aria-modal at ${width}px`);
    const controls = inspector.querySelectorAll('button, select, [tabindex="0"]');
    const first = controls[0], last = controls[controls.length - 1];
    for (const shiftKey of [false, true]) {
      const start = shiftKey ? first : last;
      start.focus();
      let prevented = false;
      inspector.listeners.keydown({ key: 'Tab', shiftKey, preventDefault() { prevented = true; } });
      assert.equal(prevented, overlay, `Tab interception at ${width}px`);
      assert.equal(document.activeElement, overlay ? (shiftKey ? last : first) : start);
    }
    const selectControl = panel.querySelector('select');
    selectControl.focus();
    inspector.listeners.keydown({ key: 'Tab', shiftKey: false, preventDefault() { assert.fail('Interior Tab must not be intercepted'); } });
    ui.closeEvidence();
    assert.equal(document.activeElement, supporting);
  }
  // Resize the same open inspector: semantics, focus and keyboard behavior must track CSS.
  function assertLayout(width) {
    const overlay = width <= overlayWidth;
    assert.equal(inspector.hidden, false);
    assert.equal(inspector.attrs.role, overlay ? 'dialog' : 'complementary', `open resize role at ${width}px`);
    assert.equal(inspector.attrs['aria-modal'], String(overlay), `open resize aria-modal at ${width}px`);
    const controls = inspector.querySelectorAll('button, select, [tabindex="0"]');
    const first = controls[0], last = controls[controls.length - 1];
    for (const shiftKey of [false, true]) {
      const start = shiftKey ? first : last;
      start.focus();
      let prevented = false;
      inspector.listeners.keydown({ key: 'Tab', shiftKey, preventDefault() { prevented = true; } });
      assert.equal(prevented, overlay, `open resize Tab interception at ${width}px`);
      assert.equal(document.activeElement, overlay ? (shiftKey ? last : first) : start);
    }
  }
  resizeViewport(1200);
  supporting.focus(); supporting.listeners.click();
  const resizeSelect = panel.querySelector('select');
  resizeSelect.value = '24'; resizeSelect.listeners.change();
  supporting.focus(); // Desktop allows focus elsewhere; entering the overlay must bring it inside.
  resizeViewport(901);
  assert.equal(document.activeElement, supporting);
  resizeViewport(900);
  assert.ok(inspector.contains(document.activeElement));
  assertLayout(900);
  resizeSelect.focus();
  resizeViewport(850);
  assert.equal(document.activeElement, resizeSelect);
  assertLayout(850);
  resizeSelect.focus();
  resizeViewport(901);
  assert.equal(document.activeElement, resizeSelect);
  assertLayout(901);
  assert.equal(panel.querySelector('select'), resizeSelect);
  assert.equal(ui.state.selectedEvidence.id, 'm24');
  ui.closeEvidence();
  assert.equal(document.activeElement, supporting);

  // Mobile-open then desktop, and entering mobile with focus already inside.
  resizeViewport(850);
  supporting.focus(); supporting.listeners.click();
  const mobileSelect = panel.querySelector('select');
  resizeViewport(1200);
  assert.equal(document.activeElement, mobileSelect);
  assertLayout(1200);
  mobileSelect.focus();
  resizeViewport(850);
  assert.equal(document.activeElement, mobileSelect);
  assertLayout(850);
  ui.closeEvidence();
  assert.equal(document.activeElement, supporting);
  resizeViewport(1200);
  resizeViewport(850);
  assert.equal(inspector.hidden, true);
  assert.equal(document.activeElement, supporting, 'Closed inspector must not steal focus on resize');

  resizeViewport(1200);
  supporting.focus(); ui.openMemories({ evidence: [] });
  supporting.focus();
  resizeViewport(850);
  assert.equal(document.activeElement, close, 'Empty overlay focuses its close button');
  assertLayout(850);
  ui.closeEvidence();
  assert.equal(document.activeElement, supporting);
  // POSIX shell parses each generated path as one literal argument, including embedded quotes.
  for (const projectPath of ['/tmp/example/project', "/tmp/a b/O'Brien;$HOME`echo wrong`$(echo wrong)"]) {
    ui.state.project.path = projectPath;
    const quoted = ui.shellQuote(projectPath);
    assert.ok(quoted.startsWith("'") && quoted.endsWith("'"));
    assert.equal(execFileSync('/bin/sh', ['-c', `printf '%s' ${quoted}`], { encoding: 'utf8' }), projectPath);
    const integration = ui.integrationSettings();
    const command = integration.querySelector('pre').textContent;
    assert.equal(command, `logchat local integration --project ${quoted}`);
    assert.ok(integration.textContent.includes('browser cannot verify'));
    assert.ok(integration.textContent.includes('first run logchat local attach'));
    assert.ok(integration.textContent.includes('intended instance state directory'));
    assert.ok(integration.textContent.includes('Integration does not attach'));
    await find(integration, 'button', 'Copy command').listeners.click();
    assert.equal(clipboard, command);
    assert.equal(execFileSync('/bin/sh', ['-c', `logchat() { printf '%s' "$4"; }; ${command}`], { encoding: 'utf8' }), projectPath);
    const processCommands = ui.commandPanel('process', 3000).querySelectorAll('pre');
    for (const node of processCommands) assert.ok(node.textContent.includes(`--project ${quoted}`));
    assert.ok(ui.commandPanel('push').textContent.includes('Requires an existing local binding'));
  }
  console.log('Native UI rendering, all-memory selection, copy, focus, empty and opt-in contracts passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
