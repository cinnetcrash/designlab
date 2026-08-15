"""In-silico validation: where each oligo binds, how well, and what else it hits.

Everything here is reported per downloaded sequence, because that is what the
question "does this primer actually work on my target group?" comes down to.

Two clearly separated kinds of numbers are produced:

* Base-pairing geometry (mismatch counts, duplex diagrams). Computed here by
  exact comparison over the MAFFT alignment columns. These are counts, not
  predictions.
* Thermodynamics (Tm, hairpin/dimer dG-derived temperatures). Taken from
  Primer3 only. This module never invents a thermodynamic value.
"""
from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import config
from sequtils import WATSON_CRICK, iupac_match, revcomp

logger = logging.getLogger("validation")

THREE_PRIME_WINDOW = 5      # last 5 nt of the 3' end: mispriming-critical zone


# ─── Binding across the aligned sequence set ─────────────────────────────────

def oligo_binding(oligo: dict[str, Any], reference: dict[str, Any],
                  aligned_records: list[dict[str, str]]) -> dict[str, Any]:
    """Read the oligo's site out of every aligned sequence, column by column.

    The oligo sequence is compared on the plus strand of the template, which is
    what the alignment stores. A reverse primer is therefore compared as its
    reverse complement (`template_slice`) — the physical duplex it forms.
    """
    col_of_pos = reference["col_of_pos"]
    start, end = oligo["start"], oligo["end"]
    plus_strand_site = oligo["template_slice"]

    # Reference position -> alignment column, and the expected base there.
    cols = [col_of_pos[p] for p in range(start, end + 1)]
    expected = {col: plus_strand_site[i] for i, col in enumerate(cols)}
    col_lo, col_hi = cols[0], cols[-1]

    # 3'-end window in reference coordinates.
    if oligo["strand"] == "+":
        tp_positions = set(range(max(start, end - THREE_PRIME_WINDOW + 1), end + 1))
    else:
        tp_positions = set(range(start, min(end, start + THREE_PRIME_WINDOW - 1) + 1))
    tp_cols = {col_of_pos[p] for p in tp_positions}

    # Offset of each column along the oligo, so a mismatch can be placed
    # relative to the 3' end without searching the column list again.
    offset_of_col = {col: i for i, col in enumerate(cols)}

    per_sequence: list[dict[str, Any]] = []
    for rec in aligned_records:
        aln = rec["aligned"]
        mismatches: list[dict[str, Any]] = []
        insertions = 0
        observed: list[str] = []
        observed_cols: list[int] = []

        for col in range(col_lo, col_hi + 1):
            base = aln[col]
            if col not in expected:
                if base != "-":
                    insertions += 1
                continue
            observed.append(base)
            observed_cols.append(col)
            exp = expected[col]
            if base == "-":
                kind = "deletion"
            elif iupac_match(base, exp):
                continue
            else:
                kind = "substitution"
            oligo_offset = offset_of_col[col]
            # Distance from the 3' end, counted along the oligo.
            if oligo["strand"] == "+":
                from_three_prime = oligo["length"] - 1 - oligo_offset
            else:
                from_three_prime = oligo_offset
            mismatches.append({
                "column": col,
                "ref_pos": start + oligo_offset,
                "expected": exp,
                "observed": base,
                "kind": kind,
                "from_three_prime": from_three_prime,
                "critical": col in tp_cols,
            })

        per_sequence.append({
            "label": rec["label"],
            "observed": "".join(observed),
            "observed_cols": observed_cols,
            "mismatches": mismatches,
            "n_mismatch": len(mismatches),
            "n_three_prime_mismatch": sum(1 for m in mismatches if m["critical"]),
            "insertions": insertions,
            "perfect": not mismatches and insertions == 0,
        })

    n = len(per_sequence)
    perfect = sum(1 for s in per_sequence if s["perfect"])
    tolerable = sum(1 for s in per_sequence
                    if s["n_mismatch"] <= 2 and s["n_three_prime_mismatch"] == 0)
    return {
        "role": oligo["role"],
        "sequence": oligo["sequence"],
        "plus_strand_site": plus_strand_site,
        "ref_start": start,
        "ref_end": end,
        "col_start": col_lo,
        "col_end": col_hi,
        "per_sequence": per_sequence,
        "n_sequences": n,
        "n_perfect": perfect,
        "perfect_percent": round(100.0 * perfect / n, 1) if n else 0.0,
        "n_tolerable": tolerable,
        "tolerable_percent": round(100.0 * tolerable / n, 1) if n else 0.0,
    }


def amplicon_per_sequence(pair: dict[str, Any], reference: dict[str, Any],
                          aligned_records: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Product length each sequence would actually give, gaps excluded."""
    col_of_pos = reference["col_of_pos"]
    fwd, rev = pair["forward"], pair["reverse"]
    if not (fwd and rev):
        return []
    col_lo = col_of_pos[fwd["start"]]
    col_hi = col_of_pos[rev["end"]]

    out = []
    for rec in aligned_records:
        span = rec["aligned"][col_lo:col_hi + 1]
        length = sum(1 for b in span if b != "-")
        out.append({
            "label": rec["label"],
            "product_size": length,
            "delta": length - (pair["product_size"] or length),
        })
    return out


# ─── Duplex / hairpin geometry ───────────────────────────────────────────────

def best_duplex(top: str, bottom_rev: str, min_pairs: int = 3) -> dict[str, Any] | None:
    """Highest-scoring complementary alignment between two oligos.

    `top` is drawn 5'->3'; `bottom_rev` is the partner drawn 3'->5'. The scan is
    a plain Watson-Crick base-pair count over every offset — a geometric
    picture of which bases could pair, not a free-energy calculation.
    """
    best: dict[str, Any] | None = None
    n, m = len(top), len(bottom_rev)

    for offset in range(-(m - 1), n):
        pairs, run, best_run = 0, 0, 0
        three_prime_pairs = 0
        for i in range(n):
            j = i - offset
            if not (0 <= j < m):
                run = 0
                continue
            if (top[i].upper(), bottom_rev[j].upper()) in WATSON_CRICK:
                pairs += 1
                run += 1
                best_run = max(best_run, run)
                if i >= n - 3:                 # pairing at the 3' end of `top`
                    three_prime_pairs += 1
            else:
                run = 0
        if pairs < min_pairs:
            continue
        score = pairs + 2 * best_run + 3 * three_prime_pairs
        if best is None or score > best["score"]:
            best = {
                "offset": offset, "pairs": pairs, "longest_run": best_run,
                "three_prime_pairs": three_prime_pairs, "score": score,
            }

    if best is None:
        return None

    offset = best["offset"]
    pad_top = max(0, -offset)
    pad_bottom = max(0, offset)
    top_line = " " * pad_top + top
    bottom_line = " " * pad_bottom + bottom_rev
    width = max(len(top_line), len(bottom_line))
    top_line = top_line.ljust(width)
    bottom_line = bottom_line.ljust(width)
    mid = "".join(
        "|" if (top_line[i].upper(), bottom_line[i].upper()) in WATSON_CRICK else " "
        for i in range(width)
    )
    best.update({"top": top_line, "match": mid, "bottom": bottom_line})
    return best


def self_dimer(seq: str) -> dict[str, Any] | None:
    """Self-dimer: the oligo against another copy of itself (3'->5')."""
    return best_duplex(seq, seq[::-1])


def cross_dimer(a: str, b: str) -> dict[str, Any] | None:
    """Hetero-dimer between the two primers of a pair."""
    return best_duplex(a, b[::-1])


def hairpin(seq: str, min_loop: int = 3) -> dict[str, Any] | None:
    """Best intramolecular stem, keeping at least `min_loop` unpaired bases."""
    n = len(seq)
    best: dict[str, Any] | None = None
    for i in range(n):
        for j in range(i + min_loop + 1, n):
            k = 0
            while (i - k >= 0 and j + k < n
                   and (seq[i - k].upper(), seq[j + k].upper()) in WATSON_CRICK):
                k += 1
            if k >= 3:
                loop = j - i - 1
                score = k * 2 - loop * 0.1
                if best is None or score > best["score"]:
                    best = {
                        "stem_length": k, "loop_length": loop,
                        "stem5": [i - k + 1, i], "stem3": [j, j + k - 1],
                        "score": round(score, 2),
                        "stem5_seq": seq[i - k + 1:i + 1],
                        "stem3_seq": seq[j:j + k],
                        "loop_seq": seq[i + 1:j],
                    }
    return best


def pair_geometry(pair: dict[str, Any]) -> dict[str, Any]:
    """Duplex diagrams for a designed pair (and its probe, when present)."""
    fwd = pair["forward"]["sequence"]
    rev = pair["reverse"]["sequence"]
    out: dict[str, Any] = {
        "forward_self_dimer": self_dimer(fwd),
        "reverse_self_dimer": self_dimer(rev),
        "cross_dimer": cross_dimer(fwd, rev),
        "forward_hairpin": hairpin(fwd),
        "reverse_hairpin": hairpin(rev),
    }
    if pair.get("probe"):
        probe = pair["probe"]["sequence"]
        out["probe_self_dimer"] = self_dimer(probe)
        out["probe_hairpin"] = hairpin(probe)
        out["probe_forward_dimer"] = cross_dimer(probe, fwd)
        out["probe_reverse_dimer"] = cross_dimer(probe, rev)
        # A 5'-G quenches most reporter dyes (Applied Biosystems design guide).
        out["probe_five_prime_g"] = probe[:1].upper() == "G"
    return out


# ─── Specificity within the downloaded set ───────────────────────────────────

def local_specificity(oligos: list[dict[str, str]],
                      records: list[dict[str, Any]],
                      workdir: Path | None = None) -> dict[str, Any]:
    """BLAST every oligo against the downloaded sequences.

    This answers "does the oligo have a second landing site among my own
    templates?". It is NOT a genome-wide specificity check: nothing outside the
    downloaded set is searched, so an off-target elsewhere in a host genome
    cannot be seen here.
    """
    if not oligos or not records:
        return {"available": False,
                "reason": "no oligos or no sequences", "hits": []}

    workdir = workdir or Path(tempfile.mkdtemp(prefix="pd_blast_"))
    workdir.mkdir(parents=True, exist_ok=True)
    db_fasta = workdir / "targets.fasta"
    q_fasta = workdir / "oligos.fasta"

    with db_fasta.open("w") as fh:
        for rec in records:
            fh.write(f">{rec['accession']}\n{rec['sequence']}\n")
    with q_fasta.open("w") as fh:
        for o in oligos:
            fh.write(f">{o['name']}\n{o['sequence']}\n")

    try:
        subprocess.run(
            config.tool_argv(config.MAKEBLASTDB_BIN, "-in", str(db_fasta),
                             "-dbtype", "nucl", "-out", str(workdir / "db")),
            capture_output=True, text=True, timeout=config.BLAST_LOCAL_TIMEOUT,
            check=True,
        )
        proc = subprocess.run(
            config.tool_argv(
                config.BLASTN_BIN, "-task", "blastn-short", "-query", str(q_fasta),
                "-db", str(workdir / "db"), "-word_size", "7", "-evalue", "1000",
                "-outfmt", "6 qseqid sseqid pident length mismatch gapopen "
                           "qstart qend sstart send evalue bitscore sstrand",
                "-max_target_seqs", "5000", "-num_threads", "2"),
            capture_output=True, text=True, timeout=config.BLAST_LOCAL_TIMEOUT,
        )
    except subprocess.SubprocessError as exc:
        logger.warning("local specificity BLAST failed: %s", exc)
        return {"available": False, "reason": str(exc)[:200], "hits": []}

    by_oligo: dict[str, list[dict[str, Any]]] = {o["name"]: [] for o in oligos}
    lengths = {o["name"]: len(o["sequence"]) for o in oligos}

    for line in proc.stdout.splitlines():
        p = line.split("\t")
        if len(p) < 13:
            continue
        qid, sid = p[0], p[1]
        try:
            pident, length = float(p[2]), int(p[3])
            mismatch, gapopen = int(p[4]), int(p[5])
            qend = int(p[7])
            sstart, send = int(p[8]), int(p[9])
        except ValueError:
            continue
        full_len = lengths.get(qid, length)
        # Only sites long enough to prime are interesting.
        if length < full_len - 3 or gapopen > 0:
            continue
        by_oligo.setdefault(qid, []).append({
            "subject": sid,
            "identity": pident,
            "aln_length": length,
            "mismatch": mismatch,
            "covers_three_prime": qend >= full_len - 1,
            "start": min(sstart, send),
            "end": max(sstart, send),
            "strand": p[12],
        })

    summary = []
    for name, hits in by_oligo.items():
        per_subject: dict[str, int] = {}
        for h in hits:
            per_subject[h["subject"]] = per_subject.get(h["subject"], 0) + 1
        extra = {s: c for s, c in per_subject.items() if c > 1}
        summary.append({
            "oligo": name,
            "total_sites": len(hits),
            "subjects_hit": len(per_subject),
            "subjects_with_multiple_sites": extra,
            "n_multi_site_subjects": len(extra),
            "hits": hits[:200],
        })

    return {
        "available": True,
        "n_subjects": len(records),
        "oligos": summary,
        "note": "Searched only against the downloaded sequences; not a "
                "genome-wide or host-background specificity check.",
    }


def build_oligo_list(pair: dict[str, Any], prefix: str) -> list[dict[str, str]]:
    oligos = [
        {"name": f"{prefix}_F", "sequence": pair["forward"]["sequence"]},
        {"name": f"{prefix}_R", "sequence": pair["reverse"]["sequence"]},
    ]
    if pair.get("probe"):
        oligos.append({"name": f"{prefix}_P", "sequence": pair["probe"]["sequence"]})
    return oligos


def order_sequences(pair: dict[str, Any]) -> dict[str, str]:
    """What you would actually paste into an oligo order form."""
    out = {
        "forward_5_3": pair["forward"]["sequence"],
        "reverse_5_3": pair["reverse"]["sequence"],
        "reverse_binds_template_plus": pair["reverse"]["template_slice"],
        "reverse_check_revcomp": revcomp(pair["reverse"]["template_slice"]),
    }
    if pair.get("probe"):
        out["probe_5_3"] = pair["probe"]["sequence"]
    return out
