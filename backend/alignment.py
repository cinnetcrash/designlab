"""MAFFT alignment, per-column conservation and conserved-block detection."""
from __future__ import annotations

import math
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

import config
from sequtils import degenerate_code, parse_fasta

Progress = Callable[[str], None]

BASES = ("A", "C", "G", "T")


# ─── MAFFT ───────────────────────────────────────────────────────────────────

def run_mafft(records: list[dict[str, Any]],
              progress: Progress = lambda _m: None,
              workdir: Path | None = None) -> list[dict[str, str]]:
    """Align records ([{label, sequence}, ...]) with MAFFT.

    `--adjustdirection` is essential here: NCBI contigs carry a gene on either
    strand, and MAFFT would otherwise align a plus-strand copy against a
    minus-strand one and produce a meaningless alignment with no conserved
    columns at all. Flipped sequences come back with an `_R_` name prefix,
    which is how they are detected and reported.

    A single record is returned unchanged (nothing to align).
    """
    if len(records) == 1:
        return [{"label": records[0]["label"],
                 "aligned": records[0]["sequence"], "reversed": False}]

    workdir = workdir or Path(tempfile.mkdtemp(prefix="pd_"))
    workdir.mkdir(parents=True, exist_ok=True)
    in_path = workdir / "input.fasta"
    out_path = workdir / "aligned.fasta"

    with in_path.open("w") as fh:
        for i, rec in enumerate(records):
            fh.write(f">s{i}\n{rec['sequence']}\n")

    progress(f"MAFFT --auto --adjustdirection on {len(records)} sequences")
    cmd = config.tool_argv(config.MAFFT_BIN, "--auto", "--adjustdirection",
                           "--quiet", "--preservecase", str(in_path))
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=config.MAFFT_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError(f"MAFFT failed: {proc.stderr.strip()[:400]}")
    out_path.write_text(proc.stdout)

    aligned_pairs = parse_fasta(proc.stdout)
    if len(aligned_pairs) != len(records):
        raise RuntimeError(
            f"MAFFT returned {len(aligned_pairs)} sequences for "
            f"{len(records)} inputs; alignment aborted."
        )

    by_index: dict[int, tuple[str, bool]] = {}
    for header, seq in aligned_pairs:
        name = header.split()[0]
        flipped = name.startswith("_R_")
        if flipped:
            name = name[3:]
        by_index[int(name.lstrip("s"))] = (seq.upper(), flipped)

    n_flipped = sum(1 for _, f in by_index.values() if f)
    if n_flipped:
        progress(f"{n_flipped} sequence(s) were reverse-complemented by MAFFT "
                 "to match the others' orientation")

    return [{"label": records[i]["label"],
             "aligned": by_index[i][0],
             "reversed": by_index[i][1]}
            for i in range(len(records))]


# ─── Conservation ────────────────────────────────────────────────────────────

def conservation_profile(aligned: list[str]) -> list[dict[str, Any]]:
    """Per alignment column: majority base, identity, gap fraction, entropy."""
    if not aligned:
        return []
    n = len(aligned)
    length = len(aligned[0])
    profile: list[dict[str, Any]] = []

    for col_idx in range(length):
        column = [s[col_idx] for s in aligned]
        gaps = sum(1 for b in column if b == "-")
        counts = {b: 0 for b in BASES}
        other = 0
        for b in column:
            if b in counts:
                counts[b] += 1
            elif b != "-":
                other += 1                      # N or another ambiguity code
        informative = sum(counts.values())
        if informative:
            major = max(counts, key=lambda b: counts[b])
            identity = counts[major] / informative
            entropy = -sum(
                (c / informative) * math.log2(c / informative)
                for c in counts.values() if c
            )
            observed = {b for b in BASES if counts[b]}
        else:
            major, identity, entropy, observed = "-", 0.0, 0.0, set()

        profile.append({
            "major": major,
            "identity": round(identity, 4),
            "gap_fraction": round(gaps / n, 4),
            "ambiguous": other,
            "entropy": round(entropy, 4),
            "code": degenerate_code(observed) if observed else "-",
        })
    return profile


def record_coverage(aligned_records: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Fraction of the alignment each record actually covers.

    NCBI contigs are often cut off mid-gene, so a record may overlap the target
    by only a fraction of its length. Such a record is nearly all gaps in the
    alignment and, under a strict gap threshold, single-handedly vetoes every
    conserved column — which is why coverage is measured before conservation is
    scored.
    """
    if not aligned_records:
        return []
    length = len(aligned_records[0]["aligned"])
    out = []
    for rec in aligned_records:
        ungapped = sum(1 for b in rec["aligned"] if b != "-")
        out.append({
            "label": rec["label"],
            "aligned_bp": ungapped,
            "coverage": round(ungapped / length, 4) if length else 0.0,
        })
    return out


def build_reference(aligned_records: list[dict[str, str]],
                    profile: list[dict[str, Any]],
                    mode: str = "consensus",
                    query_label: str | None = None) -> dict[str, Any]:
    """Build the ungapped reference the primer coordinates are reported against.

    Returns the sequence plus `col_of_pos`, mapping every reference position to
    its alignment column, so a primer found on the reference can be drawn on
    the alignment and read off every other sequence.
    """
    if mode == "consensus":
        seq_chars: list[str] = []
        col_of_pos: list[int] = []
        for col_idx, col in enumerate(profile):
            if col["gap_fraction"] > 0.5 or col["major"] == "-":
                continue                       # column is a gap in most sequences
            seq_chars.append(col["major"])
            col_of_pos.append(col_idx)
        label = "CONSENSUS"
    else:
        if mode == "query" and query_label:
            source = next((r for r in aligned_records
                           if r["label"] == query_label), aligned_records[0])
        else:
            source = aligned_records[0]
        seq_chars, col_of_pos = [], []
        for col_idx, base in enumerate(source["aligned"]):
            if base == "-":
                continue
            seq_chars.append(base)
            col_of_pos.append(col_idx)
        label = source["label"]

    return {
        "label": label,
        "mode": mode,
        "sequence": "".join(seq_chars),
        "col_of_pos": col_of_pos,
    }


def conserved_blocks(profile: list[dict[str, Any]], reference: dict[str, Any],
                     identity_threshold: float, max_gap_fraction: float,
                     min_block_length: int) -> list[dict[str, Any]]:
    """Contiguous runs of conserved columns, expressed in reference coordinates.

    A column is conserved when the majority base is carried by at least
    `identity_threshold` of the informative sequences and the gap fraction
    stays at or below `max_gap_fraction`.
    """
    pos_of_col: dict[int, int] = {c: p for p, c in enumerate(reference["col_of_pos"])}

    def ok(col: dict[str, Any]) -> bool:
        return (col["gap_fraction"] <= max_gap_fraction
                and col["identity"] >= identity_threshold
                and col["major"] in BASES)

    blocks: list[dict[str, Any]] = []
    run_start: int | None = None

    for col_idx in range(len(profile) + 1):
        conserved = col_idx < len(profile) and ok(profile[col_idx])
        # A conserved column outside the reference (e.g. an insertion the
        # consensus dropped) breaks the run — coordinates must stay mappable.
        if conserved and col_idx not in pos_of_col:
            conserved = False
        if conserved and run_start is None:
            run_start = col_idx
        elif not conserved and run_start is not None:
            _append_block(blocks, run_start, col_idx, pos_of_col, min_block_length)
            run_start = None

    blocks.sort(key=lambda b: -b["length"])
    for rank, b in enumerate(blocks, 1):
        b["rank"] = rank
    return sorted(blocks, key=lambda b: b["ref_start"])


def _append_block(blocks: list[dict[str, Any]], col_start: int, col_end: int,
                  pos_of_col: dict[int, int], min_block_length: int) -> None:
    ref_start = pos_of_col[col_start]
    ref_end = pos_of_col[col_end - 1]
    length = ref_end - ref_start + 1
    if length < min_block_length:
        return
    blocks.append({
        "col_start": col_start,
        "col_end": col_end,            # exclusive
        "ref_start": ref_start,        # 0-based, inclusive
        "ref_end": ref_end,            # 0-based, inclusive
        "length": length,
    })


def variable_regions(blocks: list[dict[str, Any]], ref_length: int) -> list[list[int]]:
    """Complement of the conserved blocks, as [start, length] in reference coords.

    Primer3 receives these as SEQUENCE_EXCLUDED_REGION, so primers land only in
    conserved blocks while the amplicon interior may still vary.
    """
    excluded: list[list[int]] = []
    cursor = 0
    for b in sorted(blocks, key=lambda x: x["ref_start"]):
        if b["ref_start"] > cursor:
            excluded.append([cursor, b["ref_start"] - cursor])
        cursor = b["ref_end"] + 1
    if cursor < ref_length:
        excluded.append([cursor, ref_length - cursor])
    return excluded
