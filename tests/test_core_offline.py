"""Offline checks for the design core: no NCBI access, real MAFFT and Primer3."""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import alignment          # noqa: E402
import primer3_runner     # noqa: E402
import validation         # noqa: E402
from models import ConservationSettings, Primer3Settings   # noqa: E402
from sequtils import revcomp                                # noqa: E402


def make_records(n: int = 6, length: int = 900, seed: int = 7):
    """One conserved backbone with variable windows mutated per sequence."""
    rng = random.Random(seed)
    backbone = "".join(rng.choice("ACGT") for _ in range(length))
    variable = [(150, 220), (430, 500), (700, 760)]
    records = []
    for i in range(n):
        seq = list(backbone)
        for start, stop in variable:
            for pos in range(start, stop):
                if rng.random() < 0.25:
                    seq[pos] = rng.choice("ACGT")
        records.append({"label": f"seq{i}", "sequence": "".join(seq)})
    return backbone, variable, records


def test_conserved_blocks_and_primers() -> None:
    backbone, variable, records = make_records()

    aligned = alignment.run_mafft(records)
    assert len(aligned) == len(records)

    profile = alignment.conservation_profile([r["aligned"] for r in aligned])
    cons = ConservationSettings(identity_threshold=1.0, max_gap_fraction=0.0,
                                min_block_length=40)
    reference = alignment.build_reference(aligned, profile, mode="consensus")
    blocks = alignment.conserved_blocks(
        profile, reference, cons.identity_threshold,
        cons.max_gap_fraction, cons.min_block_length)

    assert blocks, "no conserved block found on a mostly identical set"
    # The mutated windows must not sit inside a conserved block.
    for start, stop in variable:
        mid = (start + stop) // 2
        assert not any(b["ref_start"] <= mid <= b["ref_end"] for b in blocks), \
            f"variable window around {mid} was called conserved"

    excluded = alignment.variable_regions(blocks, len(reference["sequence"]))
    settings = Primer3Settings(product_min=100, product_max=800, num_return=3)
    p3 = primer3_runner.run_primer3(reference["sequence"], "test", settings, excluded)
    assert p3["pairs"], f"Primer3 returned nothing: {p3['explain']}"

    pair = p3["pairs"][0]
    fwd, rev = pair["forward"], pair["reverse"]

    # Coordinates must be self-consistent with the reference sequence.
    ref = reference["sequence"]
    assert ref[fwd["start"]:fwd["end"] + 1] == fwd["sequence"]
    assert revcomp(ref[rev["start"]:rev["end"] + 1]) == rev["sequence"]
    assert pair["product_size"] == rev["end"] - fwd["start"] + 1

    # Both primers must sit inside a conserved block.
    for oligo in (fwd, rev):
        assert any(b["ref_start"] <= oligo["start"] and oligo["end"] <= b["ref_end"]
                   for b in blocks), "Primer3 placed an oligo in a variable region"

    # Every sequence should be a perfect match at a fully conserved site.
    binding = validation.oligo_binding(fwd, reference, aligned)
    assert binding["n_perfect"] == len(aligned), binding["per_sequence"]
    assert binding["perfect_percent"] == 100.0


def test_mismatch_is_detected() -> None:
    """A planted substitution must show up at the right oligo offset."""
    _, _, records = make_records(n=4, seed=11)
    # Break position 50 in one sequence only.
    broken = list(records[1]["sequence"])
    broken[50] = {"A": "C", "C": "G", "G": "T", "T": "A"}[broken[50]]
    records[1]["sequence"] = "".join(broken)

    aligned = alignment.run_mafft(records)
    profile = alignment.conservation_profile([r["aligned"] for r in aligned])
    reference = alignment.build_reference(aligned, profile, mode="first")

    oligo = {
        "role": "forward", "sequence": reference["sequence"][40:60],
        "start": 40, "end": 59, "length": 20, "strand": "+",
        "three_prime_pos": 59,
        "template_slice": reference["sequence"][40:60],
    }
    binding = validation.oligo_binding(oligo, reference, aligned)
    hit = next(s for s in binding["per_sequence"] if s["label"] == "seq1")
    assert hit["n_mismatch"] == 1
    assert hit["mismatches"][0]["ref_pos"] == 50
    assert hit["mismatches"][0]["from_three_prime"] == 9
    assert binding["n_perfect"] == len(aligned) - 1


def test_duplex_and_hairpin() -> None:
    duplex = validation.best_duplex("GGGGATCCCC", "GGGGATCCCC"[::-1])
    assert duplex and duplex["pairs"] >= 8
    assert len(duplex["top"]) == len(duplex["match"]) == len(duplex["bottom"])

    hp = validation.hairpin("GCGCGCAAAAGCGCGC")
    assert hp and hp["stem_length"] >= 5 and hp["loop_length"] >= 3


if __name__ == "__main__":
    test_conserved_blocks_and_primers()
    test_mismatch_is_detected()
    test_duplex_and_hairpin()
    print("all offline core checks passed")
