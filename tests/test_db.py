"""Run-database checks. Uses a temporary data directory; touches no real runs."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="pd_db_test_")
os.environ["PRIMER_DATA_DIR"] = _TMP

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import db  # noqa: E402


SAMPLE_RESULT = {
    "gene_label": "invA",
    "settings": {"primer3": {"mode": "qpcr"}, "conservation": {}},
    "records": [
        {"accession": "CP130453", "header": "Salmonella chromosome", "length": 980,
         "fetched_range": [1, 980]},
        {"accession": "CP087538", "header": "Salmonella chromosome", "length": 980,
         "fetched_range": [1, 980]},
    ],
    "record_coverage": [
        {"label": "CP130453", "aligned_bp": 980, "coverage": 1.0},
        {"label": "CP087538", "aligned_bp": 400, "coverage": 0.41},
    ],
    "low_coverage_records": [
        {"label": "CP087538", "aligned_bp": 400, "coverage": 0.41},
    ],
    "alignment": {"n": 2, "length": 980},
    "conservation_n": 1,
    "reference": {"label": "CONSENSUS", "sequence": "ACGT" * 100},
    "blocks": [{"length": 300}, {"length": 120}],
    "conserved_bp": 420,
    "timings": {"total_s": 12.3},
    "workdir": "/tmp/does-not-matter",
    "pairs": [{
        "rank": 1,
        "product_size": 204,
        "coverage_percent": 100.0,
        "forward": {"sequence": "CAGACATGCCACGGTACAAC", "length": 20, "start": 10,
                    "end": 29, "strand": "+", "tm": 59.2, "gc_percent": 55.0,
                    "hairpin_th": 0.0},
        "reverse": {"sequence": "CGTAATTCGCCGCCATTGG", "length": 19, "start": 195,
                    "end": 213, "strand": "-", "tm": 60.0, "gc_percent": 57.9,
                    "hairpin_th": 0.0},
        "probe": {"sequence": "TGGAAGCGCTCGCATTGTGGGC", "length": 22, "start": 80,
                  "end": 101, "strand": "+", "tm": 62.7, "gc_percent": 59.1,
                  "hairpin_th": 40.0},
        "binding": {
            "forward": {"perfect_percent": 100.0, "n_sequences": 2},
            "reverse": {"perfect_percent": 50.0, "n_sequences": 2},
            "probe": {"perfect_percent": 100.0, "n_sequences": 2},
        },
    }],
}


def check(name, fn):
    try:
        fn()
        print("  ok   " + name)
    except AssertionError as exc:
        print("  FAIL " + name + " → " + str(exc))
        raise


def test_roundtrip() -> None:
    db.init()
    db.record_design("job1", SAMPLE_RESULT, "2026-08-15T10:00:00+00:00")

    runs = db.list_runs()
    assert len(runs) == 1, runs
    run = runs[0]
    assert run["gene_label"] == "invA"
    assert run["mode"] == "qpcr"
    assert run["n_pairs"] == 1
    assert run["conserved_bp"] == 420
    assert run["n_conservation"] == 1

    detail = db.get_run("job1")
    assert len(detail["primers"]) == 3, "forward, reverse and probe expected"
    assert len(detail["records"]) == 2
    # The low-coverage record must be flagged as excluded from conservation.
    by_acc = {r["accession"]: r for r in detail["records"]}
    assert by_acc["CP130453"]["used_in_conservation"] == 1
    assert by_acc["CP087538"]["used_in_conservation"] == 0


def test_search_by_oligo() -> None:
    """Pasting a primer back in must find the run that designed it."""
    hits = db.list_runs(query="CAGACATGCCACGGTACAAC")
    assert [h["job_id"] for h in hits] == ["job1"], hits
    assert db.list_runs(query="TTTTTTTTTTTTTTTTTTTT") == []
    assert [h["job_id"] for h in db.list_runs(query="CP087538")] == ["job1"]
    assert [h["job_id"] for h in db.list_runs(query="inva")] == ["job1"]


def test_reindex_is_idempotent() -> None:
    db.record_design("job1", SAMPLE_RESULT, "2026-08-15T10:00:00+00:00")
    assert len(db.list_runs()) == 1, "re-indexing must not duplicate the run"
    assert len(db.get_run("job1")["primers"]) == 3, \
        "re-indexing must not duplicate the oligos"


def test_failure_is_recorded() -> None:
    db.record_failure("job2", "design", "2026-08-15T11:00:00+00:00",
                      "RuntimeError: no conserved block",
                      {"gene_label": "gyrB", "mode": "standard"})
    failed = db.list_runs(status="error")
    assert [f["job_id"] for f in failed] == ["job2"]
    assert "no conserved block" in failed[0]["error"]
    stats = db.stats()
    assert stats["runs"] == 2 and stats["done"] == 1 and stats["failed"] == 1
    assert stats["oligos"] == 3


def test_delete_only_touches_the_index() -> None:
    assert db.delete_run("job2") is True
    assert db.delete_run("job2") is False
    assert db.stats()["runs"] == 1


if __name__ == "__main__":
    print("run database checks (temporary dir: %s)" % _TMP)
    check("record and read back a design run", test_roundtrip)
    check("find a run by its oligo sequence", test_search_by_oligo)
    check("re-indexing does not duplicate rows", test_reindex_is_idempotent)
    check("failed runs are kept with their reason", test_failure_is_recorded)
    check("delete removes the index row only", test_delete_only_touches_the_index)
    print("all database checks passed")
