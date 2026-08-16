"""DesignLab — FastAPI application.

Flow: paste a sequence (or a gene name) → pick how many NCBI records to pull →
MAFFT alignment → conserved blocks → Primer3 → in-silico validation, with every
binding site drawn on the alignment.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import config
import db
import jobs
import ncbi
import pipeline
import validation
from models import DesignRequest, SearchRequest, ValidatePrimerRequest
from sequtils import clean_sequence

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("primer_designer")

app = FastAPI(
    title="DesignLab",
    version="2.0.0",
    description="Conservation-aware PCR/qPCR primer design with NCBI integration",
)

FRONTEND = config.FRONTEND_DIR
app.mount("/css", StaticFiles(directory=FRONTEND / "css"), name="css")
app.mount("/js", StaticFiles(directory=FRONTEND / "js"), name="js")

DOWNLOADABLE = {
    "primers.tsv": "text/tab-separated-values",
    "results.json": "application/json",
    "aligned.fasta": "text/plain",
    "input.fasta": "text/plain",
    "primer3_input.txt": "text/plain",
    "search.json": "application/json",
    "job.json": "application/json",
}


@app.on_event("startup")
def _startup() -> None:
    """Index any job directories the database does not know about yet.

    A failure here must not keep the application from starting: the database is
    an index over data/jobs/, and designing new primers does not depend on it.
    """
    try:
        imported = db.rebuild_from_disk()
        logger.info("run database ready at %s (%d run(s) imported from disk)",
                    db.DB_PATH, imported)
    except Exception as exc:                            # noqa: BLE001
        logger.error("run database unavailable (%s); history will be empty "
                     "until /api/history/rebuild succeeds", exc)


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((FRONTEND / "index.html").read_text())


@app.get("/api/health")
def health() -> dict[str, Any]:
    versions = config.tool_versions()
    missing = [name for name, v in versions.items() if v.startswith("unavailable")]
    return {
        "status": "degraded" if missing else "ok",
        "missing_tools": missing,
        "versions": versions,
        "entrez_email": config.ENTREZ_EMAIL,
        "entrez_api_key": bool(config.ENTREZ_API_KEY),
        "jobs_dir": str(config.JOBS_DIR),
    }


# ─── Step 1: search NCBI ─────────────────────────────────────────────────────

@app.get("/api/entrez-query")
def entrez_query_preview(gene: str = "", organism: str = "",
                         min_length: int = 200, max_length: int = 20_000,
                         raw_query: str = "") -> dict[str, Any]:
    """Show the Entrez query the plain-word inputs will produce.

    The UI displays this live, so the user can see exactly what gets searched
    without having to write Entrez field tags themselves.
    """
    try:
        return {"query": ncbi.build_entrez_query(
            gene=gene, organism=organism, min_length=min_length,
            max_length=max_length, raw_query=raw_query)}
    except ValueError as exc:
        return {"query": "", "note": str(exc)}


@app.post("/api/search")
def start_search(req: SearchRequest) -> dict[str, Any]:
    if req.input_type == "sequence" and len(clean_sequence(req.text)) < 50:
        raise HTTPException(400, "A BLAST query needs at least 50 bp of sequence.")
    job_id = jobs.create_job("search", {
        "input_type": req.input_type,
        "database": req.database,
        "max_hits": req.max_hits,
        "preview": req.text[:60],
    })
    jobs.run_in_background(job_id, lambda jid: pipeline.run_search(jid, req))
    return {"job_id": job_id}


# ─── Step 2: design ──────────────────────────────────────────────────────────

@app.post("/api/design")
def start_design(req: DesignRequest) -> dict[str, Any]:
    if len(req.accessions) > config.MAX_SEQUENCES:
        raise HTTPException(
            400, f"At most {config.MAX_SEQUENCES} sequences per job "
                 f"({len(req.accessions)} requested).")
    if req.primer3.product_min >= req.primer3.product_max:
        raise HTTPException(400, "product_min must be smaller than product_max.")
    if req.primer3.primer_min_size > req.primer3.primer_max_size:
        raise HTTPException(400, "primer_min_size must not exceed primer_max_size.")
    job_id = jobs.create_job("design", {
        "gene_label": req.gene_label,
        "n_accessions": len(req.accessions),
        "mode": req.primer3.mode,
    })
    jobs.run_in_background(job_id, lambda jid: pipeline.run_design(jid, req))
    return {"job_id": job_id}


# ─── Job access ──────────────────────────────────────────────────────────────

@app.get("/api/job/{job_id}")
def job_status(job_id: str, log_from: int = 0) -> dict[str, Any]:
    meta = jobs.get_job_meta(job_id, log_from)
    if not meta:
        raise HTTPException(404, "Unknown job id.")
    return meta


@app.get("/api/job/{job_id}/result")
def job_result(job_id: str) -> JSONResponse:
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(404, "Unknown job id.")
    if job["status"] == "error":
        raise HTTPException(409, job["error"] or "Job failed.")
    if job["result"] is None:
        raise HTTPException(425, f"Job is {job['status']} ({job['stage']}).")
    return JSONResponse(job["result"])


@app.get("/api/jobs")
def job_list() -> dict[str, Any]:
    return {"jobs": jobs.list_jobs()}


@app.get("/api/jobs/active")
def jobs_active() -> dict[str, Any]:
    """What is running right now, and which external program each job is in."""
    active = jobs.active_jobs()
    return {
        "count": len(active),
        "jobs": active,
        "tools": sorted({j["tool"] for j in active if j.get("tool")}),
    }


# ─── Run history ─────────────────────────────────────────────────────────────

@app.get("/api/history")
def history(limit: int = 100, offset: int = 0, q: str = "",
            status: str = "") -> dict[str, Any]:
    """Past runs, newest first. `q` also matches an oligo sequence or accession."""
    if limit < 1 or limit > 500:
        raise HTTPException(400, "limit must be between 1 and 500.")
    return {
        "runs": db.list_runs(limit=limit, offset=offset, query=q, status=status),
        "stats": db.stats(),
    }


@app.get("/api/history/{job_id}")
def history_detail(job_id: str) -> dict[str, Any]:
    run = db.get_run(job_id)
    if not run:
        raise HTTPException(404, "No run with that id in the database.")
    return run


@app.delete("/api/history/{job_id}")
def history_delete(job_id: str) -> dict[str, Any]:
    """Drop a run from the index. The job directory on disk is left in place."""
    removed = db.delete_run(job_id)
    if not removed:
        raise HTTPException(404, "No run with that id in the database.")
    return {"removed": job_id,
            "note": "Removed from the index only; "
                    f"{config.JOBS_DIR / job_id} is untouched and a rebuild "
                    "will index it again."}


@app.post("/api/history/rebuild")
def history_rebuild(force: bool = False) -> dict[str, Any]:
    """Re-read the job directories into the database."""
    return {"imported": db.rebuild_from_disk(force=force), "stats": db.stats()}


@app.get("/api/job/{job_id}/file/{name}")
def job_file(job_id: str, name: str) -> FileResponse:
    if name not in DOWNLOADABLE:
        raise HTTPException(400, f"Not downloadable: {name}")
    path = Path(config.JOBS_DIR) / job_id / name
    if not path.exists():
        raise HTTPException(404, f"{name} does not exist for this job.")
    return FileResponse(path, media_type=DOWNLOADABLE[name], filename=name)


# ─── Ad-hoc oligo check ──────────────────────────────────────────────────────

@app.post("/api/validate-oligo")
def validate_oligo(req: ValidatePrimerRequest) -> dict[str, Any]:
    """Check a hand-written oligo against a finished design job's alignment."""
    job = jobs.get_job(req.job_id)
    if not job or job["kind"] != "design" or not job.get("result"):
        raise HTTPException(404, "No finished design job with that id.")

    result = job["result"]
    reference = result["reference"]
    ref_seq = reference["sequence"]
    aligned = [{"label": lb, "aligned": sq} for lb, sq in
               zip(result["alignment"]["labels"], result["alignment"]["sequences"])]

    oligo_seq = clean_sequence(req.sequence)
    from sequtils import revcomp

    site = oligo_seq if req.role != "reverse" else revcomp(oligo_seq)
    idx = ref_seq.find(site)
    if idx < 0:
        return {
            "found": False,
            "searched": site,
            "reference_label": reference["label"],
            "reference_length": len(ref_seq),
            "note": "The oligo has no exact site on the reference. It may still "
                    "bind with mismatches; this check only locates exact sites.",
        }

    oligo = {
        "role": req.role,
        "sequence": oligo_seq,
        "start": idx,
        "end": idx + len(site) - 1,
        "length": len(site),
        "strand": "-" if req.role == "reverse" else "+",
        "three_prime_pos": idx if req.role == "reverse" else idx + len(site) - 1,
        "template_slice": site,
    }
    binding = validation.oligo_binding(oligo, reference, aligned)
    return {
        "found": True,
        "oligo": oligo,
        "binding": binding,
        "self_dimer": validation.self_dimer(oligo_seq),
        "hairpin": validation.hairpin(oligo_seq),
    }


@app.get("/api/version")
def version() -> dict[str, Any]:
    return {"app": app.version, "tools": config.tool_versions()}
