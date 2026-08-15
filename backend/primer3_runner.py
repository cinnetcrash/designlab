"""Primer3 (boulder-IO) wrapper.

Primers are designed on the reference sequence with the variable stretches
passed as SEQUENCE_EXCLUDED_REGION. That keeps every primer inside a conserved
block while still allowing the amplicon interior to vary, and — unlike cutting
the conserved block out and designing on the fragment — the returned
coordinates stay in reference space, so each oligo can be drawn on the
alignment and checked against every downloaded sequence.
"""
from __future__ import annotations

import logging
import subprocess
from typing import Any

import config
from models import Primer3Settings

logger = logging.getLogger("primer3")


def build_boulder(sequence: str, seq_id: str, settings: Primer3Settings,
                  excluded: list[list[int]] | None = None) -> str:
    lines = [
        f"SEQUENCE_ID={seq_id}",
        f"SEQUENCE_TEMPLATE={sequence}",
        "PRIMER_TASK=generic",
        "PRIMER_PICK_LEFT_PRIMER=1",
        "PRIMER_PICK_RIGHT_PRIMER=1",
        f"PRIMER_PICK_INTERNAL_OLIGO={1 if settings.mode == 'qpcr' else 0}",
        f"PRIMER_MIN_SIZE={settings.primer_min_size}",
        f"PRIMER_OPT_SIZE={settings.primer_opt_size}",
        f"PRIMER_MAX_SIZE={settings.primer_max_size}",
        f"PRIMER_MIN_TM={settings.primer_min_tm}",
        f"PRIMER_OPT_TM={settings.primer_opt_tm}",
        f"PRIMER_MAX_TM={settings.primer_max_tm}",
        f"PRIMER_MIN_GC={settings.primer_min_gc}",
        f"PRIMER_MAX_GC={settings.primer_max_gc}",
        f"PRIMER_MAX_POLY_X={settings.max_poly_x}",
        f"PRIMER_GC_CLAMP={settings.gc_clamp}",
        "PRIMER_MAX_NS_ACCEPTED=0",
        "PRIMER_THERMODYNAMIC_OLIGO_ALIGNMENT=1",
        "PRIMER_THERMODYNAMIC_TEMPLATE_ALIGNMENT=0",
        f"PRIMER_MAX_SELF_ANY_TH={settings.max_self_any_th}",
        f"PRIMER_MAX_SELF_END_TH={settings.max_self_end_th}",
        f"PRIMER_MAX_HAIRPIN_TH={settings.max_hairpin_th}",
        f"PRIMER_PAIR_MAX_COMPL_ANY_TH={settings.max_pair_compl_any_th}",
        f"PRIMER_PAIR_MAX_COMPL_END_TH={settings.max_pair_compl_end_th}",
        f"PRIMER_SALT_MONOVALENT={settings.salt_monovalent}",
        f"PRIMER_SALT_DIVALENT={settings.salt_divalent}",
        f"PRIMER_DNTP_CONC={settings.dntp_conc}",
        f"PRIMER_DNA_CONC={settings.dna_conc}",
        f"PRIMER_PRODUCT_SIZE_RANGE={settings.product_min}-{settings.product_max}",
        f"PRIMER_NUM_RETURN={settings.num_return}",
        "PRIMER_EXPLAIN_FLAG=1",
    ]
    if settings.mode == "qpcr":
        lines += [
            f"PRIMER_INTERNAL_MIN_SIZE={settings.probe_min_size}",
            f"PRIMER_INTERNAL_OPT_SIZE={settings.probe_opt_size}",
            f"PRIMER_INTERNAL_MAX_SIZE={settings.probe_max_size}",
            f"PRIMER_INTERNAL_MIN_TM={settings.probe_min_tm}",
            f"PRIMER_INTERNAL_OPT_TM={settings.probe_opt_tm}",
            f"PRIMER_INTERNAL_MAX_TM={settings.probe_max_tm}",
            # A 5' G quenches most reporter dyes on hydrolysis probes.
            "PRIMER_INTERNAL_MAX_POLY_X=4",
        ]
    if config.PRIMER3_CONFIG:
        path = config.PRIMER3_CONFIG.rstrip("/") + "/"
        lines.insert(0, f"PRIMER_THERMODYNAMIC_PARAMETERS_PATH={path}")
    if excluded:
        lines.append("SEQUENCE_EXCLUDED_REGION=" +
                     " ".join(f"{s},{l}" for s, l in excluded))
    lines.append("=")
    return "\n".join(lines) + "\n"


def run_primer3(sequence: str, seq_id: str, settings: Primer3Settings,
                excluded: list[list[int]] | None = None) -> dict[str, Any]:
    """Run primer3_core and return parsed pairs plus its own explain output."""
    boulder = build_boulder(sequence, seq_id, settings, excluded)
    try:
        proc = subprocess.run([config.PRIMER3_BIN], input=boulder,
                              capture_output=True, text=True,
                              timeout=config.PRIMER3_TIMEOUT)
    except FileNotFoundError as exc:
        raise RuntimeError(f"primer3_core not found ({config.PRIMER3_BIN})") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("primer3_core timed out") from exc

    fields = _parse_boulder(proc.stdout)
    if proc.returncode != 0 and not fields:
        raise RuntimeError(f"primer3_core failed: {proc.stderr.strip()[:400]}")

    error = fields.get("PRIMER_ERROR")
    if error:
        raise RuntimeError(f"Primer3 error: {error}")

    n = int(fields.get("PRIMER_PAIR_NUM_RETURNED", "0") or 0)
    pairs = [_extract_pair(fields, i, settings, sequence) for i in range(n)]

    return {
        "pairs": pairs,
        "explain": {
            "left": fields.get("PRIMER_LEFT_EXPLAIN", ""),
            "right": fields.get("PRIMER_RIGHT_EXPLAIN", ""),
            "internal": fields.get("PRIMER_INTERNAL_EXPLAIN", ""),
            "pair": fields.get("PRIMER_PAIR_EXPLAIN", ""),
        },
        "warning": fields.get("PRIMER_WARNING", ""),
        "boulder_input": boulder,
    }


def _parse_boulder(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line and not line.startswith("="):
            key, value = line.split("=", 1)
            fields[key] = value
    return fields


def _f(fields: dict[str, str], key: str) -> float | None:
    raw = fields.get(key)
    if raw in (None, ""):
        return None
    try:
        return round(float(raw), 2)
    except ValueError:
        return None


def _oligo(fields: dict[str, str], tag: str, i: int, sequence: str,
           role: str) -> dict[str, Any] | None:
    """Read one oligo (LEFT / RIGHT / INTERNAL) into reference coordinates."""
    seq = fields.get(f"PRIMER_{tag}_{i}_SEQUENCE")
    pos = fields.get(f"PRIMER_{tag}_{i}")
    if not seq or not pos:
        return None
    start_str, len_str = pos.split(",")
    p3_start, length = int(start_str), int(len_str)

    if tag == "RIGHT":
        # Primer3 reports the 5' end of the right primer, which is its
        # right-most base on the plus strand; the oligo runs leftwards.
        start = p3_start - length + 1
        end = p3_start
        three_prime = start                      # 3' end points into the amplicon
    else:
        start = p3_start
        end = p3_start + length - 1
        three_prime = end

    return {
        "role": role,
        "sequence": seq,
        "start": start,                          # 0-based inclusive, plus strand
        "end": end,                              # 0-based inclusive, plus strand
        "length": length,
        "strand": "-" if tag == "RIGHT" else "+",
        "three_prime_pos": three_prime,
        "template_slice": sequence[start:end + 1],
        "tm": _f(fields, f"PRIMER_{tag}_{i}_TM"),
        "gc_percent": _f(fields, f"PRIMER_{tag}_{i}_GC_PERCENT"),
        "self_any_th": _f(fields, f"PRIMER_{tag}_{i}_SELF_ANY_TH"),
        "self_end_th": _f(fields, f"PRIMER_{tag}_{i}_SELF_END_TH"),
        "hairpin_th": _f(fields, f"PRIMER_{tag}_{i}_HAIRPIN_TH"),
        "end_stability": _f(fields, f"PRIMER_{tag}_{i}_END_STABILITY"),
        "penalty": _f(fields, f"PRIMER_{tag}_{i}_PENALTY"),
    }


def _extract_pair(fields: dict[str, str], i: int, settings: Primer3Settings,
                  sequence: str) -> dict[str, Any]:
    left = _oligo(fields, "LEFT", i, sequence, "forward")
    right = _oligo(fields, "RIGHT", i, sequence, "reverse")
    probe = (_oligo(fields, "INTERNAL", i, sequence, "probe")
             if settings.mode == "qpcr" else None)

    product_size = fields.get(f"PRIMER_PAIR_{i}_PRODUCT_SIZE")
    pair: dict[str, Any] = {
        "index": i,
        "rank": i + 1,
        "forward": left,
        "reverse": right,
        "probe": probe,
        "product_size": int(product_size) if product_size else None,
        "amplicon_start": left["start"] if left else None,
        "amplicon_end": right["end"] if right else None,
        "amplicon_seq": (sequence[left["start"]:right["end"] + 1]
                         if left and right else ""),
        "pair_penalty": _f(fields, f"PRIMER_PAIR_{i}_PENALTY"),
        "compl_any_th": _f(fields, f"PRIMER_PAIR_{i}_COMPL_ANY_TH"),
        "compl_end_th": _f(fields, f"PRIMER_PAIR_{i}_COMPL_END_TH"),
        "product_tm": _f(fields, f"PRIMER_PAIR_{i}_PRODUCT_TM"),
    }
    if left and right:
        pair["tm_difference"] = (round(abs(left["tm"] - right["tm"]), 2)
                                 if left["tm"] is not None and right["tm"] is not None
                                 else None)
    return pair
