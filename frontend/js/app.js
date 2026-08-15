/* Primer Designer — application flow */

const $ = (id) => document.getElementById(id);
const esc = VIZ.esc;

const state = {
  inputType: 'sequence',
  searchJob: null,
  searchResult: null,
  designJob: null,
  result: null,
  selectedPair: 0,
  view: { start: 0, end: 1 },
  drag: null,
  hover: null,
  logSeen: 0,
};

/* ─── boot ──────────────────────────────────────────────────────────── */

document.addEventListener('DOMContentLoaded', () => {
  wireUp();
  health();
  updateQueryPreview();
  pollActive();
  setInterval(pollActive, 4000);
  const saved = localStorage.getItem('pd-theme');
  if (saved) document.documentElement.setAttribute('data-theme', saved);
});

async function health() {
  try {
    const h = await api('/api/health');
    const el = $('health');
    if (h.status === 'ok') {
      el.className = 'pill pill-ok';
      el.textContent = 'araçlar hazır';
    } else {
      el.className = 'pill pill-bad';
      el.textContent = 'eksik: ' + h.missing_tools.join(', ');
    }
    el.title = Object.entries(h.versions).map(([k, v]) => `${k}: ${v}`).join('\n');
  } catch {
    $('health').className = 'pill pill-bad';
    $('health').textContent = 'backend yanıt vermiyor';
  }
}

function wireUp() {
  $('themeToggle').onclick = () => {
    const root = document.documentElement;
    const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    localStorage.setItem('pd-theme', next);
    if (state.result) redrawAll();
  };

  document.querySelectorAll('#inputType .seg').forEach(btn => {
    btn.onclick = () => {
      document.querySelectorAll('#inputType .seg').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      state.inputType = btn.dataset.value;
      const isSeq = state.inputType === 'sequence';
      $('seqInputWrap').classList.toggle('hidden', !isSeq);
      $('queryInputWrap').classList.toggle('hidden', isSeq);
      document.querySelectorAll('.entrez-only').forEach(e => e.classList.toggle('hidden', isSeq));
    };
  });

  $('seqInput').addEventListener('input', () => {
    const bp = cleanSeq($('seqInput').value).length;
    $('seqStats').textContent = bp.toLocaleString() + ' bp';
  });

  // Live preview of the query the backend will build from the plain words.
  let previewTimer = null;
  const schedulePreview = () => {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(updateQueryPreview, 250);
  };
  ['geneInput', 'organismInput', 'queryInput', 'minLength', 'maxLength']
    .forEach(id => $(id).addEventListener('input', schedulePreview));
  $('rawToggle').onchange = () => {
    const raw = $('rawToggle').checked;
    $('queryInput').classList.toggle('hidden', !raw);
    $('geneInput').disabled = raw;
    updateQueryPreview();
  };

  $('loadExample').onclick = () => {
    $('seqInput').value = EXAMPLE;
    $('seqInput').dispatchEvent(new Event('input'));
    $('geneLabel').value = 'invA';
  };

  $('searchBtn').onclick = startSearch;
  $('designBtn').onclick = startDesign;
  $('backTo1').onclick = () => showStep(1);
  $('backAfterError').onclick = () => showStep(state.designJob ? 2 : 1);
  $('newRun').onclick = () => { showStep(1); };
  $('selectTopN').onclick = () => selectTop(parseInt($('topN').value, 10) || 10);
  $('selectAll').onclick = () => setAllChecks(true);
  $('selectNone').onclick = () => setAllChecks(false);
  $('mode').onchange = () => {
    const q = $('mode').value === 'qpcr';
    $('bindingRole').querySelector('option[value=probe]').disabled = !q;
    $('probeSettings').classList.toggle('hidden', !q);
  };
  $('bindingRole').onchange = renderBindingPanel;
  $('onlyMismatch').onchange = renderBindingPanel;
  $('zoomReset').onclick = () => {
    state.view = { start: 0, end: state.result.alignment.length };
    redrawAll();
  };

  $('historyBtn').onclick = openHistory;
  $('historyClose').onclick = () => { $('history').classList.add('hidden'); showStep(state.result ? 4 : 1); };
  $('historyRefresh').onclick = loadHistory;
  $('historyStatus').onchange = loadHistory;
  $('historyRebuild').onclick = async () => {
    const r = await api('/api/history/rebuild', {});
    await loadHistory();
    alert(`${r.imported} çalıştırma diskten yeniden indekslendi.`);
  };
  let searchTimer = null;
  $('historySearch').addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadHistory, 300);
  });

  setupCanvas();
  window.addEventListener('resize', () => { if (state.result) redrawAll(); });
}

/* ─── history ───────────────────────────────────────────────────────── */

function openHistory() {
  [1, 2, 3, 4].forEach(i => $('step' + i).classList.add('hidden'));
  $('history').classList.remove('hidden');
  window.scrollTo({ top: 0, behavior: 'smooth' });
  loadHistory();
}

async function loadHistory() {
  const q = encodeURIComponent($('historySearch').value.trim());
  const status = $('historyStatus').value;
  let data;
  try {
    data = await api(`/api/history?limit=200&q=${q}&status=${status}`);
  } catch (e) {
    $('historyTable').querySelector('tbody').innerHTML =
      `<tr><td colspan="11" class="tag-bad">${esc(e.message)}</td></tr>`;
    return;
  }

  const s = data.stats;
  $('historyStats').innerHTML = [
    ['çalıştırma', s.runs ?? 0],
    ['başarılı', s.done ?? 0],
    ['hatalı', s.failed ?? 0],
    ['primer çifti', s.pairs ?? 0],
    ['oligo', s.oligos ?? 0],
    ['farklı gen', s.genes ?? 0],
    ['veritabanı', ((s.db_bytes || 0) / 1024).toFixed(0) + ' KB'],
  ].map(([k, v]) => `<div class="stat"><div class="v">${v}</div><div class="k">${k}</div></div>`).join('');

  const tbody = $('historyTable').querySelector('tbody');
  if (!data.runs.length) {
    tbody.innerHTML = '<tr><td colspan="11">Kayıt yok.</td></tr>';
    return;
  }
  tbody.innerHTML = data.runs.map(r => {
    const ok = r.status === 'done';
    const cov = r.best_coverage == null ? '—' : '%' + r.best_coverage;
    return `<tr>
      <td class="mono">${esc((r.created || '').replace('T', ' ').slice(0, 16))}</td>
      <td>${esc(r.gene_label || '—')}</td>
      <td>${esc(r.mode || '—')}</td>
      <td class="num">${r.n_sequences ?? '—'}</td>
      <td class="num">${r.n_blocks ?? '—'}</td>
      <td class="num">${r.conserved_bp?.toLocaleString() ?? '—'}</td>
      <td class="num">${r.n_pairs ?? 0}</td>
      <td class="num">${cov}</td>
      <td class="num">${r.elapsed_s ?? '—'}</td>
      <td>${ok ? '<span class="tag-ok">tamam</span>'
               : `<span class="tag-bad" title="${esc(r.error || '')}">hata</span>`}</td>
      <td class="row gap">
        <button class="btn btn-ghost btn-sm" data-detail="${esc(r.job_id)}">Özet</button>
        ${ok ? `<button class="btn btn-ghost btn-sm" data-open="${esc(r.job_id)}">Aç</button>` : ''}
      </td></tr>`;
  }).join('');

  tbody.querySelectorAll('[data-open]').forEach(b => {
    b.onclick = () => openPastRun(b.dataset.open);
  });
  tbody.querySelectorAll('[data-detail]').forEach(b => {
    b.onclick = () => showRunDetail(b.dataset.detail);
  });
  $('historyDetail').innerHTML = '';
}

async function showRunDetail(jobId) {
  const run = await api(`/api/history/${jobId}`);
  const primers = run.primers.map(p => `<tr>
    <td class="num">${p.pair_rank}</td><td>${esc(p.role)}</td>
    <td class="mono">${esc(p.sequence)}</td>
    <td class="num">${p.length}</td><td class="num">${p.tm ?? '—'}</td>
    <td class="num">${p.gc_percent ?? '—'}</td>
    <td class="num">${p.product_size ?? '—'}</td>
    <td class="num">${p.perfect_percent == null ? '—' : '%' + p.perfect_percent}</td>
  </tr>`).join('');

  const records = run.records.map(r => `<tr>
    <td class="mono">${esc(r.accession)}</td>
    <td><span class="truncate" title="${esc(r.description || '')}">${esc(r.description || '')}</span></td>
    <td class="num">${(r.length || 0).toLocaleString()}</td>
    <td class="num">${r.coverage == null ? '—' : (100 * r.coverage).toFixed(1)}</td>
    <td>${r.used_in_conservation ? '<span class="tag-ok">evet</span>' : '<span class="tag-warn">hayır</span>'}</td>
  </tr>`).join('');

  $('historyDetail').innerHTML = `<div class="card">
    <div class="row between wrap">
      <h3>${esc(run.job_id)} — ${esc(run.gene_label || '')}</h3>
      <span class="hint mono">${esc(run.workdir || '')}</span>
    </div>
    ${run.error ? `<div class="error">${esc(run.error)}</div>` : ''}
    ${primers ? `<h4 class="sub">Oligolar</h4><div class="table-wrap"><table class="data-table">
      <thead><tr><th class="num">Çift</th><th>Rol</th><th>Dizi 5'→3'</th>
        <th class="num">nt</th><th class="num">Tm</th><th class="num">GC %</th>
        <th class="num">Ürün bp</th><th class="num">Tam eşleşme</th></tr></thead>
      <tbody>${primers}</tbody></table></div>` : ''}
    ${records ? `<h4 class="sub">Kullanılan kayıtlar</h4><div class="table-wrap"><table class="data-table">
      <thead><tr><th>Accession</th><th>Tanım</th><th class="num">bp</th>
        <th class="num">Kapsama %</th><th>Korunmuşluk hesabında</th></tr></thead>
      <tbody>${records}</tbody></table></div>` : ''}
    <div class="row gap mt">
      <a class="btn btn-ghost btn-sm" href="/api/job/${esc(run.job_id)}/file/primers.tsv" download>primers.tsv</a>
      <a class="btn btn-ghost btn-sm" href="/api/job/${esc(run.job_id)}/file/results.json" download>results.json</a>
      <a class="btn btn-ghost btn-sm" href="/api/job/${esc(run.job_id)}/file/aligned.fasta" download>aligned.fasta</a>
    </div>
  </div>`;
  $('historyDetail').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function openPastRun(jobId) {
  try {
    state.result = await api(`/api/job/${jobId}/result`);
  } catch (e) {
    alert('Bu çalıştırmanın tam sonucu okunamadı: ' + e.message);
    return;
  }
  state.designJob = jobId;
  $('history').classList.add('hidden');
  renderResult();
  showStep(4);
}

async function updateQueryPreview() {
  const raw = $('rawToggle').checked;
  const params = new URLSearchParams({
    gene: raw ? '' : $('geneInput').value,
    organism: $('organismInput').value,
    min_length: $('minLength').value || 200,
    max_length: $('maxLength').value || 20000,
    raw_query: raw ? $('queryInput').value : '',
  });
  try {
    const r = await api('/api/entrez-query?' + params.toString());
    $('queryPreview').textContent = r.query || (r.note || '—');
  } catch {
    $('queryPreview').textContent = '—';
  }
}

/* ─── live "what is running now" ────────────────────────────────────── */

const PIPELINE_STEPS = [
  { stage: 'search', label: 'NCBI arama' },
  { stage: 'download', label: 'İndirme' },
  { stage: 'trim', label: 'Homoloji kırpma' },
  { stage: 'align', label: 'Hizalama' },
  { stage: 'conservation', label: 'Korunmuşluk' },
  { stage: 'primer3', label: 'Primer3' },
  { stage: 'validate', label: 'Doğrulama' },
];

function renderPipelineSteps(currentStage) {
  const order = PIPELINE_STEPS.map(s => s.stage);
  const at = order.indexOf(currentStage);
  $('pipelineSteps').innerHTML = PIPELINE_STEPS.map((s, i) => {
    const cls = at < 0 ? '' : (i < at ? 'done' : (i === at ? 'active' : ''));
    return `<span class="pstep ${cls}">${esc(s.label)}</span>`;
  }).join('');
}

function showRunning(job) {
  const running = job.status === 'running' || job.status === 'queued';
  $('runSpinner').classList.toggle('hidden', !running);
  $('nowTool').textContent = running
    ? (job.tool || 'hazırlanıyor')
    : (job.status === 'done' ? 'bitti' : 'durdu');
  $('stageLabel').textContent = job.stage || '—';
  $('toolElapsed').textContent =
    job.tool_elapsed_s != null ? job.tool_elapsed_s.toFixed(0) + ' s' : '—';
  $('nowRunning').classList.toggle('idle', !running);
  renderPipelineSteps(job.stage);
}

/* Header badge: shows anything running, even while looking at the history. */
async function pollActive() {
  try {
    const a = await api('/api/jobs/active');
    const badge = $('activeBadge');
    if (!a.count) {
      badge.classList.add('hidden');
    } else {
      badge.classList.remove('hidden');
      const tools = a.tools.length ? a.tools.join(', ') : 'hazırlanıyor';
      badge.textContent = `${a.count} iş çalışıyor · ${tools}`;
      badge.title = a.jobs
        .map(j => `${j.kind} ${j.id}: ${j.stage} — ${j.tool || '—'}`).join('\n');
    }
  } catch { /* backend down; the health pill already says so */ }
}

/* ─── step 1: search ────────────────────────────────────────────────── */

async function startSearch() {
  const raw = $('rawToggle').checked;
  const body = {
    input_type: state.inputType,
    text: state.inputType === 'sequence' ? $('seqInput').value : '',
    gene: raw ? '' : $('geneInput').value,
    raw_query: raw ? $('queryInput').value : '',
    database: $('database').value,
    max_hits: int($('maxHits'), 50),
    min_identity: num($('minIdentity'), 80),
    min_coverage: num($('minCoverage'), 60),
    min_length: int($('minLength'), 200),
    max_length: int($('maxLength'), 20000),
    organism: $('organismInput').value,
  };
  const given = state.inputType === 'sequence'
    ? body.text : (body.gene || body.raw_query || body.organism);
  if (!given || given.trim().length < 3) {
    alert(state.inputType === 'sequence'
      ? 'Önce bir gen dizisi yapıştır.'
      : 'Önce bir gen adı yaz (ör. invA).');
    return;
  }

  try {
    const { job_id } = await api('/api/search', body);
    state.searchJob = job_id;
    showStep(3);
    $('runTitle').textContent = 'NCBI aranıyor…';
    const job = await pollJob(job_id);
    if (job.status === 'error') return showError(job.error);
    state.searchResult = await api(`/api/job/${job_id}/result`);
    renderHits();
    showStep(2);
  } catch (e) {
    showError(e.message);
  }
}

function renderHits() {
  const r = state.searchResult;
  const tbody = $('hitTable').querySelector('tbody');
  if (!r.hits.length) {
    tbody.innerHTML = '<tr><td colspan="7">Eşleşme bulunamadı. Eşikleri gevşet ya da farklı bir veritabanı dene.</td></tr>';
    $('hitSummary').textContent = '0 kayıt';
    return;
  }
  $('hitSummary').innerHTML =
    `${r.n_hits} aday kayıt · kaynak: ${r.input_type === 'sequence'
      ? `BLAST (${esc(r.database)}), identity ve coverage eşikleri uygulandı`
      : 'Entrez nucleotide (identity/coverage bilgisi yok)'} · ${r.elapsed_s}s`
    + (r.entrez_query ? `<br>çalıştırılan sorgu: <code>${esc(r.entrez_query)}</code>` : '');

  tbody.innerHTML = r.hits.map((h, i) => `
    <tr>
      <td><input type="checkbox" class="hit-check" data-idx="${i}" ${i < 12 ? 'checked' : ''}></td>
      <td class="mono">${esc(h.accession)}</td>
      <td><span class="truncate" title="${esc(h.title)}">${esc(h.title)}</span></td>
      <td class="num">${h.identity ?? '—'}</td>
      <td class="num">${h.coverage ?? '—'}</td>
      <td class="num">${(h.subject_length || 0).toLocaleString()}</td>
      <td class="num mono">${h.evalue ?? '—'}</td>
    </tr>`).join('');
  $('topN').value = Math.min(12, r.hits.length);
  if (r.input_type === 'sequence') $('includeQuery').checked = true;
}

function selectTop(n) {
  document.querySelectorAll('.hit-check').forEach((c, i) => { c.checked = i < n; });
}
function setAllChecks(v) {
  document.querySelectorAll('.hit-check').forEach(c => { c.checked = v; });
}

/* ─── step 2: design ────────────────────────────────────────────────── */

async function startDesign() {
  const picked = [...document.querySelectorAll('.hit-check')]
    .filter(c => c.checked)
    .map(c => state.searchResult.hits[+c.dataset.idx]);

  if (!picked.length) { alert('En az bir kayıt seç.'); return; }

  const ranges = {};
  picked.forEach(h => { if (h.range) ranges[h.accession] = h.range; });

  const body = {
    accessions: picked.map(h => h.accession),
    ranges,
    query_sequence: ($('includeQuery').checked && state.searchResult.query_sequence)
      ? state.searchResult.query_sequence : '',
    query_label: 'QUERY',
    gene_label: $('geneLabel').value || 'target',
    specificity_check: $('specificityCheck').checked,
    primer3: {
      mode: $('mode').value,
      primer_min_size: int($('pMinSize'), 18),
      primer_opt_size: int($('pOptSize'), 20),
      primer_max_size: int($('pMaxSize'), 25),
      primer_min_tm: num($('pMinTm'), 57),
      primer_opt_tm: num($('pOptTm'), 60),
      primer_max_tm: num($('pMaxTm'), 63),
      primer_min_gc: num($('pMinGc'), 40),
      primer_max_gc: num($('pMaxGc'), 60),
      max_poly_x: int($('maxPolyX'), 4),
      gc_clamp: int($('gcClamp'), 1),
      max_hairpin_th: num($('maxHairpin'), 24),
      product_min: int($('productMin'), 100),
      product_max: int($('productMax'), 800),
      num_return: int($('numReturn'), 5),
      probe_min_tm: num($('probeMinTm'), 66),
      probe_opt_tm: num($('probeOptTm'), 70),
      probe_max_tm: num($('probeMaxTm'), 74),
      probe_min_size: int($('probeMinSize'), 18),
      probe_opt_size: int($('probeOptSize'), 22),
      probe_max_size: int($('probeMaxSize'), 27),
    },
    trim_to_homology: $('trimHomology').checked,
    conservation: {
      identity_threshold: num($('identityThreshold'), 1),
      max_gap_fraction: num($('maxGapFraction'), 0),
      min_block_length: int($('minBlockLength'), 24),
      reference: $('referenceMode').value,
      min_record_coverage: num($('minRecordCoverage'), 0.6),
    },
  };

  try {
    const { job_id } = await api('/api/design', body);
    state.designJob = job_id;
    showStep(3);
    $('runTitle').textContent = `Tasarım çalışıyor — ${picked.length} dizi`;
    const job = await pollJob(job_id);
    if (job.status === 'error') return showError(job.error);
    state.result = await api(`/api/job/${job_id}/result`);
    renderResult();
    showStep(4);
  } catch (e) {
    showError(e.message);
  }
}

/* ─── job polling ───────────────────────────────────────────────────── */

async function pollJob(jobId) {
  state.logSeen = 0;
  $('runLog').textContent = '';
  $('runError').classList.add('hidden');
  $('backAfterError').classList.add('hidden');
  $('progressBar').style.width = '0%';

  for (;;) {
    const job = await api(`/api/job/${jobId}?log_from=${state.logSeen}`);
    if (job.log.length) {
      $('runLog').textContent += job.log
        .map(l => `${l.time.slice(11, 19)}  ${l.message}`).join('\n') + '\n';
      $('runLog').scrollTop = $('runLog').scrollHeight;
      state.logSeen = job.log_total;
    }
    $('progressBar').style.width = job.progress + '%';
    showRunning(job);
    if (job.status === 'done' || job.status === 'error') return job;
    await sleep(1200);
  }
}

function showError(msg) {
  $('runError').textContent = msg || 'Bilinmeyen hata.';
  $('runError').classList.remove('hidden');
  $('backAfterError').classList.remove('hidden');
  $('runTitle').textContent = 'Başarısız';
}

/* ─── step 4: results ───────────────────────────────────────────────── */

function renderResult() {
  const r = state.result;
  state.selectedPair = 0;
  state.view = { start: 0, end: r.alignment.length };

  $('resultGene').textContent = r.gene_label;
  $('dlPrimers').href = `/api/job/${state.designJob}/file/primers.tsv`;
  $('dlAligned').href = `/api/job/${state.designJob}/file/aligned.fasta`;
  $('dlJson').href = `/api/job/${state.designJob}/file/results.json`;

  const consPct = (100 * r.conserved_bp / r.reference.sequence.length).toFixed(1);
  $('stats').innerHTML = [
    ['dizi', r.alignment.n],
    ['hizalama', r.alignment.length.toLocaleString() + ' kolon'],
    ['referans', r.reference.sequence.length.toLocaleString() + ' bp'],
    ['korunmuş', `${r.conserved_bp.toLocaleString()} bp · %${consPct}`],
    ['blok', r.blocks.length],
    ['primer çifti', r.pairs.length],
    ['süre', r.timings.total_s + ' s'],
  ].map(([k, v]) => `<div class="stat"><div class="v">${v}</div><div class="k">${k}</div></div>`).join('');

  renderPairList();
  renderMethods();
  renderSpecificity();
  renderRecords();
  selectPair(0);
}

function renderRecords() {
  const r = state.result;
  const cov = new Map((r.record_coverage || []).map(c => [c.label, c]));
  const low = new Set((r.low_coverage_records || []).map(c => c.label));

  const usedRows = r.alignment.labels.map((label, i) => {
    const c = cov.get(label);
    const pct = c ? (100 * c.coverage).toFixed(1) : '—';
    const status = low.has(label)
      ? '<span class="tag-warn">korunmuşluk hesabı dışı (kısmi kapsama)</span>'
      : '<span class="tag-ok">korunmuşluk hesabında</span>';
    const rev = r.alignment.reversed && r.alignment.reversed[i]
      ? ' <span class="hint">· ters çevrildi</span>' : '';
    return `<tr><td class="mono">${esc(label)}</td>
      <td class="num">${c ? c.aligned_bp.toLocaleString() : '—'}</td>
      <td class="num">${pct}</td><td>${status}${rev}</td></tr>`;
  });

  const excluded = [
    ...(r.download_failures || []).map(f => [f.accession, 'indirilemedi', f.reason]),
    ...(r.trim_dropped || []).map(f => [f.accession, 'homoloji kırpmasında düştü', f.reason]),
  ].map(([acc, kind, reason]) =>
    `<tr><td class="mono">${esc(acc)}</td><td colspan="2">${esc(kind)}</td>
     <td class="tag-bad">${esc(reason)}</td></tr>`);

  $('recordBox').innerHTML = `<div class="table-wrap"><table class="data-table">
    <thead><tr><th>Kayıt</th><th class="num">Hizalanan bp</th>
      <th class="num">Kapsama %</th><th>Durum</th></tr></thead>
    <tbody>${usedRows.join('')}${excluded.join('')}</tbody></table></div>
    <p class="hint" style="margin-top:8px">Korunmuşluk
      ${r.conservation_n}/${r.alignment.n} dizi üzerinden hesaplandı;
      doğrulama ${r.alignment.n} dizinin tamamına karşı yapıldı.</p>`;
}

function renderPairList() {
  const r = state.result;
  $('pairList').innerHTML = r.pairs.map((p, i) => {
    const cov = p.coverage_percent;
    const covClass = cov === 100 ? 'tag-ok' : cov >= 90 ? 'tag-warn' : 'tag-bad';
    return `<div class="pair-card ${i === state.selectedPair ? 'selected' : ''}" data-i="${i}">
      <div class="top"><span class="rank">Çift ${p.rank}</span>
        <span class="hint">${p.product_size} bp</span></div>
      <div class="seqline fw">F ${esc(p.forward.sequence)}</div>
      <div class="seqline rv">R ${esc(p.reverse.sequence)}</div>
      ${p.probe ? `<div class="seqline pb">P ${esc(p.probe.sequence)}</div>` : ''}
      <div class="meta">
        <span>Tm ${p.forward.tm}/${p.reverse.tm}°C</span>
        <span>ΔTm ${p.tm_difference ?? '—'}</span>
        <span>penalty ${p.pair_penalty ?? '—'}</span>
      </div>
      <div class="meta"><span class="${covClass}">tam eşleşme %${cov}</span>
        <span>blok F${p.block_hits.forward ?? '?'} / R${p.block_hits.reverse ?? '?'}</span></div>
      <div class="bar"><i style="width:${cov}%"></i></div>
    </div>`;
  }).join('');

  document.querySelectorAll('.pair-card').forEach(card => {
    card.onclick = () => selectPair(+card.dataset.i);
  });
}

function selectPair(i) {
  state.selectedPair = i;
  document.querySelectorAll('.pair-card').forEach((c, j) =>
    c.classList.toggle('selected', j === i));
  const pair = state.result.pairs[i];
  const probeOpt = $('bindingRole').querySelector('option[value=probe]');
  probeOpt.disabled = !pair.probe;
  if (!pair.probe && $('bindingRole').value === 'probe') $('bindingRole').value = 'forward';
  VIZ.drawAmplicon($('ampliconViz'), state.result, pair);
  VIZ.renderHeatmap($('coverageHeatmap'), pair);
  VIZ.renderStructures($('structureViz'), pair);
  renderMetrics();
  renderBindingPanel();
  redrawAll();
}

function renderBindingPanel() {
  const pair = state.result.pairs[state.selectedPair];
  const role = $('bindingRole').value;
  VIZ.renderBinding($('bindingDetail'), pair.binding[role], $('onlyMismatch').checked);
}

function renderMetrics() {
  const p = state.result.pairs[state.selectedPair];
  const roles = [['forward', 'Forward'], ['reverse', 'Reverse']]
    .concat(p.probe ? [['probe', 'Probe']] : []);

  const head = `<thead><tr><th>Oligo</th><th class="mono">Dizi 5'→3'</th>
    <th class="num">nt</th><th class="num">Tm °C</th><th class="num">GC %</th>
    <th class="num">Konum (ref)</th><th class="num">Hairpin °C</th>
    <th class="num">Self-any °C</th><th class="num">Self-end °C</th>
    <th class="num">Tam eşleşme</th></tr></thead>`;

  const rows = roles.map(([key, label]) => {
    const o = p[key], b = p.binding[key];
    return `<tr><td><b>${label}</b></td><td class="mono">${esc(o.sequence)}</td>
      <td class="num">${o.length}</td><td class="num">${o.tm ?? '—'}</td>
      <td class="num">${o.gc_percent ?? '—'}</td>
      <td class="num mono">${o.start + 1}–${o.end + 1} (${o.strand})</td>
      <td class="num">${o.hairpin_th ?? '—'}</td>
      <td class="num">${o.self_any_th ?? '—'}</td>
      <td class="num">${o.self_end_th ?? '—'}</td>
      <td class="num">${b.n_perfect}/${b.n_sequences} (%${b.perfect_percent})</td></tr>`;
  }).join('');

  const sizes = p.amplicons.map(a => a.product_size);
  const uniq = [...new Set(sizes)].sort((a, b) => a - b);
  const extra = `<tr><td colspan="10" class="hint">
    Çift penalty ${p.pair_penalty ?? '—'} · ΔTm ${p.tm_difference ?? '—'} °C ·
    ürün ${p.product_size} bp (referansta) · ürün Tm ${p.product_tm ?? '—'} °C ·
    F×R compl-any ${p.compl_any_th ?? '—'} °C, compl-end ${p.compl_end_th ?? '—'} °C ·
    dizilerdeki gerçek ürün boyu: ${uniq.length === 1 ? uniq[0] + ' bp (hepsi aynı)'
      : uniq[0] + '–' + uniq[uniq.length - 1] + ' bp'}
  </td></tr>`;

  $('metricsTable').innerHTML = head + '<tbody>' + rows + extra + '</tbody>';
}

function renderSpecificity() {
  const s = state.result.specificity;
  const box = $('specificityBox');
  if (!s || !s.available) {
    box.innerHTML = `<p class="hint">Yapılmadı: ${esc(s?.reason || 'kapalı')}</p>`;
    return;
  }
  const rows = s.oligos.map(o => {
    const multi = o.n_multi_site_subjects;
    const cls = multi ? 'tag-warn' : 'tag-ok';
    return `<tr><td class="mono">${esc(o.oligo)}</td>
      <td class="num">${o.total_sites}</td>
      <td class="num">${o.subjects_hit}/${s.n_subjects}</td>
      <td class="num ${cls}">${multi}</td></tr>`;
  }).join('');
  box.innerHTML = `<div class="table-wrap"><table class="data-table">
    <thead><tr><th>Oligo</th><th class="num">Toplam bağlanma yeri</th>
    <th class="num">Kaç dizide</th><th class="num">Birden fazla yer</th></tr></thead>
    <tbody>${rows}</tbody></table></div>
    <p class="hint" style="margin-top:8px">${esc(s.note)}</p>`;
}

function renderMethods() {
  const r = state.result;
  const c = r.settings.conservation, p = r.settings.primer3;
  $('methodsBox').textContent = [
    `Girdi: ${r.records.length} NCBI kaydı${r.query_included ? ' + girilen dizi' : ''}`,
    `Hizalama: MAFFT --auto (${r.tool_versions.mafft}) → ${r.alignment.n} × ${r.alignment.length} kolon`,
    `Referans: ${r.reference.label} (${r.reference.mode}), ${r.reference.sequence.length} bp`,
    `Korunmuşluk: kolon identity >= ${c.identity_threshold}, gap oranı <= ${c.max_gap_fraction},`,
    `  minimum blok ${c.min_block_length} bp → ${r.blocks.length} blok, ${r.conserved_bp} bp`,
    `Primer3: ${r.tool_versions.primer3_core}, mod ${p.mode}`,
    `  Tm ${p.primer_min_tm}-${p.primer_opt_tm}-${p.primer_max_tm} °C, GC ${p.primer_min_gc}-${p.primer_max_gc} %,`,
    `  uzunluk ${p.primer_min_size}-${p.primer_max_size} nt, ürün ${p.product_min}-${p.product_max} bp,`,
    `  değişken bölgeler SEQUENCE_EXCLUDED_REGION olarak verildi (${r.excluded_regions.length} bölge)`,
    `Spesifiklik: ${r.specificity.available ? r.tool_versions.blastn + ' (yalnız indirilen diziler)' : 'yapılmadı'}`,
    `Süreler: ${JSON.stringify(r.timings)}`,
    `Çıktı dizini: ${r.workdir}`,
    r.primer3_warning ? `Primer3 uyarısı: ${r.primer3_warning}` : '',
    `Primer3 explain — left: ${r.primer3_explain.left}`,
    `Primer3 explain — right: ${r.primer3_explain.right}`,
    `Primer3 explain — pair: ${r.primer3_explain.pair}`,
  ].filter(Boolean).join('\n');
}

/* ─── canvas interaction ────────────────────────────────────────────── */

let canvasMap = null;

function setupCanvas() {
  const cv = $('alnCanvas');
  const tip = $('alnTooltip');

  cv.addEventListener('mousedown', (e) => {
    state.drag = { x0: e.offsetX, x1: e.offsetX };
  });
  cv.addEventListener('mousemove', (e) => {
    if (!state.result) return;
    state.hover = { x: e.offsetX };
    if (state.drag) state.drag.x1 = e.offsetX;
    redrawAll();
    if (canvasMap) {
      const col = canvasMap.colAt(e.offsetX);
      const r = state.result;
      if (col >= 0 && col < r.alignment.length) {
        const block = r.blocks.find(b => col >= b.col_start && col < b.col_end);
        tip.classList.remove('hidden');
        tip.style.left = (e.clientX + 14) + 'px';
        tip.style.top = (e.clientY - 10) + 'px';
        tip.textContent =
          `kolon ${col + 1}\nkonsensüs ${r.profile.major[col]} (${r.profile.code[col]})\n` +
          `identity ${(r.profile.identity[col] * 100).toFixed(0)}%  gap ${(r.profile.gap[col] * 100).toFixed(0)}%\n` +
          (block ? `korunmuş blok #${block.rank} (${block.length} bp)` : 'değişken bölge');
      }
    }
    if (state.drag && Math.abs(state.drag.x1 - state.drag.x0) > 3) drawSelection();
  });
  cv.addEventListener('mouseleave', () => {
    tip.classList.add('hidden');
    state.hover = null; state.drag = null;
    if (state.result) redrawAll();
  });
  cv.addEventListener('mouseup', (e) => {
    if (!state.drag || !canvasMap) { state.drag = null; return; }
    const x0 = Math.min(state.drag.x0, e.offsetX), x1 = Math.max(state.drag.x0, e.offsetX);
    state.drag = null;
    if (x1 - x0 < 6) return;
    const a = canvasMap.colAt(x0), b = canvasMap.colAt(x1);
    if (b - a < 10) return;
    state.view = { start: Math.max(0, a), end: Math.min(state.result.alignment.length, b) };
    redrawAll();
  });
  cv.addEventListener('dblclick', () => {
    if (!state.result) return;
    state.view = { start: 0, end: state.result.alignment.length };
    redrawAll();
  });
}

function drawSelection() {
  const cv = $('alnCanvas');
  const ctx = cv.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const x0 = Math.min(state.drag.x0, state.drag.x1);
  const w = Math.abs(state.drag.x1 - state.drag.x0);
  ctx.fillStyle = 'rgba(31,111,235,.18)';
  ctx.fillRect(x0, 0, w, cv.clientHeight);
}

function redrawAll() {
  if (!state.result) return;
  canvasMap = VIZ.drawAlignment($('alnCanvas'), {
    result: state.result,
    view: state.view,
    pair: state.result.pairs[state.selectedPair],
    hover: state.hover,
  });
  const v = state.view;
  $('alnRuler').innerHTML =
    `<span>kolon ${(v.start + 1).toLocaleString()}</span>` +
    `<span>${(v.end - v.start).toLocaleString()} kolon görünüyor</span>` +
    `<span>kolon ${v.end.toLocaleString()}</span>`;
}

/* ─── helpers ───────────────────────────────────────────────────────── */

function showStep(n) {
  [1, 2, 3, 4].forEach(i => $('step' + i).classList.toggle('hidden', i !== n));
  document.querySelectorAll('.step').forEach(s => {
    const i = +s.dataset.step;
    s.classList.toggle('active', i === n);
    s.classList.toggle('done', i < n);
  });
  window.scrollTo({ top: 0, behavior: 'smooth' });
  if (n === 4) setTimeout(redrawAll, 60);
}

async function api(path, body) {
  const opts = body
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    : {};
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* keep statusText */ }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return res.json();
}

const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const int = (el, d) => { const v = parseInt(el.value, 10); return Number.isFinite(v) ? v : d; };
const num = (el, d) => { const v = parseFloat(el.value); return Number.isFinite(v) ? v : d; };
const cleanSeq = (t) => t.split('\n').filter(l => !l.startsWith('>')).join('').replace(/[^A-Za-z]/g, '');

/* Example input: first 600 bp of the invA CDS of Salmonella enterica subsp.
   enterica serovar Typhimurium str. LT2, downloaded from NCBI on 2026-08-15.
   Region NC_003197.2:c3039006-3038407 (gene invA, GeneID 1254419). */
const EXAMPLE = `>NC_003197.2:c3039006-3038407 Salmonella Typhimurium LT2 invA (first 600 bp)
GTGACGCTGGCGCGCAACGTCAATGAATATTTCGGTATTCAGGAAACAAAACATATGCTGGACCAACTGG
AAGCGAAATTTCCTGATTTACTTAAAGAAGTGCTCAGACATGCCACGGTACAACGTATATCTGAAGTTTT
GCAGCGTTTGTTAAGCGAACGTGTTTCCGTGCGTAATATGAAGTTAATTATGGAAGCGCTCGCATTGTGG
GCGCCAAGAGAAAAAGATGTCATTAACCTTGTGGAGCATATTCGTGGAGCAATGGCGCGTTATATTTGTC
ATAAATTCGCCAATGGCGGCGAATTACGAGCAGTAATGGTATCTGCTGAAGTTGAGGATGTTATTCGCAA
AGGGATCCGTCAGACCTCTGGCAGTACCTTCCTCAGCCTTGACCCGGAAGCCTCCGCTAATTTGATGGAT
CTCATTACACTTAAGTTGGATGATTTATTGATTGCACATAAAGATCTTGTCCTCCTTACGTCTGTCGATG
TCCGTCGATTTATTAAGAAAATGATTGAAGGTCGTTTTCCGGATCTGGAGGTTTTATCTTTCGGTGAGAT
AGCAGATAGCAAGTCAGTGA`;
