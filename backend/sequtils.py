"""Sequence helpers: FASTA parsing, IUPAC-aware complementation and matching."""
from __future__ import annotations

import re

_COMPLEMENT = str.maketrans(
    "ACGTURYKMBVDHNSWacgturykmbvdhnsw-",
    "TGCAAYRMKVBHDNSWtgcaayrmkvbhdnsw-",
)

# Which concrete bases each IUPAC code stands for.
IUPAC: dict[str, set[str]] = {
    "A": {"A"}, "C": {"C"}, "G": {"G"}, "T": {"T"}, "U": {"T"},
    "R": {"A", "G"}, "Y": {"C", "T"}, "S": {"C", "G"}, "W": {"A", "T"},
    "K": {"G", "T"}, "M": {"A", "C"},
    "B": {"C", "G", "T"}, "D": {"A", "G", "T"},
    "H": {"A", "C", "T"}, "V": {"A", "C", "G"},
    "N": {"A", "C", "G", "T"},
}
# Reverse lookup: base set -> shortest IUPAC code.
_SET_TO_CODE = {frozenset(v): k for k, v in IUPAC.items() if k != "U"}

WATSON_CRICK = {("A", "T"), ("T", "A"), ("G", "C"), ("C", "G")}


def revcomp(seq: str) -> str:
    """Reverse complement, IUPAC aware."""
    return seq.translate(_COMPLEMENT)[::-1]


def clean_sequence(text: str) -> str:
    """Strip FASTA headers, whitespace and digits from pasted sequence text."""
    lines = [ln for ln in text.splitlines() if not ln.startswith(">")]
    seq = re.sub(r"[^A-Za-z-]", "", "".join(lines))
    return seq.upper().replace("U", "T")


def parse_fasta(text: str) -> list[tuple[str, str]]:
    """Parse FASTA text into (header, sequence) pairs."""
    records: list[tuple[str, str]] = []
    header, chunks = None, []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(">"):
            if header is not None:
                records.append((header, "".join(chunks).upper()))
            header, chunks = line[1:], []
        elif line:
            chunks.append(re.sub(r"[^A-Za-z-]", "", line))
    if header is not None:
        records.append((header, "".join(chunks).upper()))
    return records


def iupac_match(template_base: str, primer_base: str) -> bool:
    """True when the primer base can pair with the template base.

    Both sides may be degenerate; a match means their base sets overlap.
    An ambiguous template position is therefore *not* counted as a mismatch,
    which keeps N-runs in draft assemblies from inflating the mismatch count.
    """
    t = IUPAC.get(template_base.upper())
    p = IUPAC.get(primer_base.upper())
    if not t or not p:
        return False
    return bool(t & p)


def degenerate_code(bases: set[str]) -> str:
    """Shortest IUPAC code covering the given concrete bases."""
    bases = {b for b in bases if b in "ACGT"}
    if not bases:
        return "N"
    return _SET_TO_CODE.get(frozenset(bases), "N")


def gc_percent(seq: str) -> float:
    seq = seq.upper()
    bases = [b for b in seq if b in "ACGT"]
    if not bases:
        return 0.0
    return 100.0 * sum(1 for b in bases if b in "GC") / len(bases)


def complement_pairs(a: str, b: str) -> list[bool]:
    """Element-wise Watson-Crick pairing between two equal-length strings."""
    return [(x.upper(), y.upper()) in WATSON_CRICK for x, y in zip(a, b)]
