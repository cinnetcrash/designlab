/* Primer Designer — visualisation layer.
   Everything drawn here comes from the job result; nothing is smoothed,
   interpolated or invented. When a pixel aggregates several alignment columns
   the worst-case column wins, so a single mismatch never disappears at zoom-out. */

const VIZ = (() => {

  const css = (name) => getComputedStyle(document.body).getPropertyValue(name).trim();

  /* ─── colours pulled from the stylesheet so themes stay in sync ─────── */
  function palette() {
    return {
      text: css('--text'), dim: css('--text-dim'), border: css('--border'),
      surface: css('--surface'), surface2: css('--surface-2'),
      conserved: css('--conserved'), variable: css('--variable'),
      match: css('--match'), gap: css('--gap'), danger: css('--danger'),
      forward: css('--forward'), reverse: css('--reverse'), probe: css('--probe'),
      accent: css('--accent'),
    };
  }

  /* ─────────────────────────────────────────────────────────────────────
     1. Alignment / conservation map
     ───────────────────────────────────────────────────────────────────── */

  const LANE = { primer: 26, blocks: 12, cons: 66, pad: 6 };

  function drawAlignment(canvas, state) {
    const { result, view, pair, hover } = state;
    const p = palette();
    const aln = result.alignment;
    const prof = result.profile;
    const dpr = window.devicePixelRatio || 1;

    const cssW = canvas.clientWidth || 900;
    const rowsH = Math.max(60, Math.min(190, aln.n * 14));
    const cssH = LANE.primer + LANE.blocks + LANE.cons + rowsH + 26;
    canvas.style.height = cssH + 'px';
    canvas.width = Math.round(cssW * dpr);
    canvas.height = Math.round(cssH * dpr);
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    const c0 = view.start, c1 = view.end;            // column window
    const nCols = Math.max(1, c1 - c0);
    const W = cssW, xOf = (col) => ((col - c0) / nCols) * W;
    const colAt = (x) => Math.floor(c0 + (x / W) * nCols);
    const perPix = nCols / W;

    // ── lane 1: primer arrows ───────────────────────────────────────────
    let y = 0;
    ctx.fillStyle = p.surface2;
    ctx.fillRect(0, y, W, LANE.primer);
    if (pair) {
      drawOligoArrow(ctx, pair.binding.forward, xOf, y + 4, p.forward, 'F', W);
      if (pair.binding.probe)
        drawOligoArrow(ctx, pair.binding.probe, xOf, y + 4, p.probe, 'P', W, true);
      drawOligoArrow(ctx, pair.binding.reverse, xOf, y + 14, p.reverse, 'R', W);

      // amplicon span
      const a0 = xOf(pair.binding.forward.col_start);
      const a1 = xOf(pair.binding.reverse.col_end);
      ctx.strokeStyle = p.accent; ctx.setLineDash([3, 3]); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(a0, y + 23.5); ctx.lineTo(a1, y + 23.5); ctx.stroke();
      ctx.setLineDash([]);
    }

    // ── lane 2: conserved blocks ────────────────────────────────────────
    y += LANE.primer;
    ctx.fillStyle = p.variable;
    ctx.globalAlpha = 0.35;
    ctx.fillRect(0, y, W, LANE.blocks);
    ctx.globalAlpha = 1;
    ctx.fillStyle = p.conserved;
    result.blocks.forEach(b => {
      const x0 = xOf(b.col_start), x1 = xOf(b.col_end);
      if (x1 < 0 || x0 > W) return;
      ctx.fillRect(x0, y, Math.max(1, x1 - x0), LANE.blocks);
    });

    // ── lane 3: per-column identity ─────────────────────────────────────
    y += LANE.blocks;
    ctx.fillStyle = p.surface;
    ctx.fillRect(0, y, W, LANE.cons);
    const base = y + LANE.cons - 2;
    for (let px = 0; px < W; px++) {
      const s = Math.floor(c0 + px * perPix);
      const e = Math.max(s + 1, Math.floor(c0 + (px + 1) * perPix));
      let worst = 1, gapMax = 0;
      for (let c = s; c < e && c < prof.identity.length; c++) {
        worst = Math.min(worst, prof.identity[c]);
        gapMax = Math.max(gapMax, prof.gap[c]);
      }
      const h = Math.max(1, worst * (LANE.cons - 6));
      ctx.fillStyle = worst >= 1 ? p.conserved : (worst >= 0.9 ? p.variable : p.danger);
      ctx.globalAlpha = gapMax > 0.2 ? 0.4 : 1;
      ctx.fillRect(px, base - h, 1, h);
    }
    ctx.globalAlpha = 1;
    ctx.strokeStyle = p.border; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(0, base + .5); ctx.lineTo(W, base + .5); ctx.stroke();
    ctx.fillStyle = p.dim; ctx.font = '10px ui-monospace, monospace';
    ctx.fillText('identity', 4, y + 11);

    // ── lane 4: the alignment itself ────────────────────────────────────
    y += LANE.cons + 4;
    const rowH = Math.max(3, Math.min(13, rowsH / aln.n));
    const refMajor = prof.major;
    for (let r = 0; r < aln.n; r++) {
      const seq = aln.sequences[r];
      const ry = y + r * rowH;
      for (let px = 0; px < W; px++) {
        const s = Math.floor(c0 + px * perPix);
        const e = Math.max(s + 1, Math.floor(c0 + (px + 1) * perPix));
        let kind = 0;                                   // 0 match, 1 gap, 2 mismatch
        for (let c = s; c < e && c < seq.length; c++) {
          const b = seq[c];
          if (b === '-') { if (kind < 1) kind = 1; }
          else if (b !== refMajor[c]) { kind = 2; break; }
        }
        ctx.fillStyle = kind === 2 ? p.danger : (kind === 1 ? p.gap : p.match);
        ctx.fillRect(px, ry, 1, Math.max(2, rowH - 1));
      }
      if (rowH >= 9) {
        ctx.fillStyle = p.dim; ctx.font = '9px ui-monospace, monospace';
        ctx.fillText(aln.labels[r].slice(0, 14), 3, ry + rowH - 2);
      }
    }

    // primer site outlines on top of the rows
    if (pair) {
      [['forward', p.forward], ['probe', p.probe], ['reverse', p.reverse]]
        .forEach(([role, colour]) => {
          const b = pair.binding[role];
          if (!b) return;
          const x0 = xOf(b.col_start), x1 = xOf(b.col_end + 1);
          ctx.strokeStyle = colour; ctx.lineWidth = 1.2;
          ctx.strokeRect(x0 - .5, y - 1.5, Math.max(2, x1 - x0), aln.n * rowH + 2);
        });
    }

    // hover crosshair
    if (hover && hover.x >= 0) {
      const col = colAt(hover.x);
      ctx.strokeStyle = p.accent; ctx.globalAlpha = .6; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(hover.x + .5, 0); ctx.lineTo(hover.x + .5, cssH);
      ctx.stroke(); ctx.globalAlpha = 1;
    }

    return { colAt, xOf, rowH, rowsTop: y };
  }

  function drawOligoArrow(ctx, binding, xOf, y, colour, letter, W, isBox) {
    if (!binding) return;
    const x0 = xOf(binding.col_start), x1 = xOf(binding.col_end + 1);
    const w = Math.max(4, x1 - x0);
    if (x1 < -10 || x0 > W + 10) return;
    const h = 8;
    ctx.fillStyle = colour;
    if (isBox) {
      ctx.fillRect(x0, y + 1, w, h - 2);
    } else if (letter === 'F') {
      ctx.beginPath();
      ctx.moveTo(x0, y); ctx.lineTo(x1 - Math.min(6, w / 2), y);
      ctx.lineTo(x1, y + h / 2); ctx.lineTo(x1 - Math.min(6, w / 2), y + h);
      ctx.lineTo(x0, y + h); ctx.closePath(); ctx.fill();
    } else {
      ctx.beginPath();
      ctx.moveTo(x1, y); ctx.lineTo(x0 + Math.min(6, w / 2), y);
      ctx.lineTo(x0, y + h / 2); ctx.lineTo(x0 + Math.min(6, w / 2), y + h);
      ctx.lineTo(x1, y + h); ctx.closePath(); ctx.fill();
    }
    if (w > 22) {
      ctx.fillStyle = '#fff'; ctx.font = 'bold 8px ui-monospace, monospace';
      ctx.fillText(letter, x0 + 3, y + h - 1.5);
    }
  }

  /* ─────────────────────────────────────────────────────────────────────
     2. Amplicon schematic + binding close-ups
     ───────────────────────────────────────────────────────────────────── */

  function drawAmplicon(container, result, pair) {
    const p = palette();
    const refLen = result.reference.sequence.length;
    const W = 1000, H = 132;
    const m = 40, plotW = W - 2 * m;
    const x = (pos) => m + (pos / refLen) * plotW;

    const fw = pair.forward, rv = pair.reverse, pb = pair.probe;
    const parts = [];

    // conserved blocks under the template line
    result.blocks.forEach(b => {
      parts.push(`<rect x="${x(b.ref_start).toFixed(1)}" y="58"
        width="${Math.max(1, x(b.ref_end + 1) - x(b.ref_start)).toFixed(1)}" height="14"
        fill="${p.conserved}" opacity=".30"></rect>`);
    });

    // template strands
    parts.push(`<rect x="${m}" y="60" width="${plotW}" height="4" rx="2" fill="${p.match}"></rect>`);
    parts.push(`<rect x="${m}" y="68" width="${plotW}" height="4" rx="2" fill="${p.match}"></rect>`);
    parts.push(`<text class="svg-dim" x="${m - 6}" y="64" text-anchor="end">5'</text>`);
    parts.push(`<text class="svg-dim" x="${W - m + 6}" y="64">3'</text>`);
    parts.push(`<text class="svg-dim" x="${m - 6}" y="76" text-anchor="end">3'</text>`);
    parts.push(`<text class="svg-dim" x="${W - m + 6}" y="76">5'</text>`);

    // amplicon span
    const ax0 = x(fw.start), ax1 = x(rv.end + 1);
    parts.push(`<rect x="${ax0}" y="52" width="${Math.max(2, ax1 - ax0)}" height="28"
      fill="${p.accent}" opacity=".08"></rect>`);
    parts.push(`<line x1="${ax0}" y1="96" x2="${ax1}" y2="96" stroke="${p.accent}"
      stroke-width="1"></line>`);
    parts.push(`<line x1="${ax0}" y1="92" x2="${ax0}" y2="100" stroke="${p.accent}"></line>`);
    parts.push(`<line x1="${ax1}" y1="92" x2="${ax1}" y2="100" stroke="${p.accent}"></line>`);
    parts.push(`<text class="svg-label" x="${(ax0 + ax1) / 2}" y="112" text-anchor="middle"
      fill="${p.accent}">amplikon ${pair.product_size} bp</text>`);

    // primers, drawn on the strand they anneal to
    parts.push(arrowSvg(x(fw.start), x(fw.end + 1), 44, p.forward, '+',
      `F ${fw.length} nt · Tm ${fw.tm}°C`));
    parts.push(arrowSvg(x(rv.start), x(rv.end + 1), 78, p.reverse, '-',
      `R ${rv.length} nt · Tm ${rv.tm}°C`));
    if (pb) {
      parts.push(`<rect x="${x(pb.start)}" y="44" width="${Math.max(3, x(pb.end + 1) - x(pb.start))}"
        height="9" rx="2" fill="${p.probe}"></rect>`);
      parts.push(`<text class="svg-dim" x="${x(pb.start)}" y="40">P ${pb.length} nt · Tm ${pb.tm}°C</text>`);
    }

    // scale
    const ticks = 6;
    for (let i = 0; i <= ticks; i++) {
      const pos = Math.round((refLen / ticks) * i);
      parts.push(`<line x1="${x(pos)}" y1="122" x2="${x(pos)}" y2="126" stroke="${p.dim}"></line>`);
      parts.push(`<text class="svg-dim" x="${x(pos)}" y="136" text-anchor="middle">${pos.toLocaleString()}</text>`);
    }
    parts.push(`<text class="svg-dim" x="${m}" y="20">referans: ${result.reference.label}
      (${refLen.toLocaleString()} bp)</text>`);

    const svg = `<svg viewBox="0 0 ${W} ${H + 14}" preserveAspectRatio="xMidYMid meet">${parts.join('')}</svg>`;

    // close-ups: what actually pairs with what
    const closeups = [
      closeupDuplex(result, pair.forward, 'Forward primer', p.forward),
      pair.probe ? closeupDuplex(result, pair.probe, 'Probe', p.probe) : '',
      closeupDuplex(result, pair.reverse, 'Reverse primer', p.reverse),
    ].join('');

    container.innerHTML = svg + `<div class="struct-grid" style="margin-top:14px">${closeups}</div>`;
  }

  function arrowSvg(x0, x1, y, colour, strand, label) {
    const w = Math.max(6, x1 - x0), head = Math.min(8, w / 2);
    const pts = strand === '+'
      ? `${x0},${y} ${x0 + w - head},${y} ${x0 + w},${y + 4.5} ${x0 + w - head},${y + 9} ${x0},${y + 9}`
      : `${x0 + w},${y} ${x0 + head},${y} ${x0},${y + 4.5} ${x0 + head},${y + 9} ${x0 + w},${y + 9}`;
    const anchor = strand === '+' ? x0 : x0 + w;
    const ty = strand === '+' ? y - 4 : y + 20;
    return `<polygon points="${pts}" fill="${colour}"></polygon>
      <text class="svg-dim" x="${anchor}" y="${ty}"
        text-anchor="${strand === '+' ? 'start' : 'end'}">${label}</text>`;
  }

  /* Ten bases of context either side, both template strands, primer annealed. */
  function closeupDuplex(result, oligo, title, colour) {
    const ref = result.reference.sequence;
    const flank = 8;
    const s = Math.max(0, oligo.start - flank);
    const e = Math.min(ref.length, oligo.end + 1 + flank);
    const top = ref.slice(s, e);
    const bottom = complement(top);
    const lead = oligo.start - s, tail = e - (oligo.end + 1);
    const site = oligo.template_slice;

    const note = oligo.strand === '+'
      ? 'primer üst zincirle aynı diziye sahiptir; alt zincire bağlanır, 3\' ucu sağa uzar'
      : 'primer alt zincirle aynı diziye sahiptir; üst zincire bağlanır, 3\' ucu sola uzar';

    const mark = (str, cls) => `<span class="${cls}">${esc(str)}</span>`;
    const seg = (str) => mark(str.slice(0, lead), 'dim') +
      esc(str.slice(lead, lead + site.length)) + mark(str.slice(lead + site.length), 'dim');

    const strandRows = [
      `5'-${seg(top)}-3'  şablon (+)`,
      `   ${' '.repeat(lead)}${'|'.repeat(site.length)}`,
      `3'-${seg(bottom)}-5'  şablon (−)`,
    ];

    const primerRow = oligo.strand === '+'
      ? `   ${' '.repeat(lead)}<b style="color:${colour}">${esc(site)}</b>  primer 5'→3'`
      : `   ${' '.repeat(lead)}<b style="color:${colour}">${esc(complement(site))}</b>  primer 3'←5'`;

    const ordered = oligo.strand === '+'
      ? [primerRow, ...strandRows]
      : [...strandRows, primerRow];

    return `<div class="struct">
      <h4 style="color:${colour}">${title} — referans ${oligo.start + 1}–${oligo.end + 1}</h4>
      <pre>${ordered.join('\n')}</pre>
      <div class="val">${esc(note)}<br>
        sipariş dizisi (5'→3'): <b>${esc(oligo.sequence)}</b></div>
    </div>`;
  }

  const COMP = { A: 'T', T: 'A', G: 'C', C: 'G', N: 'N', '-': '-' };
  const complement = (s) => [...s].map(b => COMP[b] || 'N').join('');

  /* ─────────────────────────────────────────────────────────────────────
     3. Per-sequence binding detail
     ───────────────────────────────────────────────────────────────────── */

  function renderBinding(container, binding, onlyMismatch) {
    if (!binding) { container.innerHTML = '<p class="hint">Bu oligo yok.</p>'; return; }
    const site = binding.plus_strand_site;
    const rows = binding.per_sequence
      .filter(s => !onlyMismatch || !s.perfect)
      .map(s => {
        const mmCols = new Map(s.mismatches.map(m => [m.column, m]));
        // observed_cols carries the real alignment column of each shown base;
        // insertion columns are skipped, so index arithmetic would drift.
        const cols = s.observed_cols;
        let obs = '';
        for (let i = 0; i < s.observed.length; i++) {
          const col = cols[i];
          const b = s.observed[i];
          const isTp = mmCols.get(col)?.critical;
          if (mmCols.has(col)) obs += `<span class="mm ${isTp ? 'tp' : ''}">${esc(b)}</span>`;
          else obs += esc(b);
        }
        const bars = cols.map(col => mmCols.has(col) ? ' ' : '|').join('');
        const tag = s.perfect
          ? '<span class="tag-ok">tam eşleşme</span>'
          : (s.n_three_prime_mismatch
            ? `<span class="tag-bad">${s.n_mismatch} uyumsuz · 3' uçta ${s.n_three_prime_mismatch}</span>`
            : `<span class="tag-warn">${s.n_mismatch} uyumsuz</span>`);
        const ins = s.insertions ? ` <span class="tag-warn">+${s.insertions} insersiyon</span>` : '';
        return `<div class="bind-row">
          <div class="bind-head"><span class="lbl">${esc(s.label)}</span><span>${tag}${ins}</span></div>
          <div class="duplex"><span class="dim">primer  </span>${esc(site)}
<span class="dim">        </span>${bars}
<span class="dim">şablon  </span>${obs}</div>
        </div>`;
      });
    container.innerHTML = rows.length ? rows.join('')
      : '<p class="hint">Tüm diziler tam eşleşiyor.</p>';
  }

  /* ─────────────────────────────────────────────────────────────────────
     4. Coverage heat map
     ───────────────────────────────────────────────────────────────────── */

  function renderHeatmap(container, pair) {
    const roles = ['forward', 'reverse'].concat(pair.binding.probe ? ['probe'] : []);
    const labels = pair.binding.forward.per_sequence.map(s => s.label);
    const heatColour = (n) => n === 0 ? '#2f9e6f'
      : n === 1 ? '#7cb342' : n === 2 ? '#d9a334'
        : n <= 4 ? '#e2703a' : '#c0392b';

    const head = `<div class="heat-row"><span></span>${roles
      .map(r => `<span class="heat-head">${r === 'forward' ? 'F' : r === 'reverse' ? 'R' : 'P'}</span>`)
      .join('')}</div>`;

    const rows = labels.map((label, i) => {
      const cells = roles.map(role => {
        const s = pair.binding[role].per_sequence[i];
        const crit = s.n_three_prime_mismatch > 0 ? ' crit' : '';
        return `<span class="heat-cell${crit}" style="background:${heatColour(s.n_mismatch)}"
          title="${esc(label)} · ${role} · ${s.n_mismatch} uyumsuz, 3' uçta ${s.n_three_prime_mismatch}">${s.n_mismatch}</span>`;
      }).join('');
      return `<div class="heat-row"><span class="heat-label" title="${esc(label)}">${esc(label)}</span>${cells}</div>`;
    });

    container.innerHTML = `<div class="heat">${head}${rows.join('')}</div>
      <p class="hint" style="margin-top:8px">Sayı = uyumsuz baz. Kırmızı çerçeve =
      3' uçtaki son 5 bazda uyumsuzluk (uzama ciddi şekilde bozulur).</p>`;
  }

  /* ─────────────────────────────────────────────────────────────────────
     5. Dimer / hairpin diagrams
     ───────────────────────────────────────────────────────────────────── */

  function renderStructures(container, pair) {
    const g = pair.geometry, p = palette();
    const cards = [];

    const duplexCard = (title, d, thermo) => {
      if (!d) return `<div class="struct"><h4>${title}</h4>
        <pre>eşleşme bulunamadı (&lt;3 baz çifti)</pre>${thermo || ''}</div>`;
      return `<div class="struct"><h4>${title}</h4>
        <pre>5'-${esc(d.top)}-3'
   ${esc(d.match)}
3'-${esc(d.bottom)}-5'</pre>
        <div class="val">${d.pairs} baz çifti · en uzun kesintisiz dizi ${d.longest_run}
          · 3' uçta ${d.three_prime_pairs}${thermo || ''}</div></div>`;
    };

    const th = (label, v) => v === null || v === undefined ? ''
      : `<br>Primer3 ${label}: <b>${v} °C</b>`;

    cards.push(duplexCard('Forward self-dimer', g.forward_self_dimer,
      th('SELF_ANY_TH', pair.forward.self_any_th) + th('SELF_END_TH', pair.forward.self_end_th)));
    cards.push(duplexCard('Reverse self-dimer', g.reverse_self_dimer,
      th('SELF_ANY_TH', pair.reverse.self_any_th) + th('SELF_END_TH', pair.reverse.self_end_th)));
    cards.push(duplexCard('F × R cross-dimer', g.cross_dimer,
      th('PAIR_COMPL_ANY_TH', pair.compl_any_th) + th('PAIR_COMPL_END_TH', pair.compl_end_th)));

    cards.push(hairpinCard('Forward hairpin', g.forward_hairpin, pair.forward.hairpin_th));
    cards.push(hairpinCard('Reverse hairpin', g.reverse_hairpin, pair.reverse.hairpin_th));

    if (pair.probe) {
      cards.push(duplexCard('Probe self-dimer', g.probe_self_dimer,
        th('SELF_ANY_TH', pair.probe.self_any_th)));
      cards.push(duplexCard('Probe × F dimer', g.probe_forward_dimer, ''));
      cards.push(duplexCard('Probe × R dimer', g.probe_reverse_dimer, ''));
      cards.push(hairpinCard('Probe hairpin', g.probe_hairpin, pair.probe.hairpin_th));
      if (g.probe_five_prime_g) {
        cards.push(`<div class="struct"><h4 class="tag-bad">Prob 5' ucu G</h4>
          <pre>${esc(pair.probe.sequence.slice(0, 12))}…</pre>
          <div class="val tag-bad">Hidroliz problarında 5'-G raportör boyayı söndürür;
          probu 1–2 baz kaydırmak ya da ters zinciri kullanmak gerekir.</div></div>`);
      }
    }
    container.innerHTML = cards.join('');
  }

  function hairpinCard(title, hp, thermoTm) {
    const t = thermoTm === null || thermoTm === undefined ? ''
      : `<br>Primer3 HAIRPIN_TH: <b>${thermoTm} °C</b>`;
    if (!hp) return `<div class="struct"><h4>${title}</h4>
      <pre>3 bazdan uzun sap bulunamadı</pre><div class="val">${t}</div></div>`;
    const loop = hp.loop_seq;
    const arm = hp.stem5_seq, arm3 = hp.stem3_seq;
    const pairsLine = '|'.repeat(hp.stem_length);
    return `<div class="struct"><h4>${title}</h4>
      <pre>5'-…${esc(arm)}
      ${esc(pairsLine)}   ╮
3'-…${esc([...arm3].reverse().join(''))}   ├ döngü ${hp.loop_length} nt: ${esc(loop)}
                 ╯</pre>
      <div class="val">sap ${hp.stem_length} bp · döngü ${hp.loop_length} nt${t}</div></div>`;
  }

  /* ─────────────────────────────────────────────────────────────────────
     helpers
     ───────────────────────────────────────────────────────────────────── */

  function esc(s) {
    return String(s ?? '').replace(/[&<>"]/g,
      c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  return { drawAlignment, drawAmplicon, renderBinding, renderHeatmap, renderStructures, esc };
})();
