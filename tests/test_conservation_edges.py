"""Regressions for two ways a conserved block can be wrong rather than absent.

Both were found by an adversarial review of the published code and both produced
a primer the tool then reported as good. They are the dangerous class of bug for
this application: not a crash, not an empty result, but a confident wrong answer.

Runs real MAFFT and real primer3_core. No network.
"""
from __future__ import annotations

import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import alignment                                  # noqa: E402
import primer3_runner                             # noqa: E402
import validation                                 # noqa: E402
from models import Primer3Settings                # noqa: E402


def check(name, fn):
    try:
        note = fn()
        print("  ok   " + name + (f" — {note}" if note else ""))
    except AssertionError as exc:
        print("  FAIL " + name + " → " + str(exc))
        raise


# ─── 1. an assembly gap must not become the most conserved region ────────────

def _draft_genomes_with_a_gap(seed: int = 4242):
    """Five drafts: a hypervariable window, and an N-run in four of them."""
    rng = random.Random(seed)
    backbone = "".join(rng.choice("ACGT") for _ in range(600))
    records = []
    for i in range(5):
        bases = list(backbone)
        for pos in range(150, 260):                  # genuinely variable
            if rng.random() < 0.35:
                bases[pos] = rng.choice("ACGT")
        if i < 4:                                    # 4 of 5 could not be read
            for pos in range(300, 400):
                bases[pos] = "N"
        records.append({"label": f"draft{i}", "aligned": "".join(bases)})
    return records, backbone


def test_n_run_is_not_a_conserved_block() -> None:
    records, _ = _draft_genomes_with_a_gap()
    profile = alignment.conservation_profile([r["aligned"] for r in records])
    reference = alignment.build_reference(records, profile, mode="consensus")

    # The N columns read as identity 1.0 with no gaps, which is why identity
    # alone cannot be trusted here.
    assert profile[350]["identity"] == 1.0, profile[350]
    assert profile[350]["gap_fraction"] == 0.0, profile[350]
    assert profile[350]["ambiguous_fraction"] == 0.8, profile[350]

    def touches_the_gap(block) -> bool:
        return block["ref_start"] <= 399 and block["ref_end"] >= 300

    blocks = alignment.conserved_blocks(profile, reference, 1.0, 0.0, 24)
    overlapping = [b for b in blocks if touches_the_gap(b)]
    assert not overlapping, f"an assembly gap was called conserved: {overlapping}"

    # Relaxing the threshold must bring it back — the check is a knob, not a ban.
    # Permissively the run merges with its conserved flanks into one long block,
    # which is exactly the failure: the gap became the biggest "conserved" region.
    permissive = alignment.conserved_blocks(profile, reference, 1.0, 0.0, 24,
                                            max_ambiguous_fraction=1.0)
    assert any(touches_the_gap(b) for b in permissive), \
        "max_ambiguous_fraction=1.0 should restore the old behaviour"
    assert max(b["length"] for b in permissive) > max(b["length"] for b in blocks), \
        "the permissive run should produce the longer, N-inflated block"
    return (f"{len(blocks)} blocks clear of the gap; permissive gives a "
            f"{max(b['length'] for b in permissive)} bp block over it")


def test_unread_bases_are_not_reported_as_a_perfect_match() -> None:
    records, backbone = _draft_genomes_with_a_gap()
    profile = alignment.conservation_profile([r["aligned"] for r in records])
    reference = alignment.build_reference(records, profile, mode="consensus")

    oligo = {"role": "forward", "sequence": backbone[320:340], "start": 320,
             "end": 339, "length": 20, "strand": "+", "three_prime_pos": 339,
             "template_slice": backbone[320:340]}
    binding = validation.oligo_binding(oligo, reference, records)

    assert binding["n_perfect"] == 1, \
        f"only the one record that was read should count: {binding['n_perfect']}"
    assert binding["perfect_percent"] == 20.0, binding["perfect_percent"]
    assert binding["tolerable_percent"] == 20.0, \
        "an unread site is not a tolerable mismatch either"

    unread = [s for s in binding["per_sequence"] if s["label"] != "draft4"]
    for s in unread:
        assert s["unknown"] == 20, s
        assert s["n_mismatch"] == 0, "an N is not a mismatch, it is an unknown"
        assert not s["perfect"], "a site that was never read cannot be perfect"
    return "20% instead of 100%"


# ─── 2. blocks welded across an insertion carried by a minority ──────────────

def _records_with_a_minority_insertion(seed: int = 1010):
    """The scenario from the review: 2 of 6 records carry an extra stretch."""
    rng = random.Random(seed)
    core = "".join(rng.choice("ACGT") for _ in range(900))
    at = rng.randrange(300, 600)
    insert = "".join(rng.choice("ACGT") for _ in range(rng.randrange(9, 30)))
    records = []
    for k in range(6):
        seq = core[:at] + (insert if k < 2 else "") + core[at:]
        bases = list(seq)
        for _ in range(rng.randrange(2, 10)):
            # Two statements on purpose: in `bases[f()] = g()` Python evaluates
            # g() first, which would draw the random numbers in the opposite
            # order and silently produce a different fixture from the one this
            # regression was found on.
            at_base = rng.randrange(len(bases))
            bases[at_base] = rng.choice("ACGT")
        records.append({"label": f"s{k}", "sequence": "".join(bases)})
    return records


def _welds(blocks):
    ordered = sorted(blocks, key=lambda b: b["ref_start"])
    return [(a, b) for a, b in zip(ordered, ordered[1:])
            if b["ref_start"] == a["ref_end"] + 1 and b["col_start"] > a["col_end"]]


def test_a_welded_junction_is_excluded() -> None:
    records = _records_with_a_minority_insertion()
    aligned = alignment.run_mafft(
        records, workdir=Path(tempfile.mkdtemp(prefix="pd_weld_")))
    profile = alignment.conservation_profile([r["aligned"] for r in aligned])
    reference = alignment.build_reference(aligned, profile, mode="consensus")
    blocks = alignment.conserved_blocks(profile, reference, 1.0, 0.0, 24)

    welds = _welds(blocks)
    assert welds, "the fixture no longer produces a welded junction"

    excluded = alignment.variable_regions(blocks, len(reference["sequence"]))
    for earlier, _later in welds:
        covering = [e for e in excluded
                    if e[0] <= earlier["ref_end"] < e[0] + e[1]]
        assert covering, (
            f"nothing stops a primer bridging the junction at ref "
            f"{earlier['ref_end']}: {excluded}")
    return f"{len(welds)} junction(s) guarded"


def test_no_primer_spans_a_junction_that_exists_in_no_sequence() -> None:
    records = _records_with_a_minority_insertion()
    aligned = alignment.run_mafft(
        records, workdir=Path(tempfile.mkdtemp(prefix="pd_weld2_")))
    profile = alignment.conservation_profile([r["aligned"] for r in aligned])
    reference = alignment.build_reference(aligned, profile, mode="consensus")
    blocks = alignment.conserved_blocks(profile, reference, 1.0, 0.0, 24)
    excluded = alignment.variable_regions(blocks, len(reference["sequence"]))

    settings = Primer3Settings(product_min=100, product_max=600, num_return=5)
    pairs = primer3_runner.run_primer3(
        reference["sequence"], "weld", settings, excluded)["pairs"]
    assert pairs, "Primer3 returned nothing; the fixture cannot test anything"

    def block_of(oligo):
        return next((b for b in blocks
                     if b["ref_start"] <= oligo["start"]
                     and oligo["end"] <= b["ref_end"]), None)

    for pair in pairs:
        for role in ("forward", "reverse"):
            oligo = pair[role]
            assert block_of(oligo) is not None, (
                f"pair {pair['rank']} {role} at ref "
                f"{oligo['start']}-{oligo['end']} lies in no single conserved "
                "block, so it spans a junction that exists in no sequence")

    # And the sequences carrying the insertion must not be silently counted as
    # bindable: nothing should report an insertion inside a primer site.
    for pair in pairs:
        for role in ("forward", "reverse"):
            binding = validation.oligo_binding(pair[role], reference, aligned)
            worst = max(s["insertions"] for s in binding["per_sequence"])
            assert worst == 0, (
                f"pair {pair['rank']} {role} has a site split by {worst} nt in "
                "at least one sequence")
    return f"{len(pairs)} pairs, all inside one block"


if __name__ == "__main__":
    print("conservation edge cases")
    check("an assembly gap is not a conserved block",
          test_n_run_is_not_a_conserved_block)
    check("unread bases are not a perfect match",
          test_unread_bases_are_not_reported_as_a_perfect_match)
    check("a welded junction is excluded", test_a_welded_junction_is_excluded)
    check("no primer spans a consensus-only junction",
          test_no_primer_spans_a_junction_that_exists_in_no_sequence)
    print("all conservation edge checks passed")
