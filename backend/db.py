"""SQLite index of past runs.

The job directories under data/jobs/ already hold everything a run produced;
this database exists so those runs can be listed, filtered and searched without
opening every results.json on disk. It is an index, not the source of truth —
if the database is deleted it can be rebuilt from the job directories with
`rebuild_from_disk()`, which also runs at startup to pick up runs that finished
before the database existed.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

import config

logger = logging.getLogger("db")

DB_PATH = Path(config.DATA_DIR) / "runs.sqlite3"
_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    job_id            TEXT PRIMARY KEY,
    kind              TEXT NOT NULL,
    status            TEXT NOT NULL,
    created           TEXT NOT NULL,
    gene_label        TEXT,
    mode              TEXT,
    input_type        TEXT,
    query_preview     TEXT,
    n_sequences       INTEGER,
    n_conservation    INTEGER,
    n_blocks          INTEGER,
    conserved_bp      INTEGER,
    reference_label   TEXT,
    reference_bp      INTEGER,
    n_pairs           INTEGER,
    best_coverage     REAL,
    elapsed_s         REAL,
    settings_json     TEXT,
    error             TEXT,
    workdir           TEXT
);

CREATE TABLE IF NOT EXISTS run_primers (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id           TEXT NOT NULL,
    pair_rank        INTEGER,
    role             TEXT,
    sequence         TEXT,
    length           INTEGER,
    ref_start        INTEGER,
    ref_end          INTEGER,
    strand           TEXT,
    tm               REAL,
    gc_percent       REAL,
    hairpin_th       REAL,
    product_size     INTEGER,
    perfect_percent  REAL,
    n_sequences      INTEGER,
    FOREIGN KEY (job_id) REFERENCES runs(job_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS run_records (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id                TEXT NOT NULL,
    accession             TEXT,
    description           TEXT,
    length                INTEGER,
    coverage              REAL,
    used_in_conservation  INTEGER,
    FOREIGN KEY (job_id) REFERENCES runs(job_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_runs_created  ON runs(created DESC);
CREATE INDEX IF NOT EXISTS idx_runs_gene     ON runs(gene_label);
CREATE INDEX IF NOT EXISTS idx_primers_job   ON run_primers(job_id);
CREATE INDEX IF NOT EXISTS idx_primers_seq   ON run_primers(sequence);
CREATE INDEX IF NOT EXISTS idx_records_job   ON run_records(job_id);
CREATE INDEX IF NOT EXISTS idx_records_acc   ON run_records(accession);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init() -> None:
    with _LOCK, connect() as conn:
        conn.executescript(SCHEMA)


# ─── writing ─────────────────────────────────────────────────────────────────

def record_design(job_id: str, result: dict[str, Any], created: str) -> None:
    """Index a finished design run."""
    pairs = result.get("pairs", [])
    settings = result.get("settings", {})
    reference = result.get("reference", {})
    alignment = result.get("alignment", {})

    row = {
        "job_id": job_id,
        "kind": "design",
        "status": "done",
        "created": created,
        "gene_label": result.get("gene_label"),
        "mode": settings.get("primer3", {}).get("mode"),
        "input_type": "design",
        "query_preview": ", ".join(r["accession"] for r in result.get("records", [])[:4]),
        "n_sequences": alignment.get("n"),
        "n_conservation": result.get("conservation_n"),
        "n_blocks": len(result.get("blocks", [])),
        "conserved_bp": result.get("conserved_bp"),
        "reference_label": reference.get("label"),
        "reference_bp": len(reference.get("sequence", "")),
        "n_pairs": len(pairs),
        "best_coverage": max((p.get("coverage_percent") or 0 for p in pairs),
                             default=None),
        "elapsed_s": result.get("timings", {}).get("total_s"),
        "settings_json": json.dumps(settings),
        "error": None,
        "workdir": result.get("workdir"),
    }

    # Older runs were written before some of these keys existed; the index has
    # to read whatever shape is on disk rather than assume the current one.
    coverage_by_label = {c["label"]: c
                         for c in result.get("record_coverage") or []
                         if isinstance(c, dict) and "label" in c}
    low = {c["label"] for c in result.get("low_coverage_records") or []
           if isinstance(c, dict) and "label" in c}

    primer_rows = []
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        for role in ("forward", "reverse", "probe"):
            oligo = pair.get(role)
            if not isinstance(oligo, dict):
                continue
            binding = pair.get("binding", {}).get(role, {})
            primer_rows.append((
                job_id, pair.get("rank"), role, oligo.get("sequence"),
                oligo.get("length"), oligo.get("start"), oligo.get("end"),
                oligo.get("strand"), oligo.get("tm"), oligo.get("gc_percent"),
                oligo.get("hairpin_th"), pair.get("product_size"),
                binding.get("perfect_percent"), binding.get("n_sequences"),
            ))

    record_rows = []
    for rec in result.get("records") or []:
        if not isinstance(rec, dict):
            continue
        accession = rec.get("accession")
        cov = coverage_by_label.get(accession, {})
        record_rows.append((
            job_id, accession, rec.get("header"), rec.get("length"),
            cov.get("coverage"),
            0 if accession in low else 1,
        ))

    with _LOCK, connect() as conn:
        _replace_run(conn, row)
        conn.executemany(
            "INSERT INTO run_primers (job_id, pair_rank, role, sequence, length,"
            " ref_start, ref_end, strand, tm, gc_percent, hairpin_th,"
            " product_size, perfect_percent, n_sequences)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", primer_rows)
        conn.executemany(
            "INSERT INTO run_records (job_id, accession, description, length,"
            " coverage, used_in_conservation) VALUES (?,?,?,?,?,?)", record_rows)


def record_search(job_id: str, result: dict[str, Any], created: str) -> None:
    """Index a homologue search, so the history shows what was looked for."""
    hits = result.get("hits") or []
    row = {
        "job_id": job_id,
        "kind": "search",
        "status": "done",
        "created": created,
        "gene_label": None,
        "mode": result.get("database"),
        "input_type": result.get("input_type"),
        "query_preview": (result.get("query_sequence", "")[:60]
                          or ", ".join(h.get("accession", "") for h in hits[:4])),
        "n_sequences": result.get("n_hits"),
        "n_conservation": None, "n_blocks": None, "conserved_bp": None,
        "reference_label": None, "reference_bp": result.get("query_length"),
        "n_pairs": 0, "best_coverage": None,
        "elapsed_s": result.get("elapsed_s"),
        "settings_json": json.dumps({"database": result.get("database")}),
        "error": None,
        "workdir": str(config.JOBS_DIR / job_id),
    }
    record_rows = [
        (job_id, h.get("accession"), h.get("title"), h.get("subject_length"),
         (h.get("coverage") / 100.0) if h.get("coverage") is not None else None, 1)
        for h in hits if isinstance(h, dict)
    ]
    with _LOCK, connect() as conn:
        _replace_run(conn, row)
        conn.executemany(
            "INSERT INTO run_records (job_id, accession, description, length,"
            " coverage, used_in_conservation) VALUES (?,?,?,?,?,?)", record_rows)


def record_failure(job_id: str, kind: str, created: str, error: str,
                   meta: dict[str, Any] | None = None) -> None:
    """Index a run that failed, so the history shows what was attempted."""
    meta = meta or {}
    row = {
        "job_id": job_id, "kind": kind, "status": "error", "created": created,
        "gene_label": meta.get("gene_label"), "mode": meta.get("mode"),
        "input_type": meta.get("input_type"),
        "query_preview": meta.get("preview"),
        "n_sequences": meta.get("n_accessions"), "n_conservation": None,
        "n_blocks": None, "conserved_bp": None, "reference_label": None,
        "reference_bp": None, "n_pairs": 0, "best_coverage": None,
        "elapsed_s": meta.get("elapsed_s"),
        "settings_json": json.dumps(meta.get("settings", {})),
        "error": error[:2000], "workdir": str(config.JOBS_DIR / job_id),
    }
    with _LOCK, connect() as conn:
        _replace_run(conn, row)


def _replace_run(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute("DELETE FROM run_primers WHERE job_id = ?", (row["job_id"],))
    conn.execute("DELETE FROM run_records WHERE job_id = ?", (row["job_id"],))
    columns = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    conn.execute(f"INSERT OR REPLACE INTO runs ({columns}) VALUES ({placeholders})",
                 tuple(row.values()))


def delete_run(job_id: str) -> bool:
    """Remove a run from the index. Files on disk are left untouched."""
    with _LOCK, connect() as conn:
        cur = conn.execute("DELETE FROM runs WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM run_primers WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM run_records WHERE job_id = ?", (job_id,))
        return cur.rowcount > 0


# ─── reading ─────────────────────────────────────────────────────────────────

def list_runs(limit: int = 100, offset: int = 0, query: str = "",
              status: str = "") -> list[dict[str, Any]]:
    """Recent runs, newest first.

    `query` matches the gene label, the accessions preview, or an oligo
    sequence designed in that run — so pasting a primer back in finds the run
    it came from.
    """
    sql = ["SELECT r.* FROM runs r"]
    params: list[Any] = []
    where = []

    if query:
        needle = f"%{query.strip().upper()}%"
        where.append(
            "(UPPER(COALESCE(r.gene_label,'')) LIKE ?"
            " OR UPPER(COALESCE(r.query_preview,'')) LIKE ?"
            " OR r.job_id LIKE ?"
            " OR EXISTS (SELECT 1 FROM run_primers p"
            "            WHERE p.job_id = r.job_id AND UPPER(p.sequence) LIKE ?)"
            " OR EXISTS (SELECT 1 FROM run_records c"
            "            WHERE c.job_id = r.job_id AND UPPER(c.accession) LIKE ?))")
        params += [needle] * 5
    if status:
        where.append("r.status = ?")
        params.append(status)

    if where:
        sql.append("WHERE " + " AND ".join(where))
    sql.append("ORDER BY r.created DESC LIMIT ? OFFSET ?")
    params += [limit, offset]

    with _LOCK, connect() as conn:
        rows = conn.execute(" ".join(sql), params).fetchall()
    return [dict(r) for r in rows]


def get_run(job_id: str) -> dict[str, Any] | None:
    with _LOCK, connect() as conn:
        run = conn.execute("SELECT * FROM runs WHERE job_id = ?", (job_id,)).fetchone()
        if not run:
            return None
        primers = conn.execute(
            "SELECT * FROM run_primers WHERE job_id = ? ORDER BY pair_rank, role",
            (job_id,)).fetchall()
        records = conn.execute(
            "SELECT * FROM run_records WHERE job_id = ? ORDER BY accession",
            (job_id,)).fetchall()
    out = dict(run)
    out["primers"] = [dict(p) for p in primers]
    out["records"] = [dict(r) for r in records]
    out["result_available"] = (config.JOBS_DIR / job_id / "results.json").exists()
    return out


def stats() -> dict[str, Any]:
    with _LOCK, connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS runs,"
            " SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done,"
            " SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS failed,"
            " SUM(COALESCE(n_pairs,0)) AS pairs,"
            " MAX(created) AS last_run FROM runs").fetchone()
        oligos = conn.execute("SELECT COUNT(*) FROM run_primers").fetchone()[0]
        genes = conn.execute(
            "SELECT COUNT(DISTINCT gene_label) FROM runs"
            " WHERE gene_label IS NOT NULL").fetchone()[0]
    out = dict(row)
    out["oligos"] = oligos
    out["genes"] = genes
    out["db_path"] = str(DB_PATH)
    # In WAL mode most recent data sits in the -wal file, so the main file's
    # size alone reads as an almost-empty database.
    out["db_bytes"] = sum(
        path.stat().st_size
        for path in (DB_PATH, DB_PATH.with_name(DB_PATH.name + "-wal"),
                     DB_PATH.with_name(DB_PATH.name + "-shm"))
        if path.exists()
    )
    return out


# ─── rebuild ─────────────────────────────────────────────────────────────────

def rebuild_from_disk(force: bool = False) -> int:
    """Index job directories that are on disk but missing from the database.

    Runs at startup so a deleted database, or runs produced before the database
    existed, do not disappear from the history.
    """
    init()
    with _LOCK, connect() as conn:
        known = {r[0] for r in conn.execute("SELECT job_id FROM runs").fetchall()}

    imported = 0
    for job_dir in sorted(Path(config.JOBS_DIR).glob("*")):
        if not job_dir.is_dir():
            continue
        job_id = job_dir.name
        if job_id in known and not force:
            continue

        job_meta: dict[str, Any] = {}
        job_file = job_dir / "job.json"
        if job_file.exists():
            try:
                job_meta = json.loads(job_file.read_text())
            except (OSError, json.JSONDecodeError):
                job_meta = {}
        created = job_meta.get("created") or ""

        search_file = job_dir / "search.json"
        results_file = job_dir / "results.json"
        if search_file.exists() and not results_file.exists():
            try:
                record_search(job_id, json.loads(search_file.read_text()), created)
                imported += 1
            except Exception as exc:                    # noqa: BLE001
                logger.warning("could not index search %s: %s", job_id, exc)
            continue

        if results_file.exists():
            try:
                result = json.loads(results_file.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("skipping %s: unreadable results.json (%s)", job_id, exc)
                continue
            # One unreadable historical run must not stop the rest — and must
            # never stop the application from starting.
            try:
                record_design(job_id, result, created)
                imported += 1
            except Exception as exc:                    # noqa: BLE001
                logger.warning("could not index %s: %s", job_id, exc)
        elif job_meta.get("status") == "error":
            try:
                record_failure(job_id, job_meta.get("kind", "design"), created,
                               job_meta.get("error") or "unknown error",
                               job_meta.get("meta"))
                imported += 1
            except Exception as exc:                    # noqa: BLE001
                logger.warning("could not index failed run %s: %s", job_id, exc)

    if imported:
        logger.info("indexed %d run(s) from disk", imported)
    return imported
