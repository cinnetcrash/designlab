/* Headless check of frontend/js/viz.js against a real job result.
 *
 * Runs the renderers with a stubbed DOM, so the field names the visualisation
 * expects are checked against what the backend actually produced. Without this
 * a renamed key only shows up as a blank panel in the browser.
 *
 *   node tests/test_viz_headless.js [path/to/results.json]
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');

function newestResult() {
  const jobsDir = path.join(root, 'data', 'jobs');
  const candidates = fs.readdirSync(jobsDir)
    .map(d => path.join(jobsDir, d, 'results.json'))
    .filter(fs.existsSync)
    .map(p => ({ p, m: fs.statSync(p).mtimeMs }))
    .sort((a, b) => b.m - a.m);
  if (!candidates.length) throw new Error('no results.json under data/jobs — run a design first');
  return candidates[0].p;
}

const resultPath = process.argv[2] || newestResult();
const result = JSON.parse(fs.readFileSync(resultPath, 'utf8'));
console.log('using', path.relative(root, resultPath));

/* ─── minimal DOM ─────────────────────────────────────────────────────── */

const ctxCalls = [];
const fakeCtx = new Proxy({}, {
  get(_t, prop) {
    if (prop === 'setTransform' || prop === 'clearRect') return () => {};
    if (prop === 'measureText') return () => ({ width: 10 });
    return (...args) => { ctxCalls.push({ op: String(prop), args }); };
  },
  set() { return true; },
});

function makeEl(tag = 'div') {
  return {
    tagName: tag, innerHTML: '', style: {}, clientWidth: 1200, clientHeight: 400,
    width: 1200, height: 400, classList: { add() {}, remove() {}, toggle() {} },
    getContext: () => fakeCtx,
    querySelector: () => makeEl(),
    querySelectorAll: () => [],
    addEventListener() {}, appendChild() {},
  };
}

const sandbox = {
  console,
  window: { devicePixelRatio: 1 },
  document: { body: {}, createElement: makeEl },
  getComputedStyle: () => ({
    getPropertyValue: (name) => ({
      '--text': '#111', '--text-dim': '#666', '--border': '#ddd',
      '--surface': '#fff', '--surface-2': '#eee', '--conserved': '#2f9e6f',
      '--variable': '#e6a23c', '--match': '#ccc', '--gap': '#f5f5f5',
      '--danger': '#c0392b', '--forward': '#1a7f5a', '--reverse': '#b03a2e',
      '--probe': '#7d3fb5', '--accent': '#1f6feb',
    }[name] || '#000'),
  }),
};
sandbox.globalThis = sandbox;
sandbox.localStorage = { getItem: () => null, setItem: () => {} };
vm.createContext(sandbox);
// viz.js calls t(), so i18n.js has to be in the context first, exactly as the
// page loads it. Both define their export with `const`, which is a lexical
// binding and never lands on the sandbox global — hence the explicit assignment.
vm.runInContext(fs.readFileSync(path.join(root, 'frontend', 'js', 'i18n.js'), 'utf8')
  + '\n;globalThis.I18N = I18N; globalThis.t = t;', sandbox, { filename: 'i18n.js' });
const vizSource = fs.readFileSync(path.join(root, 'frontend', 'js', 'viz.js'), 'utf8');
const VIZ = vm.runInContext(vizSource + '\n;VIZ;', sandbox, { filename: 'viz.js' });

/* ─── checks ──────────────────────────────────────────────────────────── */

let failures = 0;
function check(name, fn) {
  try {
    fn();
    console.log('  ok   ' + name);
  } catch (e) {
    failures++;
    console.log('  FAIL ' + name + ' → ' + e.message);
  }
}
function assert(cond, msg) { if (!cond) throw new Error(msg); }

const pair = result.pairs[0];

check('drawAlignment runs and returns a column mapper', () => {
  const canvas = makeEl('canvas');
  const map = VIZ.drawAlignment(canvas, {
    result, view: { start: 0, end: result.alignment.length }, pair, hover: null,
  });
  assert(typeof map.colAt === 'function', 'no colAt returned');
  assert(map.colAt(0) === 0, 'colAt(0) should be column 0');
  assert(ctxCalls.some(c => c.op === 'fillRect'), 'nothing was drawn');
});

check('drawAmplicon produces an SVG with both primers', () => {
  const el = makeEl();
  VIZ.drawAmplicon(el, result, pair);
  assert(el.innerHTML.includes('<svg'), 'no svg emitted');
  assert(el.innerHTML.includes('amplikon'), 'amplicon label missing');
  assert(el.innerHTML.includes(pair.forward.sequence), 'forward sequence missing');
  assert(el.innerHTML.includes(pair.reverse.sequence), 'reverse sequence missing');
  assert(!/undefined|NaN/.test(el.innerHTML), 'undefined/NaN leaked into the SVG');
});

check('renderBinding lists every sequence', () => {
  const el = makeEl();
  VIZ.renderBinding(el, pair.binding.forward, false);
  const n = pair.binding.forward.per_sequence.length;
  const shown = (el.innerHTML.match(/class="bind-row"/g) || []).length;
  assert(shown === n, `expected ${n} rows, got ${shown}`);
  assert(!/undefined/.test(el.innerHTML), 'undefined leaked into binding rows');
});

check('renderBinding mismatch highlighting lines up with the data', () => {
  const el = makeEl();
  VIZ.renderBinding(el, pair.binding.forward, false);
  const totalMismatch = pair.binding.forward.per_sequence
    .reduce((s, x) => s + x.n_mismatch, 0);
  const marked = (el.innerHTML.match(/class="mm /g) || []).length;
  assert(marked === totalMismatch,
    `data says ${totalMismatch} mismatches, markup highlights ${marked}`);
});

check('renderHeatmap emits one cell per sequence per oligo', () => {
  const el = makeEl();
  VIZ.renderHeatmap(el, pair);
  const roles = pair.binding.probe ? 3 : 2;
  const cells = (el.innerHTML.match(/heat-cell/g) || []).length;
  const expected = roles * pair.binding.forward.per_sequence.length;
  assert(cells === expected, `expected ${expected} cells, got ${cells}`);
});

check('renderStructures draws duplex diagrams with aligned rows', () => {
  const el = makeEl();
  VIZ.renderStructures(el, pair);
  assert(el.innerHTML.includes('self-dimer'), 'self-dimer card missing');
  assert(el.innerHTML.includes('hairpin'), 'hairpin card missing');
  assert(!/undefined/.test(el.innerHTML), 'undefined leaked into structures');
});

check('duplex diagram rows are the same width', () => {
  const g = pair.geometry;
  [g.forward_self_dimer, g.reverse_self_dimer, g.cross_dimer]
    .filter(Boolean)
    .forEach(d => assert(d.top.length === d.match.length &&
                         d.match.length === d.bottom.length,
                         'duplex rows have different lengths'));
});

check('every pair renders without throwing', () => {
  result.pairs.forEach(p => {
    const el = makeEl();
    VIZ.drawAmplicon(el, result, p);
    VIZ.renderHeatmap(makeEl(), p);
    VIZ.renderStructures(makeEl(), p);
    ['forward', 'reverse', 'probe'].forEach(role => {
      if (p.binding[role]) VIZ.renderBinding(makeEl(), p.binding[role], false);
    });
  });
});

console.log(failures ? `\n${failures} check(s) failed` : '\nall viz checks passed');
process.exit(failures ? 1 : 0);
