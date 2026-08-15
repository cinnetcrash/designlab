"""Runtime configuration: tool paths, defaults and directories."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("PRIMER_DATA_DIR", BASE_DIR / "data"))
JOBS_DIR = DATA_DIR / "jobs"
FRONTEND_DIR = BASE_DIR / "frontend"

JOBS_DIR.mkdir(parents=True, exist_ok=True)

# ─── External tools ──────────────────────────────────────────────────────────
MAFFT_BIN = os.environ.get("MAFFT_BIN") or shutil.which("mafft") or "mafft"
PRIMER3_BIN = os.environ.get("PRIMER3_BIN") or shutil.which("primer3_core") or "primer3_core"
BLASTN_BIN = os.environ.get("BLASTN_BIN") or shutil.which("blastn") or "blastn"
MAKEBLASTDB_BIN = os.environ.get("MAKEBLASTDB_BIN") or shutil.which("makeblastdb") or "makeblastdb"

# primer3_core >= 2.5 needs its thermodynamic parameter directory.
_P3_CONFIG_CANDIDATES = [
    os.environ.get("PRIMER3_CONFIG"),
    "/etc/primer3_config",
    "/usr/share/primer3/primer3_config",
    "/usr/local/share/primer3_config",
]
PRIMER3_CONFIG = next(
    (c for c in _P3_CONFIG_CANDIDATES if c and Path(c).is_dir()), None
)

# ─── NCBI ────────────────────────────────────────────────────────────────────
ENTREZ_EMAIL = os.environ.get("NCBI_EMAIL", "gultekinnunal@gmail.com")
ENTREZ_API_KEY = os.environ.get("NCBI_API_KEY") or None

# ─── Timeouts (seconds) ──────────────────────────────────────────────────────
MAFFT_TIMEOUT = int(os.environ.get("MAFFT_TIMEOUT", 900))
PRIMER3_TIMEOUT = int(os.environ.get("PRIMER3_TIMEOUT", 120))
BLAST_REMOTE_TIMEOUT = int(os.environ.get("BLAST_REMOTE_TIMEOUT", 1200))
BLAST_LOCAL_TIMEOUT = int(os.environ.get("BLAST_LOCAL_TIMEOUT", 300))
ENTREZ_TIMEOUT = int(os.environ.get("ENTREZ_TIMEOUT", 120))

# ─── Limits ──────────────────────────────────────────────────────────────────
MAX_SEQUENCES = 200          # hard ceiling on how many records a job may pull
MAX_TEMPLATE_BP = 50_000     # per-record length ceiling before download
MAX_QUERY_BP = 20_000        # pasted sequence ceiling


def tool_versions() -> dict[str, str]:
    """Collect --version output of every external tool, for the methods section."""

    def _run(cmd: list[str], grep: str | None = None) -> str:
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            out = (p.stdout + p.stderr).strip().splitlines()
        except Exception as exc:                       # noqa: BLE001
            return f"unavailable ({exc.__class__.__name__})"
        if not out:
            return "unavailable"
        if grep:
            for line in out:
                if grep.lower() in line.lower():
                    return line.strip()
        return out[0].strip()

    import Bio

    return {
        "mafft": _run([MAFFT_BIN, "--version"]),
        "primer3_core": _run([PRIMER3_BIN, "--about"], grep="primer3"),
        "blastn": _run([BLASTN_BIN, "-version"], grep="blastn"),
        "biopython": f"biopython {Bio.__version__}",
        "primer3_config": PRIMER3_CONFIG or "not found",
    }
