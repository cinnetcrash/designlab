/* Integration check: run the real frontend code against a running server.
 *
 * Loads frontend/js/viz.js and frontend/js/app.js into a stubbed DOM built from
 * the actual index.html (ids, input types and default values are read from the
 * file, not hand-copied), then drives the same functions the buttons call:
 * startSearch → renderHits → startDesign → renderResult.
 *
 * This is what the browser would do. It catches wiring bugs a backend test
 * cannot see — a request field the frontend never sends, a result key the
 * renderers read under a different name, a step that never becomes visible.
 *
 *   ./run.sh &                        # server must be up
 *   node tests/test_frontend_flow.js  # add --blast to exercise the NCBI BLAST route
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const BASE = process.env.PD_URL || 'http://127.0.0.1:8090';
const USE_BLAST = process.argv.includes('--blast');

/* ─── build a DOM stub from the real index.html ───────────────────────── */

const html = fs.readFileSync(path.join(root, 'frontend', 'index.html'), 'utf8');

function parseElements() {
  const elements = new Map();
  const tagRe = /<([a-z][a-z0-9]*)\b([^>]*\bid="[^"]+"[^>]*)>/gi;
  let m;
  while ((m = tagRe.exec(html))) {
    const [, tag, attrs] = m;
    const id = (attrs.match(/\bid="([^"]+)"/) || [])[1];
    if (!id) continue;
    const el = makeEl(tag.toLowerCase(), id);
    el.type = (attrs.match(/\btype="([^"]+)"/) || [])[1] || 'text';
    el.value = (attrs.match(/\bvalue="([^"]+)"/) || [])[1] || '';
    el.checked = /\bchecked\b/.test(attrs);
    el._classes = new Set(((attrs.match(/\bclass="([^"]+)"/) || [])[1] || '').split(/\s+/).filter(Boolean));
    elements.set(id, el);
  }
  // <select> defaults come from the option marked selected.
  for (const [id, el] of elements) {
    if (el.tagName !== 'select') continue;
    const block = html.slice(html.indexOf(`id="${id}"`));
    const sel = block.match(/<option value="([^"]+)"[^>]*selected/);
    const first = block.match(/<option value="([^"]+)"/);
    el.value = (sel || first || [])[1] || '';
  }
  return elements;
}

function makeEl(tag = 'div', id = '') {
  const el = {
    tagName: tag, id, value: '', checked: false, disabled: false,
    innerHTML: '', textContent: '', title: '', href: '', className: '',
    style: {}, dataset: {}, _classes: new Set(), _handlers: {}, _kids: {},
    clientWidth: 1200, clientHeight: 400, width: 1200, height: 400,
    scrollTop: 0, scrollHeight: 0,
    getContext: () => fakeCtx,
    addEventListener(type, fn) { this._handlers[type] = fn; },
    dispatchEvent(ev) { const h = this._handlers[ev.type]; if (h) h.call(this, ev); },
    scrollIntoView() {},
    appendChild() {},
    querySelector(sel) {
      const found = queryAll(sel, this)[0];
      if (found) return found;
      // Structural children (tbody, …) must persist: renderHits writes its rows
      // into `table.querySelector('tbody')`, and a throwaway object would drop
      // them on the floor.
      if (!this._kids[sel]) this._kids[sel] = makeEl('stub');
      return this._kids[sel];
    },
    querySelectorAll(sel) { return queryAll(sel, this); },
  };
  el.classList = {
    add: (c) => el._classes.add(c),
    remove: (c) => el._classes.delete(c),
    contains: (c) => el._classes.has(c),
    toggle: (c, force) => {
      const on = force === undefined ? !el._classes.has(c) : force;
      on ? el._classes.add(c) : el._classes.delete(c);
      return on;
    },
  };
  return el;
}

/* Elements the app creates at runtime by writing innerHTML. The stub returns
   objects for the selectors app.js queries, driven by the markup it wrote. */
function queryAll(selector, scope) {
  let source = (scope && scope.innerHTML) || '';
  for (const kid of Object.values((scope && scope._kids) || {})) {
    source += kid.innerHTML || '';
  }
  const collect = (re, make) => {
    const out = []; let m;
    while ((m = re.exec(source))) out.push(make(m));
    return out;
  };
  switch (selector) {
    case '.hit-check':
      return collect(/data-idx="(\d+)"/g, (m) => {
        const el = makeEl('input');
        el.type = 'checkbox';
        el.dataset = { idx: m[1] };
        el.checked = true;
        return el;
      });
    case '.pair-card':
      return collect(/class="pair-card[^"]*" data-i="(\d+)"/g, (m) => {
        const el = makeEl('div'); el.dataset = { i: m[1] }; return el;
      });
    case '[data-open]':
      return collect(/data-open="([^"]+)"/g, (m) => {
        const el = makeEl('button'); el.dataset = { open: m[1] }; return el;
      });
    case '[data-detail]':
      return collect(/data-detail="([^"]+)"/g, (m) => {
        const el = makeEl('button'); el.dataset = { detail: m[1] }; return el;
      });
    case 'option[value=probe]':
      return [makeEl('option')];
    case '#inputType .seg':
      return ['sequence', 'query'].map(v => {
        const el = makeEl('button'); el.dataset = { value: v }; return el;
      });
    case '.entrez-only':
    case '.step':
      return [makeEl('div'), makeEl('div')];
    default:
      return [];
  }
}

const ctxCalls = [];
const fakeCtx = new Proxy({}, {
  get(_t, p) {
    if (p === 'setTransform' || p === 'clearRect') return () => {};
    if (p === 'measureText') return () => ({ width: 10 });
    return (...a) => { ctxCalls.push(String(p)); };
  },
  set() { return true; },
});

const elements = parseElements();
const alerts = [];

// app.js uses relative URLs, which only resolve inside a browser page.
const fetchAbsolute = (url, opts) =>
  fetch(String(url).startsWith('http') ? url : BASE + url, opts);

const sandbox = {
  console, fetch: fetchAbsolute, setTimeout, clearTimeout, setInterval: () => 0,
  URLSearchParams, Event: class { constructor(t) { this.type = t; } },
  alert: (msg) => alerts.push(msg),
  localStorage: { getItem: () => null, setItem: () => {} },
  window: {
    devicePixelRatio: 1,
    scrollTo: () => {},
    addEventListener: () => {},
  },
  document: {
    documentElement: makeEl('html'),
    getElementById: (id) => elements.get(id) || makeEl('div', id),
    // Document-wide queries must see markup any element wrote, the way the
    // browser does — app.js selects .hit-check and .pair-card from `document`.
    querySelectorAll: (sel) => {
      const all = [];
      for (const el of elements.values()) all.push(...queryAll(sel, el));
      return all.length ? all : queryAll(sel, { innerHTML: '' });
    },
    addEventListener: () => {},
    body: makeEl('body'),
  },
  getComputedStyle: () => ({ getPropertyValue: () => '#123456' }),
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

vm.runInContext(fs.readFileSync(path.join(root, 'frontend/js/viz.js'), 'utf8')
  + '\n;globalThis.VIZ = VIZ;', sandbox, { filename: 'viz.js' });
vm.runInContext(fs.readFileSync(path.join(root, 'frontend/js/app.js'), 'utf8')
  + '\n;globalThis.__app = {startSearch, startDesign, renderHits, renderResult,'
  + ' updateQueryPreview, pollActive, state, showStep, loadHistory, openPastRun,'
  + ' EXAMPLE};',
  sandbox, { filename: 'app.js' });

const app = sandbox.__app;
const $ = (id) => elements.get(id) || makeEl();

/* ─── checks ──────────────────────────────────────────────────────────── */

let failed = 0;
const results = [];
function ok(name, extra = '') { results.push(['ok', name, extra]); }
function bad(name, why) { results.push(['FAIL', name, why]); failed++; }
function assert(cond, msg) { if (!cond) throw new Error(msg); }

async function step(name, fn) {
  try {
    const extra = await fn();
    ok(name, extra || '');
  } catch (e) {
    bad(name, e.message);
  }
  const [kind, n, extra] = results[results.length - 1];
  console.log(`  ${kind === 'ok' ? 'ok  ' : 'FAIL'} ${n}${extra ? ' — ' + extra : ''}`);
}

(async () => {
  console.log(`frontend flow against ${BASE} (${USE_BLAST ? 'BLAST' : 'Entrez'} route)\n`);

  await step('server reachable and tools present', async () => {
    const h = await (await fetchAbsolute('/api/health')).json();
    assert(h.status === 'ok', `health says ${h.status}: missing ${h.missing_tools}`);
    return Object.keys(h.versions).length + ' tools';
  });

  await step('query preview renders from plain words', async () => {
    $('geneInput').value = 'invA';
    $('organismInput').value = 'Salmonella enterica';
    $('minLength').value = '1500';
    $('maxLength').value = '5000';
    $('rawToggle').checked = false;
    await app.updateQueryPreview();
    const q = $('queryPreview').textContent;
    assert(q.includes('"invA"[Gene]'), `unexpected preview: ${q}`);
    assert(q.includes('"Salmonella enterica"[Organism]'), q);
    assert(q.includes('1500:5000[SLEN]'), q);
    return q.slice(0, 58) + '…';
  });

  await step('search runs and hits are rendered', async () => {
    app.state.inputType = USE_BLAST ? 'sequence' : 'query';
    if (USE_BLAST) $('seqInput').value = app.EXAMPLE;
    $('maxHits').value = '10';
    await app.startSearch();
    assert(!alerts.length, 'alert raised: ' + alerts.join('; '));
    const r = app.state.searchResult;
    assert(r && r.n_hits > 0, 'no hits returned');
    const body = $('hitTable').querySelector('tbody').innerHTML
      || $('hitTable').innerHTML;
    assert($('hitSummary').innerHTML.length > 20, 'hit summary not filled');
    return `${r.n_hits} hits, step2 visible: ${!$('step2')._classes.has('hidden')}`;
  });

  await step('the built query is shown with the results', async () => {
    const summary = $('hitSummary').innerHTML;
    if (USE_BLAST) return 'BLAST route — no Entrez query expected';
    assert(summary.includes('[Gene]'), 'query not shown in the summary');
    return 'query displayed';
  });

  await step('design runs from the selected records', async () => {
    $('mode').value = 'standard';
    $('geneLabel').value = 'flow_test';
    $('numReturn').value = '3';
    $('productMin').value = '100';
    $('productMax').value = '800';
    $('specificityCheck').checked = true;
    $('trimHomology').checked = true;
    $('includeQuery').checked = USE_BLAST;
    await app.startDesign();
    assert(!alerts.length, 'alert raised: ' + alerts.join('; '));
    const res = app.state.result;
    assert(res, 'no result — run log: ' + $('runLog').textContent.slice(-300));
    assert(res.pairs.length > 0, 'design produced no primer pairs');
    return `${res.pairs.length} pairs, ${res.blocks.length} blocks, `
         + `${res.conserved_bp} bp conserved`;
  });

  await step('live status named the running programs', async () => {
    const log = $('runLog').textContent;
    const steps = $('pipelineSteps').innerHTML;
    assert(steps.includes('Primer3'), 'pipeline step list not rendered');
    assert(log.includes('MAFFT'), 'MAFFT never appeared in the run log');
    assert($('nowTool').textContent === 'bitti',
      `run indicator stuck on "${$('nowTool').textContent}"`);
    return 'indicator settled, stages rendered';
  });

  await step('result view is populated', async () => {
    assert(!$('step4')._classes.has('hidden'), 'step 4 not shown');
    assert($('stats').innerHTML.includes('stat'), 'summary tiles empty');
    assert($('pairList').innerHTML.includes('pair-card'), 'pair cards empty');
    assert($('ampliconViz').innerHTML.includes('<svg'), 'amplicon svg missing');
    assert($('bindingDetail').innerHTML.includes('bind-row'), 'binding panel empty');
    assert($('coverageHeatmap').innerHTML.includes('heat-cell'), 'heatmap empty');
    assert($('structureViz').innerHTML.includes('struct'), 'structures empty');
    assert($('metricsTable').innerHTML.includes('<tbody>'), 'metrics table empty');
    assert($('recordBox').innerHTML.includes('data-table'), 'record table empty');
    assert($('methodsBox').textContent.includes('MAFFT'), 'methods block empty');
    const bad = ['undefined', 'NaN', '[object Object]'].filter(
      t => $('metricsTable').innerHTML.includes(t) || $('pairList').innerHTML.includes(t));
    assert(!bad.length, 'leaked into the markup: ' + bad.join(', '));
    return 'all panels filled, no undefined/NaN';
  });

  await step('downloads point at the finished job', async () => {
    const href = $('dlPrimers').href;
    assert(href.includes(app.state.designJob), `bad href: ${href}`);
    const res = await fetchAbsolute(href);
    assert(res.ok, `primers.tsv → HTTP ${res.status}`);
    const text = await res.text();
    assert(text.split('\n').length > 2, 'primers.tsv looks empty');
    return `${text.split('\n').length - 2} oligo rows`;
  });

  await step('history lists the run and reopens it', async () => {
    await app.loadHistory();
    const rows = $('historyTable').querySelector('tbody').innerHTML
      || $('historyTable').innerHTML;
    assert($('historyStats').innerHTML.includes('stat'), 'history stats empty');
    const jobId = app.state.designJob;
    app.state.result = null;
    await app.openPastRun(jobId);
    assert(app.state.result, 'reopening a past run produced nothing');
    assert(app.state.result.pairs.length > 0, 'reopened run has no pairs');
    return `reopened ${jobId} with ${app.state.result.pairs.length} pairs`;
  });

  await step('active-job endpoint is quiet when idle', async () => {
    await app.pollActive();
    const a = await (await fetchAbsolute('/api/jobs/active')).json();
    assert(a.count === 0, `${a.count} job(s) still running: ${JSON.stringify(a.tools)}`);
    assert($('activeBadge')._classes.has('hidden'), 'badge left visible while idle');
    return 'badge hidden';
  });

  console.log(failed ? `\n${failed} check(s) failed` : '\nfrontend flow ok');
  process.exit(failed ? 1 : 0);
})();
