"""Trim downloaded records to the region homologous to an anchor sequence.

A gene-name search returns whole contigs: the gene of interest plus however
much unrelated flanking sequence the assembly happened to carry. Aligning those
end to end produces an alignment whose conserved columns are scattered in
9-base fragments — useless for primer design. Every record is therefore cut back
to the part that actually matches the anchor, and records with no match are
dropped with the reason recorded rather than silently aligned.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any, Callable

import config
from sequtils import revcomp

logger = logging.getLogger("homology")
Progress = Callable[[str], None]

MIN_BITSCORE = 50.0


MIN_CORE_BP = 120          # below this the "shared core" is not worth trimming to
CORE_MERGE_GAP = 40        # bridge short dips in coverage inside the core


def pick_anchor(records: list[dict[str, Any]], query_sequence: str = "",
                workdir: Path | None = None,
                progress: Progress = lambda _m: None,
                min_fraction: float = 0.5) -> tuple[str, str]:
    """Anchor = the pasted query, else the region the records actually share.

    Two heuristics were tried first and both fail on real NCBI records:

    * shortest record — may share only a few hundred bases with the rest, so
      everything else gets trimmed to that sliver or dropped as "no match";
    * highest total bitscore — biased straight to the *longest* contig, which
      brings back all the unrelated flanking sequence trimming exists to remove.

    So the anchor is built instead of picked: take the record that matches the
    most others, then keep only the part of it that is covered by at least
    `min_fraction` of them. That region is the shared core, which is what the
    alignment and the conserved-block search actually need.
    """
    if query_sequence:
        return query_sequence, "girilen dizi"
    if len(records) < 3 or workdir is None:
        shortest = min(records, key=lambda r: len(r["sequence"]))
        return shortest["sequence"], shortest["label"]

    hits = _all_vs_all(records, workdir)
    if not hits:
        shortest = min(records, key=lambda r: len(r["sequence"]))
        progress("Anchor selection fell back to the shortest record "
                 "(all-vs-all BLAST produced no hits)")
        return shortest["sequence"], shortest["label"]

    # Breadth first (how many other records match it), shorter record wins ties.
    partners: dict[int, set[int]] = {}
    for (q, s) in hits:
        partners.setdefault(s, set()).add(q)
    anchor_idx = min(
        range(len(records)),
        key=lambda i: (-len(partners.get(i, set())), len(records[i]["sequence"])),
    )
    anchor_rec = records[anchor_idx]
    anchor_seq = anchor_rec["sequence"]

    # Per-base coverage of the anchor by the other records.
    coverage = [0] * (len(anchor_seq) + 1)
    for (q, s), intervals in hits.items():
        if s != anchor_idx or q == anchor_idx:
            continue
        covered = set()
        for lo, hi in intervals:
            covered.update(range(max(1, lo), min(len(anchor_seq), hi) + 1))
        for pos in covered:
            coverage[pos] += 1

    others = len(records) - 1
    needed = max(1, int(round(min_fraction * others)))
    keep = [pos for pos in range(1, len(anchor_seq) + 1) if coverage[pos] >= needed]
    core = _longest_run(keep, CORE_MERGE_GAP)

    if not core or core[1] - core[0] + 1 < MIN_CORE_BP:
        progress(f"Anchor: {anchor_rec['label']} (whole record — no region was "
                 f"covered by at least {needed} of the other {others})")
        return anchor_seq, anchor_rec["label"]

    lo, hi = core
    label = f"{anchor_rec['label']}:{lo}-{hi}"
    progress(f"Anchor: {label} — {hi - lo + 1} bp shared by at least {needed} "
             f"of the other {others} record(s)")
    return anchor_seq[lo - 1:hi], label


def _longest_run(positions: list[int], merge_gap: int) -> tuple[int, int] | None:
    """Longest run of positions, bridging gaps of up to `merge_gap` bases."""
    if not positions:
        return None
    best = (positions[0], positions[0])
    start = prev = positions[0]
    for pos in positions[1:]:
        if pos - prev > merge_gap:
            if prev - start > best[1] - best[0]:
                best = (start, prev)
            start = pos
        prev = pos
    if prev - start > best[1] - best[0]:
        best = (start, prev)
    return best


def _all_vs_all(records: list[dict[str, Any]],
                workdir: Path) -> dict[tuple[int, int], list[tuple[int, int]]]:
    """All-vs-all BLAST; HSP intervals on the subject, keyed by (query, subject)."""
    workdir.mkdir(parents=True, exist_ok=True)
    fasta = workdir / "anchor_all.fasta"
    with fasta.open("w") as fh:
        for i, rec in enumerate(records):
            fh.write(f">r{i}\n{rec['sequence']}\n")
    try:
        subprocess.run(
            [config.MAKEBLASTDB_BIN, "-in", str(fasta), "-dbtype", "nucl",
             "-out", str(workdir / "anchordb")],
            capture_output=True, text=True, check=True,
            timeout=config.BLAST_LOCAL_TIMEOUT)
        proc = subprocess.run(
            [config.BLASTN_BIN, "-task", "blastn", "-query", str(fasta),
             "-db", str(workdir / "anchordb"), "-evalue", "1e-10",
             "-outfmt", "6 qseqid sseqid sstart send bitscore",
             "-max_target_seqs", str(len(records) * 2), "-num_threads", "2"],
            capture_output=True, text=True, timeout=config.BLAST_LOCAL_TIMEOUT)
    except subprocess.SubprocessError as exc:
        logger.warning("all-vs-all BLAST failed: %s", exc)
        return {}

    out: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        try:
            q, s = int(parts[0][1:]), int(parts[1][1:])
            sstart, send = int(parts[2]), int(parts[3])
            bits = float(parts[4])
        except ValueError:
            continue
        if q == s or bits < MIN_BITSCORE:
            continue
        out.setdefault((q, s), []).append((min(sstart, send), max(sstart, send)))
    return out


def trim_to_anchor(records: list[dict[str, Any]], anchor: str, anchor_name: str,
                   workdir: Path, flank: int = 60,
                   progress: Progress = lambda _m: None,
                   ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """BLAST the anchor against every record and cut each to its matching span."""
    if len(records) < 2:
        return records, []

    workdir.mkdir(parents=True, exist_ok=True)
    db_fasta = workdir / "trim_db.fasta"
    q_fasta = workdir / "trim_anchor.fasta"
    with db_fasta.open("w") as fh:
        for i, rec in enumerate(records):
            fh.write(f">r{i}\n{rec['sequence']}\n")
    q_fasta.write_text(f">anchor\n{anchor}\n")

    try:
        subprocess.run(
            [config.MAKEBLASTDB_BIN, "-in", str(db_fasta), "-dbtype", "nucl",
             "-out", str(workdir / "trimdb")],
            capture_output=True, text=True, check=True,
            timeout=config.BLAST_LOCAL_TIMEOUT)
        proc = subprocess.run(
            [config.BLASTN_BIN, "-task", "blastn", "-query", str(q_fasta),
             "-db", str(workdir / "trimdb"), "-evalue", "1e-20",
             "-outfmt", "6 sseqid sstart send pident length bitscore",
             "-max_target_seqs", str(len(records) * 5), "-num_threads", "2"],
            capture_output=True, text=True, timeout=config.BLAST_LOCAL_TIMEOUT)
    except subprocess.SubprocessError as exc:
        progress(f"Homology trimming skipped: BLAST failed ({exc}). "
                 "Records are aligned at full length.")
        return records, []

    hsps: dict[int, list[dict[str, Any]]] = {}
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 6:
            continue
        try:
            idx = int(parts[0].lstrip("r"))
            sstart, send = int(parts[1]), int(parts[2])
            bitscore = float(parts[5])
        except ValueError:
            continue
        if bitscore < MIN_BITSCORE:
            continue
        hsps.setdefault(idx, []).append({
            "lo": min(sstart, send), "hi": max(sstart, send),
            "strand": "+" if sstart <= send else "-", "bitscore": bitscore,
        })

    span_limit = int(len(anchor) * 1.5) + 2 * flank
    trimmed: list[dict[str, Any]] = []
    dropped: list[dict[str, str]] = []

    for i, rec in enumerate(records):
        hits = hsps.get(i)
        if not hits:
            dropped.append({
                "accession": rec["label"],
                "reason": f"no BLAST match to the anchor ({anchor_name}) at "
                          f"evalue 1e-20 / bitscore >= {MIN_BITSCORE:.0f}",
            })
            continue

        best = max(hits, key=lambda h: h["bitscore"])
        # Merge only HSPs on the same strand and close to the best one; a distant
        # match is a paralogue, not part of this locus.
        group = [h for h in hits
                 if h["strand"] == best["strand"]
                 and h["lo"] < best["hi"] + span_limit
                 and h["hi"] > best["lo"] - span_limit]
        lo = max(1, min(h["lo"] for h in group) - flank)
        hi = min(len(rec["sequence"]), max(h["hi"] for h in group) + flank)

        region = rec["sequence"][lo - 1:hi]
        if best["strand"] == "-":
            region = revcomp(region)

        trimmed.append({
            **rec,
            "sequence": region,
            "length": len(region),
            "trimmed_from": [lo, hi],
            "trim_strand": best["strand"],
            "original_length": len(rec["sequence"]),
        })

    kept = len(trimmed)
    shrunk = sum(1 for r in trimmed if r["length"] < r["original_length"])
    progress(f"Homology trimming against {anchor_name}: {kept}/{len(records)} "
             f"record(s) kept, {shrunk} shortened, {len(dropped)} dropped")
    return trimmed, dropped
