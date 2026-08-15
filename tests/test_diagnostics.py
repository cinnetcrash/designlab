"""Failure-message checks: when a design fails, the message must name the right cause.

These are regression tests for messages that were wrong in the field. A design
that fails is normal; a design that fails and blames the wrong constraint costs
the user an afternoon of turning the wrong dial.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from models import DesignRequest, Primer3Settings          # noqa: E402
from pipeline import _diagnose_primer3, _feasible_product_range   # noqa: E402


def req(**kw) -> DesignRequest:
    return DesignRequest(accessions=["X"], primer3=Primer3Settings(**kw))


def check(name, fn):
    try:
        fn()
        print("  ok   " + name)
    except AssertionError as exc:
        print("  FAIL " + name + " → " + str(exc))
        raise


# Real counters from job c7dd3bfd019d: 49 short invA records, one 28 bp conserved
# block (CAGTTTATCGTTATTACCAAAGGTTCAG, 35.7% GC) against PRIMER_MIN_GC=40.
# 234 - 174 = 60 left candidates fitted inside the block; 59 failed GC, 1 Tm.
FIELD_CASE = {
    "left": "considered 234, overlap excluded region 174, "
            "GC content failed 59, low tm 1, ok 0",
    "right": "considered 206, overlap excluded region 174, "
             "GC content failed 31, low tm 1, ok 0",
    "pair": "considered 0, ok 0",
}


def test_short_block_blames_composition_not_length() -> None:
    """The block held 60 candidates, so 'too short to hold a primer' is false."""
    msg = _diagnose_primer3(FIELD_CASE, req(mode="qpcr"))
    assert "did fit inside a conserved block" in msg, msg
    assert "GC window" in msg, msg
    # The wrong message claimed the block could not hold a primer at all.
    assert "too short to hold" not in msg, \
        "the block held 60 candidates; calling it too short is wrong: " + msg
    assert "not too short" in msg, "the correction should be explicit: " + msg
    assert "92" in msg, "in-block candidate count (60 + 32) not reported: " + msg
    # Both filters must be reported: widening GC alone would not have rescued
    # this run, because the best in-block Tm was 55.9 C against a 57 C floor.
    assert "Tm window" in msg and "GC window" in msg, \
        "both per-oligo filters should be shown, not only the dominant one: " + msg
    assert "may not be enough" in msg, \
        "the message should warn that widening one filter can be futile: " + msg


def test_nothing_fits_is_still_reported_as_such() -> None:
    msg = _diagnose_primer3({
        "left": "considered 100, overlap excluded region 100, ok 0",
        "right": "considered 100, overlap excluded region 100, ok 0",
        "pair": "considered 0, ok 0",
    }, req())
    assert "No conserved block can hold a primer" in msg, msg
    assert "(N-1)/N" in msg, "the threshold gotcha should be explained: " + msg


def test_tm_is_named_when_tm_dominates() -> None:
    msg = _diagnose_primer3({
        "left": "considered 200, overlap excluded region 50, low tm 140, "
                "GC content failed 10, ok 0",
        "right": "considered 200, overlap excluded region 50, low tm 140, "
                 "GC content failed 10, ok 0",
        "pair": "considered 0, ok 0",
    }, req())
    # Tm leads, so the advice must be about Tm even though both counts are shown.
    assert "280 on the Tm window" in msg, msg
    assert "20 on the GC window" in msg, "the other counter is still reported: " + msg
    assert msg.index("Widening the Tm window") < msg.index("alone may not be enough"), \
        "the warning must name the leading filter: " + msg
    assert "widen the Tm window" in msg, msg


def test_probe_is_named_when_the_pair_stage_ran() -> None:
    msg = _diagnose_primer3({
        "left": "considered 3287, ok 251", "right": "considered 3272, ok 189",
        "pair": "considered 66610, unacceptable product size 61111, "
                "no internal oligo 5499, ok 0",
    }, req(mode="qpcr"))
    assert "hydrolysis probe" in msg, msg
    assert "probe Tm" in msg, msg


def test_empty_explain_does_not_crash() -> None:
    msg = _diagnose_primer3({"left": "", "right": "", "pair": ""}, req())
    assert "no candidate at all" in msg, msg
    assert _diagnose_primer3({}, req())          # missing keys entirely


def test_impossible_product_window_is_not_printed() -> None:
    """A single 28 bp block cannot yield any product: report None, not 36-28 bp."""
    one_short = [{"ref_start": 75, "ref_end": 102, "length": 28}]
    assert _feasible_product_range(one_short, 18) is None

    # Exactly two primers wide is feasible.
    exact = [{"ref_start": 0, "ref_end": 35, "length": 36}]
    assert _feasible_product_range(exact, 18) == (36, 36)

    # Two blocks. The 91 bp block alone already holds both primers, so the
    # shortest possible product is 2 x 18 rather than the gap between blocks;
    # the longest spans from the start of the first to the end of the last.
    two = [{"ref_start": 0, "ref_end": 90, "length": 91},
           {"ref_start": 150, "ref_end": 214, "length": 65}]
    assert _feasible_product_range(two, 18) == (36, 215)

    # When neither block alone is wide enough, the gap between them sets the floor.
    narrow = [{"ref_start": 0, "ref_end": 24, "length": 25},
              {"ref_start": 150, "ref_end": 174, "length": 25}]
    assert _feasible_product_range(narrow, 18) == (150 - 24 + 36, 175)

    # Order of the input must not matter.
    assert _feasible_product_range(list(reversed(two)), 18) \
        == _feasible_product_range(two, 18)


if __name__ == "__main__":
    print("failure-message checks")
    check("a short block is blamed for its composition, not its length",
          test_short_block_blames_composition_not_length)
    check("a block that truly fits nothing is reported as such",
          test_nothing_fits_is_still_reported_as_such)
    check("Tm is named when Tm dominates", test_tm_is_named_when_tm_dominates)
    check("the probe is named when the pair stage ran",
          test_probe_is_named_when_the_pair_stage_ran)
    check("an empty explain block does not crash", test_empty_explain_does_not_crash)
    check("no impossible product window is printed",
          test_impossible_product_window_is_not_printed)
    print("all diagnostic checks passed")
