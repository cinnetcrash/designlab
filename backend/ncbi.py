"""NCBI access: homologue discovery (remote BLAST or Entrez) and record download."""
from __future__ import annotations

import io
import logging
import re
import time
import urllib.parse
import urllib.request
from typing import Any, Callable

from Bio import Entrez
from Bio.Blast import NCBIXML

import config
from sequtils import clean_sequence

logger = logging.getLogger("ncbi")

Entrez.email = config.ENTREZ_EMAIL
if config.ENTREZ_API_KEY:
    Entrez.api_key = config.ENTREZ_API_KEY

Progress = Callable[[str], None]

# WGS/TSA master accessions: 4-6 letters, two digits, then all zeros.
WGS_MASTER_RE = re.compile(r"^[A-Z]{4,6}\d{2}0{6,}(\.\d+)?$", re.IGNORECASE)


# ─── Sequence input → remote BLAST ───────────────────────────────────────────

BLAST_URL = "https://blast.ncbi.nlm.nih.gov/Blast.cgi"


def blast_search(query_seq: str, *, database: str, max_hits: int,
                 min_identity: float, min_coverage: float,
                 progress: Progress = lambda _m: None) -> list[dict[str, Any]]:
    """Run megablast at NCBI and return one entry per subject accession.

    This talks to NCBI's URL API (Put / poll / Get XML) rather than calling
    `blastn -remote`. The BLAST+ client's remote mode submits the job on this
    machine but then never returns a result — it was still hanging after seven
    minutes on a query the URL API answered in about thirty seconds.

    The subject range of the best HSP is carried along, because a hit is often
    a whole 5 Mb chromosome and only the matching locus should be downloaded.
    """
    query_seq = clean_sequence(query_seq)
    if len(query_seq) < 50:
        raise ValueError("Query sequence is shorter than 50 bp; BLAST needs more.")

    rid, rtoe = _blast_submit(query_seq, database, max_hits, progress)
    xml = _blast_wait(rid, rtoe, progress)
    hits = _blast_parse(xml, min_identity, min_coverage, progress)
    progress(f"{len(hits)} accession(s) passed identity >= {min_identity}% and "
             f"coverage >= {min_coverage}%")
    return hits[:max_hits]


def _blast_submit(query_seq: str, database: str, max_hits: int,
                  progress: Progress) -> tuple[str, int]:
    params = {
        "CMD": "Put",
        "PROGRAM": "blastn",
        "MEGABLAST": "on",
        "DATABASE": database,
        "QUERY": query_seq,
        "HITLIST_SIZE": str(max(max_hits * 3, 50)),
        "EXPECT": "1e-10",
        "FORMAT_TYPE": "XML",
    }
    data = urllib.parse.urlencode(params).encode()
    try:
        with urllib.request.urlopen(BLAST_URL, data=data, timeout=120) as fh:
            body = fh.read().decode("utf-8", "replace")
    except Exception as exc:                            # noqa: BLE001
        raise RuntimeError(f"Could not reach NCBI BLAST: {exc}") from exc

    rid_match = re.search(r"RID = (\S+)", body)
    rtoe_match = re.search(r"RTOE = (\d+)", body)
    if not rid_match:
        raise RuntimeError(
            "NCBI did not return a BLAST request id. The database name "
            f"({database}) may be unsupported, or the query was rejected."
        )
    rid = rid_match.group(1)
    rtoe = int(rtoe_match.group(1)) if rtoe_match else 20
    progress(f"NCBI BLAST submitted against {database} — RID {rid}, "
             f"estimated {rtoe}s")
    return rid, rtoe


def _blast_wait(rid: str, rtoe: int, progress: Progress) -> str:
    deadline = time.time() + config.BLAST_REMOTE_TIMEOUT
    wait = min(max(rtoe, 5), 30)
    started = time.time()

    while time.time() < deadline:
        time.sleep(wait)
        wait = 10
        query = urllib.parse.urlencode(
            {"CMD": "Get", "FORMAT_OBJECT": "SearchInfo", "RID": rid})
        try:
            with urllib.request.urlopen(f"{BLAST_URL}?{query}", timeout=90) as fh:
                info = fh.read().decode("utf-8", "replace")
        except Exception as exc:                        # noqa: BLE001
            progress(f"BLAST status check failed ({exc}); retrying")
            continue

        if "Status=WAITING" in info:
            progress(f"BLAST running… {time.time() - started:.0f}s elapsed "
                     f"(RID {rid})")
            continue
        if "Status=FAILED" in info:
            raise RuntimeError(f"NCBI reports the BLAST search failed (RID {rid}).")
        if "Status=UNKNOWN" in info:
            raise RuntimeError(
                f"NCBI no longer knows RID {rid} — the search expired.")
        if "Status=READY" in info:
            if "ThereAreHits=yes" not in info:
                return ""
            progress(f"BLAST finished in {time.time() - started:.0f}s, "
                     "downloading results")
            return _blast_fetch_xml(rid)

    raise RuntimeError(
        f"BLAST (RID {rid}) did not finish within "
        f"{config.BLAST_REMOTE_TIMEOUT}s. The search may still complete at NCBI; "
        "retry, or use the gene-name search mode which answers immediately."
    )


def _blast_fetch_xml(rid: str) -> str:
    query = urllib.parse.urlencode({
        "CMD": "Get", "FORMAT_TYPE": "XML", "RID": rid,
        "ALIGNMENTS": "500", "DESCRIPTIONS": "500",
    })
    with urllib.request.urlopen(f"{BLAST_URL}?{query}", timeout=300) as fh:
        return fh.read().decode("utf-8", "replace")


def _blast_parse(xml: str, min_identity: float, min_coverage: float,
                 progress: Progress) -> list[dict[str, Any]]:
    if not xml.strip():
        progress("BLAST returned no hits")
        return []
    try:
        record = NCBIXML.read(io.StringIO(xml))
    except Exception as exc:                            # noqa: BLE001
        raise RuntimeError(f"Could not parse the BLAST XML result: {exc}") from exc

    query_len = record.query_letters or 1
    best: dict[str, dict[str, Any]] = {}

    for alignment in record.alignments:
        accession = alignment.accession or alignment.hit_id
        for hsp in alignment.hsps:
            identity = 100.0 * hsp.identities / hsp.align_length
            coverage = 100.0 * (hsp.query_end - hsp.query_start + 1) / query_len
            if identity < min_identity or coverage < min_coverage:
                continue
            hit = {
                "accession": accession,
                "title": alignment.hit_def,
                "identity": round(identity, 2),
                "coverage": round(coverage, 2),
                "evalue": f"{hsp.expect:.1e}",
                "bitscore": float(hsp.bits),
                "aln_length": hsp.align_length,
                "subject_length": alignment.length,
                "strand": "plus" if hsp.sbjct_start <= hsp.sbjct_end else "minus",
                "range": sorted((hsp.sbjct_start, hsp.sbjct_end)),
                "source": "blast",
            }
            if accession not in best or hit["bitscore"] > best[accession]["bitscore"]:
                best[accession] = hit

    return sorted(best.values(), key=lambda h: -h["bitscore"])


# ─── Gene-name input → Entrez search ─────────────────────────────────────────

def entrez_search(term: str, *, organism: str, max_hits: int,
                  min_length: int, max_length: int,
                  progress: Progress = lambda _m: None) -> list[dict[str, Any]]:
    """Search the nucleotide database by text and summarise the records."""
    query = term.strip()
    if organism.strip():
        query += f' AND "{organism.strip()}"[Organism]'
    query += f" AND {min_length}:{max_length}[SLEN]"
    # WGS master records dominate gene-name searches and carry no sequence.
    query += ' NOT "wgs master"[Properties]'

    progress(f"Entrez esearch: {query}")
    handle = Entrez.esearch(db="nucleotide", term=query, retmax=max_hits,
                            sort="relevance")
    result = Entrez.read(handle)
    handle.close()
    ids = result.get("IdList", [])
    if not ids:
        return []

    progress(f"{result.get('Count')} record(s) matched, summarising top {len(ids)}")
    handle = Entrez.esummary(db="nucleotide", id=",".join(ids))
    summaries = Entrez.read(handle)
    handle.close()

    hits: list[dict[str, Any]] = []
    skipped_master = 0
    for s in summaries:
        accession = str(s.get("AccessionVersion") or s.get("Caption"))
        if WGS_MASTER_RE.match(accession):
            # WGS/TSA master records describe a project and carry no sequence;
            # efetch returns an empty FASTA for them.
            skipped_master += 1
            continue
        hits.append({
            "accession": accession,
            "title": str(s.get("Title", "")),
            "identity": None,
            "coverage": None,
            "evalue": None,
            "bitscore": 0.0,
            "aln_length": None,
            "subject_length": int(s.get("Length", 0)),
            "strand": "plus",
            "range": None,
            "source": "entrez",
        })
    if skipped_master:
        progress(f"{skipped_master} WGS/TSA master record(s) skipped — they carry "
                 "no sequence data")
    return hits


# ─── Download ────────────────────────────────────────────────────────────────

def fetch_sequences(accessions: list[str],
                    ranges: dict[str, list[int]] | None = None,
                    flank: int = 200,
                    progress: Progress = lambda _m: None,
                    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Fetch FASTA records, restricted to a subrange (plus flanks) when known.

    Fetching only the aligned locus matters: an accession may be a whole
    chromosome, and aligning megabases would be both wrong and unusable.

    Returns (records, failures); each failure carries the accession and why it
    could not be used, so the caller can say what happened instead of only
    reporting that nothing arrived.
    """
    ranges = ranges or {}
    out: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    for i, acc in enumerate(accessions, 1):
        params: dict[str, Any] = {
            "db": "nucleotide", "id": acc, "rettype": "fasta", "retmode": "text",
        }
        rng = ranges.get(acc)
        if rng and len(rng) == 2:
            start, stop = sorted(int(x) for x in rng)
            params["seq_start"] = max(1, start - flank)
            params["seq_stop"] = stop + flank
        try:
            handle = Entrez.efetch(**params)
            raw = handle.read()
            handle.close()
        except Exception as exc:                       # noqa: BLE001
            logger.warning("efetch failed for %s: %s", acc, exc)
            reason = f"efetch failed: {exc.__class__.__name__}: {exc}"
            failures.append({"accession": acc, "reason": reason})
            progress(f"[{i}/{len(accessions)}] {acc}: {reason}")
            continue

        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        header, seq = _first_record(raw)
        if not seq:
            reason = ("record carries no sequence (WGS/TSA master or "
                      "suppressed record)")
            failures.append({"accession": acc, "reason": reason})
            progress(f"[{i}/{len(accessions)}] {acc}: {reason}")
            continue
        if len(seq) > config.MAX_TEMPLATE_BP:
            reason = (f"{len(seq):,} bp exceeds the {config.MAX_TEMPLATE_BP:,} bp "
                      "per-record limit")
            failures.append({"accession": acc, "reason": reason})
            progress(f"[{i}/{len(accessions)}] {acc}: {reason}")
            continue

        out.append({
            "accession": acc,
            "header": header,
            "sequence": seq,
            "length": len(seq),
            "fetched_range": [params.get("seq_start"), params.get("seq_stop")]
                             if rng else None,
        })
        progress(f"[{i}/{len(accessions)}] {acc}: {len(seq):,} bp downloaded")
        time.sleep(0.12 if config.ENTREZ_API_KEY else 0.35)   # NCBI rate limit

    return out, failures


def _first_record(fasta_text: str) -> tuple[str, str]:
    header, chunks = "", []
    for line in fasta_text.splitlines():
        if line.startswith(">"):
            if header:
                break
            header = line[1:].strip()
        elif line.strip():
            chunks.append(line.strip())
    return header, "".join(chunks).upper().replace("U", "T")
