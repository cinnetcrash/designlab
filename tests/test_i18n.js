/* Interface language checks. No server, no network.
 *
 * The failure mode this guards against is a half-translated interface: a key
 * added to one dictionary and forgotten in the other, a key used in the code
 * that exists in neither, or markup that never gets translated because it was
 * written without a data-i18n attribute.
 *
 *   node tests/test_i18n.js
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(root, p), 'utf8');

const html = read('frontend/index.html');
const appSrc = read('frontend/js/app.js');
const vizSrc = read('frontend/js/viz.js');
const i18nSrc = read('frontend/js/i18n.js');

/* i18n.js must work without a DOM: it is loaded before anything else. */
const sandbox = { console, localStorage: { getItem: () => null, setItem: () => {} } };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
const I18N = vm.runInContext(i18nSrc + '\n;I18N;', sandbox, { filename: 'i18n.js' });

let failed = 0;
function check(name, fn) {
  try {
    const note = fn();
    console.log('  ok   ' + name + (note ? ' — ' + note : ''));
  } catch (e) {
    console.log('  FAIL ' + name + ' → ' + e.message);
    failed++;
  }
}
function assert(cond, msg) { if (!cond) throw new Error(msg); }

check('both dictionaries hold exactly the same keys', () => {
  const tr = new Set(I18N.keys('tr'));
  const en = new Set(I18N.keys('en'));
  const missingEn = [...tr].filter(k => !en.has(k));
  const missingTr = [...en].filter(k => !tr.has(k));
  assert(!missingEn.length, 'missing from en: ' + missingEn.join(', '));
  assert(!missingTr.length, 'missing from tr: ' + missingTr.join(', '));
  return `${tr.size} keys`;
});

check('no translation is left empty', () => {
  for (const lang of I18N.supported) {
    for (const key of I18N.keys(lang)) {
      const value = I18N._strings[lang][key];
      assert(typeof value === 'string' && value.trim().length > 0,
        `${lang}/${key} is empty`);
    }
  }
});

/* Keys reach t() three ways: a literal, a ternary inside the call, and a
   prefix concatenated with a variable (t('stage.' + stage)). The first two are
   plain quoted keys anywhere in the source; the third is checked separately, so
   a trailing-dot prefix is not treated as a key of its own. */
function keysUsedIn(src) {
  const out = new Set();
  for (const m of src.matchAll(/'([a-zA-Z][a-zA-Z0-9]*\.[a-zA-Z0-9._]+)'/g)) {
    if (!m[1].endsWith('.')) out.add(m[1]);
  }
  return out;
}

check('every key used in the code exists in both dictionaries', () => {
  const used = new Set();
  for (const src of [appSrc, vizSrc]) {
    for (const k of keysUsedIn(src)) used.add(k);
  }
  for (const m of html.matchAll(/data-i18n(?:-ph|-title)?="([^"]+)"/g)) used.add(m[1]);
  // Quoted dotted strings that are not translation keys at all.
  for (const k of [...used]) {
    if (!I18N.keys('tr').includes(k) && !I18N.keys('en').includes(k)
        && !/^(app|nav|run|stage|s1|s2|s4|tbl|m|h|v)\./.test(k)) used.delete(k);
  }
  assert(used.size > 50, `only ${used.size} keys found — the scan probably broke`);

  const missing = [];
  for (const key of used) {
    for (const lang of I18N.supported) {
      if (!I18N.keys(lang).includes(key)) missing.push(`${lang}:${key}`);
    }
  }
  assert(!missing.length, 'used but not defined: ' + missing.join(', '));
  return `${used.size} keys used`;
});

check('composed keys are covered too', () => {
  // app.js builds these at runtime: t('stage.' + stage)
  const stages = (appSrc.match(/const PIPELINE_STAGES = \[([^\]]+)\]/) || [])[1] || '';
  const names = [...stages.matchAll(/'([a-z0-9]+)'/g)].map(m => m[1]);
  assert(names.length >= 5, 'PIPELINE_STAGES not found in app.js');
  for (const stage of names) {
    for (const lang of I18N.supported) {
      assert(I18N.keys(lang).includes('stage.' + stage),
        `${lang} has no stage.${stage}`);
    }
  }
  return names.length + ' stages';
});

check('no dictionary key is defined but never used', () => {
  const used = new Set();
  for (const src of [appSrc, vizSrc]) for (const k of keysUsedIn(src)) used.add(k);
  for (const m of html.matchAll(/data-i18n(?:-ph|-title)?="([^"]+)"/g)) used.add(m[1]);
  const dead = I18N.keys('tr').filter(k =>
    !used.has(k) && !k.startsWith('stage.'));   // stage.* is built at runtime
  assert(!dead.length, 'defined but unused: ' + dead.join(', '));
});

check('no Turkish text is left hard-coded in the code or markup', () => {
  const turkish = /[çğıöşüÇĞİÖŞÜ]/;
  const offenders = [];
  for (const [name, src] of [['app.js', appSrc], ['viz.js', vizSrc]]) {
    src.split('\n').forEach((line, i) => {
      if (turkish.test(line)) offenders.push(`${name}:${i + 1}`);
    });
  }
  html.split('\n').forEach((line, i) => {
    // index.html keeps Turkish as the inline default of a translated element
    if (turkish.test(line) && !line.includes('data-i18n')) {
      offenders.push(`index.html:${i + 1}`);
    }
  });
  assert(!offenders.length, 'untranslatable text at ' + offenders.join(', '));
});

check('t() substitutes placeholders and flags unknown keys', () => {
  I18N.setLang('en');
  assert(I18N.t('run.designing', { n: 7 }).includes('7'), 'placeholder not filled');
  assert(!I18N.t('run.designing', { n: 7 }).includes('{n}'), 'placeholder left in place');
  assert(I18N.t('no.such.key') === '!no.such.key',
    'an unknown key must be visible, not blank');
});

check('switching language actually changes the text', () => {
  I18N.setLang('tr');
  const tr = I18N.t('s2.designBtn');
  I18N.setLang('en');
  const en = I18N.t('s2.designBtn');
  assert(tr !== en, 'the two languages returned the same string');
  assert(I18N.setLang('de') === 'en', 'an unsupported language must be ignored');
  return `"${tr}" / "${en}"`;
});

check('every element that shows text carries a translation marker', () => {
  // Headings and buttons inside the panels are the ones users read.
  const untagged = [];
  for (const m of html.matchAll(/<(h2|h3|button)\b([^>]*)>([^<]{2,})</g)) {
    const [, tag, attrs, text] = m;
    if (/data-i18n/.test(attrs)) continue;
    if (!/[A-Za-zÇĞİÖŞÜçğıöşü]{3,}/.test(text)) continue;   // icons, digits
    if (text.trim() === 'Primer Designer') continue;         // product name
    untagged.push(`${tag}:"${text.trim().slice(0, 30)}"`);
  }
  assert(!untagged.length, 'not translated: ' + untagged.join(', '));
});

console.log(failed ? `\n${failed} check(s) failed` : '\nall i18n checks passed');
process.exit(failed ? 1 : 0);
