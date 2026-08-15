"""Design pipeline: download → align → conserved blocks → Primer3 → validate."""
from __future__ import annotations

import csv
import json
import re
import time
from pathlib import Path
from typing import Any

import alignment
import config
import db
import homology
import jobs
import ncbi
import primer3_runner
import validation
from models import DesignRequest, SearchRequest
from sequtils import clean_sequence


# ─── Step 1: search ──────────────────────────────────────────────────────────

def run_search(job_id: str, req: SearchRequest) -> dict[str, Any]:
    """Find candidate homologues; index the search in the run database."""
    created = (jobs.get_job_meta(job_id) or {}).get("created", "")
    started = time.time()
    try:
        result = _run_search(job_id, req)
    except Exception as exc:                            # noqa: BLE001
        db.record_failure(
            job_id, "search", created, f"{exc.__class__.__name__}: {exc}",
            {"input_type": req.input_type, "mode": req.database,
             "preview": req.text[:60],
             "elapsed_s": round(time.time() - started, 1)},
        )
        raise
    db.record_search(job_id, result, created)
    return result


def _run_search(job_id: str, req: SearchRequest) -> dict[str, Any]:
    def progress(msg: str) -> None:
        jobs.log(job_id, msg)

    started = time.time()
    jobs.log(job_id, "Search started", progress=5, stage="search")

    if req.input_type == "sequence":
        query = clean_sequence(req.text)
        if len(query) > config.MAX_QUERY_BP:
            raise ValueError(
                f"Query sequence is {len(query):,} bp; the limit is "
                f"{config.MAX_QUERY_BP:,} bp. Paste a single gene, not a genome."
            )
        jobs.log(job_id, f"Query sequence: {len(query):,} bp", progress=10,
                 tool="NCBI BLAST (remote)")
        hits = ncbi.blast_search(
            query, database=req.database, max_hits=req.max_hits,
            min_identity=req.min_identity, min_coverage=req.min_coverage,
            progress=progress,
        )
        query_seq = query
        entrez_query = ""
    else:
        entrez_query = ncbi.build_entrez_query(
            gene=req.gene or req.text, organism=req.organism,
            min_length=req.min_length, max_length=req.max_length,
            raw_query=req.raw_query,
        )
        jobs.log(job_id, f"Entrez query built: {entrez_query}", progress=10,
                 tool="NCBI Entrez")
        hits = ncbi.entrez_search(entrez_query, max_hits=req.max_hits,
                                  progress=progress)
        query_seq = ""

    jobs.log(job_id, f"Search finished in {time.time() - started:.0f}s "
                     f"({len(hits)} candidate record(s))", progress=100)

    result = {
        "input_type": req.input_type,
        "query_sequence": query_seq,
        "query_length": len(query_seq),
        "hits": hits,
        "n_hits": len(hits),
        "database": req.database if req.input_type == "sequence" else "nucleotide",
        "entrez_query": entrez_query,
        "elapsed_s": round(time.time() - started, 1),
    }
    (jobs.workdir(job_id) / "search.json").write_text(json.dumps(result, indent=2))
    return result


# ─── Step 2: design ──────────────────────────────────────────────────────────

def run_design(job_id: str, req: DesignRequest) -> dict[str, Any]:
    """Run the design; index the outcome in the run database either way."""
    started = time.time()
    created = (jobs.get_job_meta(job_id) or {}).get("created", "")
    try:
        result = _run_design(job_id, req)
    except Exception as exc:                            # noqa: BLE001
        db.record_failure(
            job_id, "design", created, f"{exc.__class__.__name__}: {exc}",
            {"gene_label": req.gene_label, "mode": req.primer3.mode,
             "input_type": "design", "n_accessions": len(req.accessions),
             "preview": ", ".join(req.accessions[:4]),
             "elapsed_s": round(time.time() - started, 1),
             "settings": {"primer3": req.primer3.model_dump(),
                          "conservation": req.conservation.model_dump()}},
        )
        raise
    db.record_design(job_id, result, created)
    return result


def _run_design(job_id: str, req: DesignRequest) -> dict[str, Any]:
    started = time.time()
    workdir = jobs.workdir(job_id)
    timings: dict[str, float] = {}

    def progress(msg: str) -> None:
        jobs.log(job_id, msg)

    # 1 — download -----------------------------------------------------------
    jobs.log(job_id, f"Downloading {len(req.accessions)} record(s) from NCBI",
             progress=8, stage="download", tool="NCBI efetch")
    t0 = time.time()
    records, failures = ncbi.fetch_sequences(req.accessions, req.ranges,
                                             progress=progress)
    timings["download_s"] = round(time.time() - t0, 1)
    if failures:
        jobs.log(job_id, f"{len(failures)} record(s) could not be used")
    if not records:
        detail = "; ".join(f"{f['accession']}: {f['reason']}" for f in failures[:6])
        raise RuntimeError(
            f"None of the {len(req.accessions)} selected records yielded a "
            f"sequence. Reasons: {detail or 'no response from NCBI'}. "
            "Next steps: pick records with a concrete accession (WGS master "
            "accessions ending in many zeros contain no sequence), or use the "
            "sequence/BLAST search mode, which returns coordinate-bearing hits."
        )
    if len(records) < 2:
        jobs.log(job_id, "Only one usable sequence — MAFFT will be skipped and "
                         "the whole sequence is treated as conserved.")

    seq_records = [{"label": r["accession"], "sequence": r["sequence"],
                    "header": r["header"], "length": r["length"]}
                   for r in records]

    query_seq = clean_sequence(req.query_sequence) if req.query_sequence else ""
    if query_seq:
        seq_records.insert(0, {"label": req.query_label, "sequence": query_seq,
                               "header": "user-supplied query sequence",
                               "length": len(query_seq)})
        jobs.log(job_id, f"Query sequence added to the alignment "
                         f"({len(query_seq):,} bp)")

    # 2 — trim to the homologous core ---------------------------------------
    trim_dropped: list[dict[str, str]] = []
    if req.trim_to_homology and len(seq_records) > 1:
        jobs.log(job_id, "Trimming records to the region homologous to the anchor",
                 progress=18, stage="trim", tool="blastn (local)")
        anchor, anchor_name = homology.pick_anchor(
            seq_records, query_seq, workdir=workdir, progress=progress)
        seq_records, trim_dropped = homology.trim_to_anchor(
            seq_records, anchor, anchor_name, workdir,
            flank=req.anchor_flank, progress=progress)
        for d in trim_dropped:
            jobs.log(job_id, f"dropped {d['accession']}: {d['reason']}")
        if len(seq_records) < 2:
            raise RuntimeError(
                f"After homology trimming only {len(seq_records)} record(s) "
                f"remained (anchor: {anchor_name}). The selected records may not "
                "share a homologous region. Next steps: turn off homology "
                "trimming to align them at full length, pick records from a "
                "narrower taxonomic range, or use the sequence/BLAST search "
                "mode so the anchor is your own gene."
            )

    fasta_path = workdir / "input.fasta"
    with fasta_path.open("w") as fh:
        for rec in seq_records:
            fh.write(f">{rec['label']} {rec.get('header', '')}\n{rec['sequence']}\n")

    # 3 — align --------------------------------------------------------------
    jobs.log(job_id, f"Aligning {len(seq_records)} sequences with MAFFT",
             progress=25, stage="align", tool="MAFFT")
    t0 = time.time()
    aligned = alignment.run_mafft(seq_records, progress=progress, workdir=workdir)
    timings["mafft_s"] = round(time.time() - t0, 1)
    aln_len = len(aligned[0]["aligned"])
    jobs.log(job_id, f"Alignment: {len(aligned)} sequences x {aln_len:,} columns",
             progress=45)

    with (workdir / "aligned.fasta").open("w") as fh:
        for rec in aligned:
            fh.write(f">{rec['label']}\n{rec['aligned']}\n")

    # 4 — conservation -------------------------------------------------------
    jobs.log(job_id, "Scoring conservation per alignment column",
             progress=52, stage="conservation", tool="conservation scan")

    # Records that barely overlap the target would veto every conserved column
    # under a strict gap threshold. They are excluded from the conservation
    # calculation but stay in the alignment and are still validated against, so
    # nothing is hidden — the coverage table shows them as mismatching.
    coverage = alignment.record_coverage(aligned)
    min_cov = req.conservation.min_record_coverage
    keep_labels = {c["label"] for c in coverage if c["coverage"] >= min_cov}
    low_coverage = [c for c in coverage if c["coverage"] < min_cov]
    conservation_set = [r for r in aligned if r["label"] in keep_labels]

    for c in low_coverage:
        jobs.log(job_id, f"{c['label']} covers only {100 * c['coverage']:.1f}% of "
                         f"the alignment ({c['aligned_bp']:,} bp) — excluded from "
                         "the conservation calculation, still validated against")

    if len(conservation_set) < 2:
        table = "; ".join(f"{c['label']} {100 * c['coverage']:.0f}%" for c in coverage)
        raise RuntimeError(
            f"Only {len(conservation_set)} record(s) cover at least "
            f"{100 * min_cov:.0f}% of the alignment, which is too few to call a "
            f"conserved region. Per-record coverage: {table}. Next steps: lower "
            "the minimum record coverage, deselect the truncated records, or "
            "search for longer records (raise the minimum length filter)."
        )

    profile = alignment.conservation_profile([r["aligned"] for r in conservation_set])
    reference = alignment.build_reference(
        conservation_set, profile, mode=req.conservation.reference,
        query_label=req.query_label if query_seq else None)

    blocks = alignment.conserved_blocks(
        profile, reference,
        identity_threshold=req.conservation.identity_threshold,
        max_gap_fraction=req.conservation.max_gap_fraction,
        min_block_length=req.conservation.min_block_length,
    )
    ref_seq = reference["sequence"]
    excluded = alignment.variable_regions(blocks, len(ref_seq))

    conserved_bp = sum(b["length"] for b in blocks)
    jobs.log(job_id,
             f"{len(blocks)} conserved block(s), {conserved_bp:,} bp "
             f"({100 * conserved_bp / max(len(ref_seq), 1):.1f}% of the "
             f"{len(ref_seq):,} bp reference '{reference['label']}')",
             progress=60)

    if not blocks:
        longest = _longest_conserved_run(
            profile, reference, req.conservation.identity_threshold,
            req.conservation.max_gap_fraction)
        divergence = _divergence_table(conservation_set, profile)
        raise RuntimeError(
            "No conserved block long enough was found. Tried: identity >= "
            f"{req.conservation.identity_threshold:.2f}, gap fraction <= "
            f"{req.conservation.max_gap_fraction:.2f}, minimum length "
            f"{req.conservation.min_block_length} bp over "
            f"{len(conservation_set)} sequences. The longest run that did pass "
            f"identity and gap was {longest} bp. Per-sequence divergence from "
            f"the consensus: {divergence}. Next steps: drop the most divergent "
            "sequence(s) listed above, lower the identity threshold, allow a "
            "higher gap fraction, or shorten the minimum block length."
        )

    # 5 — Primer3 ------------------------------------------------------------
    jobs.log(job_id, f"Primer3 ({req.primer3.mode}) on the reference, "
                     f"{len(excluded)} variable region(s) excluded",
             progress=68, stage="primer3", tool="Primer3")
    t0 = time.time()
    p3 = primer3_runner.run_primer3(ref_seq, req.gene_label, req.primer3, excluded)
    timings["primer3_s"] = round(time.time() - t0, 1)
    (workdir / "primer3_input.txt").write_text(p3["boulder_input"])

    pairs = p3["pairs"]
    jobs.log(job_id, f"Primer3 returned {len(pairs)} pair(s)", progress=78)
    if not pairs:
        feasible = _feasible_product_range(blocks, req.primer3.primer_min_size)
        layout = "; ".join(f"{b['ref_start'] + 1}-{b['ref_end'] + 1} "
                           f"({b['length']} bp)" for b in blocks[:8])
        if feasible is None:
            window = (
                "that layout allows no product at all: no conserved block is "
                f"long enough to hold two {req.primer3.primer_min_size} nt "
                "primers, and there is no pair of blocks a product could span"
            )
        else:
            window = (f"that layout allows products of roughly {feasible[0]}-"
                      f"{feasible[1]} bp, while "
                      f"{req.primer3.product_min}-{req.primer3.product_max} bp "
                      "was requested")
        raise RuntimeError(
            "Primer3 found no pair under these constraints. "
            f"{_diagnose_primer3(p3['explain'], req)} "
            f"Conserved blocks on the reference: {layout}"
            f"{' …' if len(blocks) > 8 else ''}; {window}. "
            "Primer3's own counters: "
            f"left[{p3['explain']['left']}] right[{p3['explain']['right']}] "
            f"pair[{p3['explain']['pair']}]."
        )

    # 6 — validation ---------------------------------------------------------
    jobs.log(job_id, "Checking every oligo against every sequence",
             progress=84, stage="validate", tool="in-silico validation")
    t0 = time.time()
    for pair in pairs:
        pair["binding"] = {
            "forward": validation.oligo_binding(pair["forward"], reference, aligned),
            "reverse": validation.oligo_binding(pair["reverse"], reference, aligned),
        }
        if pair.get("probe"):
            pair["binding"]["probe"] = validation.oligo_binding(
                pair["probe"], reference, aligned)
        pair["amplicons"] = validation.amplicon_per_sequence(pair, reference, aligned)
        pair["geometry"] = validation.pair_geometry(pair)
        pair["order"] = validation.order_sequences(pair)
        pair["block_hits"] = {
            "forward": _block_of(pair["forward"], blocks),
            "reverse": _block_of(pair["reverse"], blocks),
        }
        coverage = [pair["binding"]["forward"], pair["binding"]["reverse"]]
        if "probe" in pair["binding"]:
            coverage.append(pair["binding"]["probe"])
        pair["coverage_percent"] = round(
            min(c["perfect_percent"] for c in coverage), 1)

    specificity: dict[str, Any] = {"available": False, "reason": "not requested"}
    if req.specificity_check:
        jobs.log(job_id, "Local BLAST specificity check against the downloaded set",
                 progress=90, tool="blastn-short (local)")
        oligos: list[dict[str, str]] = []
        for pair in pairs:
            oligos += validation.build_oligo_list(pair, f"pair{pair['rank']}")
        specificity = validation.local_specificity(oligos, records, workdir)
        if specificity.get("available"):
            by_name = {o["oligo"]: o for o in specificity["oligos"]}
            for pair in pairs:
                pair["specificity"] = {
                    role: by_name.get(f"pair{pair['rank']}_{tag}")
                    for role, tag in (("forward", "F"), ("reverse", "R"),
                                      ("probe", "P"))
                    if by_name.get(f"pair{pair['rank']}_{tag}")
                }
    timings["validation_s"] = round(time.time() - t0, 1)

    # 7 — assemble -----------------------------------------------------------
    result = {
        "gene_label": req.gene_label,
        "settings": {"primer3": req.primer3.model_dump(),
                     "conservation": req.conservation.model_dump()},
        "records": [{k: r[k] for k in
                     ("accession", "header", "length", "fetched_range")}
                    for r in records],
        "download_failures": failures,
        "trim_dropped": trim_dropped,
        "record_coverage": coverage,
        "low_coverage_records": low_coverage,
        "conservation_n": len(conservation_set),
        "query_included": bool(query_seq),
        "alignment": {
            "labels": [r["label"] for r in aligned],
            "sequences": [r["aligned"] for r in aligned],
            "reversed": [bool(r.get("reversed")) for r in aligned],
            "n": len(aligned),
            "length": aln_len,
        },
        "reference": reference,
        "profile": {
            "identity": [c["identity"] for c in profile],
            "gap": [c["gap_fraction"] for c in profile],
            "entropy": [c["entropy"] for c in profile],
            "major": "".join(c["major"] for c in profile),
            "code": "".join(c["code"] for c in profile),
        },
        "blocks": blocks,
        "excluded_regions": excluded,
        "conserved_bp": conserved_bp,
        "pairs": pairs,
        "primer3_explain": p3["explain"],
        "primer3_warning": p3["warning"],
        "specificity": specificity,
        "tool_versions": config.tool_versions(),
        "timings": {**timings, "total_s": round(time.time() - started, 1)},
        "workdir": str(workdir),
    }

    (workdir / "results.json").write_text(json.dumps(result, indent=2))
    _write_primer_tsv(workdir / "primers.tsv", req.gene_label, pairs)
    jobs.log(job_id, f"Done in {result['timings']['total_s']}s — outputs in {workdir}",
             progress=100)
    return result


def _diagnose_primer3(explain: dict[str, str], req: DesignRequest) -> str:
    """Name the constraint that actually blocked the design.

    Primer3's pair counters make the real blocker easy to miss: a run can
    report tens of thousands of "unacceptable product size" simply because most
    combinations are the wrong length, while the reason no *valid-length* pair
    survived is something else entirely — most often the qPCR probe.
    """
    # Primer3 writes each line as "considered 66610, unacceptable product size
    # 61111, no internal oligo 5499, ok 0".
    def parse(line: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for chunk in line.split(","):
            match = re.match(r"\s*(.+?)\s+(\d+)\s*$", chunk)
            if match:
                out[match.group(1).strip().lower()] = int(match.group(2))
        return out

    counts = parse(explain.get("pair", ""))
    left = parse(explain.get("left", ""))
    right = parse(explain.get("right", ""))

    def got(key: str, table: dict[str, int] | None = None) -> int:
        table = counts if table is None else table
        return sum(v for k, v in table.items() if key in k)

    # When no individual primer survived, the pair stage never ran ("considered
    # 0") and its counters say nothing; the reason lives in the left/right
    # lines. Two different failures hide there and must not be confused:
    # candidates rejected for falling outside a conserved block ("overlap
    # excluded region"), and candidates that did fit but failed Tm/GC. Counting
    # only the first leads to "no primer could fit", which is wrong whenever the
    # block is long enough to hold one — the block's base composition is then
    # the real blocker.
    if got("considered") == 0:
        considered = got("considered", left) + got("considered", right)
        excluded = got("overlap excluded region", left) + got("overlap excluded region", right)
        gc_fail = got("gc content failed", left) + got("gc content failed", right)
        tm_fail = (got("low tm", left) + got("high tm", left)
                   + got("low tm", right) + got("high tm", right))
        inside = considered - excluded

        if considered == 0:
            return ("Primer3 considered no candidate at all, which normally means "
                    "the template or the excluded regions were malformed.")
        if inside <= 0:
            return (
                f"No conserved block can hold a primer: all {considered:,} "
                "candidates overlapped a variable region. Next steps: lower the "
                "conservation identity threshold or deselect the most divergent "
                "sequences so the blocks grow — with N sequences a threshold "
                "above (N-1)/N still demands that every one of them agree, so on "
                "a large set 0.98 behaves exactly like 1.00."
            )

        worst, label, advice = max(
            ((gc_fail, f"the GC window ({req.primer3.primer_min_gc}-"
                       f"{req.primer3.primer_max_gc}%)",
              "widen the GC window, or pick a conserved block whose base "
              "composition is closer to the rest of the target"),
             (tm_fail, f"the Tm window ({req.primer3.primer_min_tm}-"
                       f"{req.primer3.primer_max_tm} °C)",
              "widen the Tm window, or allow longer primers so a low-GC block "
              "can still reach the required Tm")),
            key=lambda x: x[0])

        # Report both counters, not just the dominant one: on a short block the
        # second filter usually bites as soon as the first is widened, and a
        # message naming only the leader sends the user round the loop twice.
        return (
            f"{inside:,} candidate primers did fit inside a conserved block, so "
            "the blocks are not too short — but every one of them was rejected: "
            f"{gc_fail:,} on the GC window ({req.primer3.primer_min_gc}-"
            f"{req.primer3.primer_max_gc}%) and {tm_fail:,} on the Tm window "
            f"({req.primer3.primer_min_tm}-{req.primer3.primer_max_tm} °C). The "
            "conserved sequence itself is the problem, not its length "
            f"({excluded:,} further candidates fell outside the blocks). Widening "
            f"{label} alone may not be enough — on a short block the other filter "
            "usually rejects whatever survives. Next steps: " + advice + "; or "
            "lower the conservation identity threshold so longer, more "
            "representative blocks become available."
        )

    size_fail = got("unacceptable product size")
    probe_fail = got("no internal oligo")
    compl_fail = got("high compl")
    tm_fail = got("tm diff too large")

    if probe_fail and probe_fail >= size_fail * 0.05:
        return (
            f"{probe_fail:,} primer pair(s) had an acceptable product size but no "
            "hydrolysis probe could be placed between them. The probe constraints "
            f"are the blocker, not the primers: probe Tm "
            f"{req.primer3.probe_min_tm}-{req.primer3.probe_max_tm} °C, length "
            f"{req.primer3.probe_min_size}-{req.primer3.probe_max_size} nt. Next "
            "steps: widen the probe Tm window, allow a longer probe, or switch to "
            "standard PCR mode if a probe is not required."
        )
    if compl_fail and compl_fail >= size_fail * 0.05:
        return (
            f"{compl_fail:,} pair(s) were rejected for primer-dimer "
            "complementarity. Next steps: raise the pair complementarity limits "
            "or widen the product size range so other combinations become "
            "available."
        )
    if tm_fail:
        return (f"{tm_fail:,} pair(s) were rejected for a too-large Tm difference "
                "between the two primers. Next steps: widen the Tm window.")
    if size_fail:
        return (
            f"Every candidate ({size_fail:,}) was rejected on product size alone, "
            "so the primers themselves are fine — the requested amplicon length "
            "does not fit the conserved-block layout. Next steps: move the "
            "product size range, or lower the conservation identity threshold so "
            "the blocks grow and move closer together."
        )
    return ("No single dominant reason: too few individual primers passed the "
            "Tm/GC/hairpin filters. Next steps: relax Tm and GC first.")


def _longest_conserved_run(profile: list[dict[str, Any]], reference: dict[str, Any],
                           identity_threshold: float, max_gap_fraction: float) -> int:
    """Longest run of columns passing the thresholds, ignoring the length cut-off."""
    mapped = set(reference["col_of_pos"])
    best = run = 0
    for col_idx, col in enumerate(profile):
        passes = (col["gap_fraction"] <= max_gap_fraction
                  and col["identity"] >= identity_threshold
                  and col["major"] in "ACGT"
                  and col_idx in mapped)
        run = run + 1 if passes else 0
        best = max(best, run)
    return best


def _divergence_table(aligned: list[dict[str, str]],
                      profile: list[dict[str, Any]]) -> str:
    """Mismatches against the consensus per sequence, worst first."""
    consensus = "".join(c["major"] for c in profile)
    rows = []
    for rec in aligned:
        seq = rec["aligned"]
        mism = sum(1 for a, b in zip(seq, consensus)
                   if a != "-" and b in "ACGT" and a != b)
        rows.append((mism, rec["label"]))
    rows.sort(reverse=True)
    return "; ".join(f"{label} {mism} mismatch" for mism, label in rows[:8])


def _feasible_product_range(blocks: list[dict[str, Any]],
                            min_primer: int) -> tuple[int, int] | None:
    """Smallest and largest amplicon the conserved-block layout allows.

    The forward primer must fit inside one block and the reverse inside the
    same or a later one, so the shortest product puts both primers as close to
    the facing block edges as their length permits.

    Returns None when no product is possible at all — a single block shorter
    than two primers, for instance. Reporting a range in that case would print
    an impossible window such as "36-28 bp".
    """
    ordered = sorted(blocks, key=lambda b: b["ref_start"])
    smallest = None
    for i, a in enumerate(ordered):
        for b in ordered[i:]:
            if b is a:
                if a["length"] < 2 * min_primer:
                    continue          # one block cannot hold both primers
                candidate = 2 * min_primer
            else:
                candidate = b["ref_start"] - a["ref_end"] + 2 * min_primer
            if smallest is None or candidate < smallest:
                smallest = candidate
    if smallest is None:
        return None
    largest = ordered[-1]["ref_end"] - ordered[0]["ref_start"] + 1
    return (smallest, largest) if smallest <= largest else None


def _block_of(oligo: dict[str, Any], blocks: list[dict[str, Any]]) -> int | None:
    for b in blocks:
        if b["ref_start"] <= oligo["start"] and oligo["end"] <= b["ref_end"]:
            return b["rank"]
    return None


def _write_primer_tsv(path: Path, gene: str, pairs: list[dict[str, Any]]) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t")
        writer.writerow([
            "gene", "pair", "role", "sequence_5_3", "length", "ref_start_1based",
            "ref_end_1based", "strand", "tm_c", "gc_percent", "hairpin_th_c",
            "self_any_th_c", "self_end_th_c", "product_size_bp",
            "perfect_match_percent", "n_sequences",
        ])
        for pair in pairs:
            for role in ("forward", "reverse", "probe"):
                oligo = pair.get(role)
                if not oligo:
                    continue
                binding = pair["binding"].get(role, {})
                writer.writerow([
                    gene, pair["rank"], role, oligo["sequence"], oligo["length"],
                    oligo["start"] + 1, oligo["end"] + 1, oligo["strand"],
                    oligo["tm"], oligo["gc_percent"], oligo["hairpin_th"],
                    oligo["self_any_th"], oligo["self_end_th"],
                    pair["product_size"], binding.get("perfect_percent"),
                    binding.get("n_sequences"),
                ])
