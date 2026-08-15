"""Entrez query builder: plain words in, a valid Entrez query out. No network."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from ncbi import build_entrez_query          # noqa: E402
from models import SearchRequest             # noqa: E402


def check(name, fn):
    try:
        fn()
        print("  ok   " + name)
    except AssertionError as exc:
        print("  FAIL " + name + " → " + str(exc))
        raise


def test_gene_only() -> None:
    q = build_entrez_query(gene="invA", min_length=1500, max_length=5000)
    assert '"invA"[Gene]' in q and '"invA"[Title]' in q, q
    assert " OR " in q, "a gene name must match the gene field or the title"
    assert "1500:5000[SLEN]" in q, q
    assert '"wgs master"[Properties]' in q, "master records must be excluded"


def test_gene_and_organism() -> None:
    q = build_entrez_query(gene="invA", organism="Salmonella enterica")
    assert '"Salmonella enterica"[Organism]' in q, q
    assert q.index("[Gene]") < q.index("[Organism]"), "gene clause comes first"
    assert q.count(" AND ") >= 2, q


def test_organism_only_is_allowed() -> None:
    q = build_entrez_query(organism="Escherichia coli")
    assert '"Escherichia coli"[Organism]' in q and "[Gene]" not in q, q


def test_raw_query_is_used_verbatim() -> None:
    raw = 'rpoB[Gene] AND txid590[Organism:exp]'
    q = build_entrez_query(gene="ignored", organism="ignored", raw_query=raw)
    assert q.startswith(raw), q
    assert "ignored" not in q, "raw query must override the plain words"
    assert "[SLEN]" in q, "the length window still applies to a raw query"


def test_master_filter_not_doubled() -> None:
    raw = 'invA[Gene] NOT "wgs master"[Properties]'
    q = build_entrez_query(raw_query=raw)
    assert q.count("wgs master") == 1, q


def test_quotes_in_gene_name_do_not_break_the_query() -> None:
    q = build_entrez_query(gene='inv"A')
    assert q.count('"') % 2 == 0, f"unbalanced quotes: {q}"


def test_empty_input_is_refused() -> None:
    try:
        build_entrez_query()
    except ValueError:
        return
    raise AssertionError("empty input should raise, not search for everything")


def test_request_validation() -> None:
    """A gene-name search must not require Entrez syntax in `text`."""
    req = SearchRequest(input_type="query", gene="invA")
    assert req.gene == "invA" and req.text == ""

    for bad in ({"input_type": "query"},
                {"input_type": "sequence", "text": "AC"}):
        try:
            SearchRequest(**bad)
        except Exception:
            continue
        raise AssertionError(f"should have been rejected: {bad}")


if __name__ == "__main__":
    print("entrez query builder checks")
    check("gene name alone", test_gene_only)
    check("gene plus organism", test_gene_and_organism)
    check("organism alone", test_organism_only_is_allowed)
    check("raw query wins over plain words", test_raw_query_is_used_verbatim)
    check("master-record filter is not duplicated", test_master_filter_not_doubled)
    check("quotes in the gene name stay balanced",
          test_quotes_in_gene_name_do_not_break_the_query)
    check("empty input is refused", test_empty_input_is_refused)
    check("request accepts plain words", test_request_validation)
    print("all entrez query checks passed")
