#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Primer Designer — installer
#
#   bash install.sh                install Python deps, then report on the
#                                  external tools and how to get any that are
#                                  missing
#   bash install.sh --with-apt     also install the missing external tools with
#                                  apt (Debian/Ubuntu; asks for sudo)
#   bash install.sh --with-conda   same, but into a conda environment
#
# Nothing here touches an existing Python environment: the packages go into a
# project-local .venv/, and --with-conda creates its own named environment.
# --with-apt does install system packages, which is why it is opt-in and prints
# the exact command before running it.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

cd "$(dirname "$0")"
PROJECT_DIR="$PWD"
CONDA_ENV_NAME="${CONDA_ENV_NAME:-primer-designer}"
WITH_CONDA=0
WITH_APT=0
for arg in "$@"; do
  case "$arg" in
    --with-conda) WITH_CONDA=1 ;;
    --with-apt)   WITH_APT=1 ;;
    -h|--help)    sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg (try --help)"; exit 2 ;;
  esac
done

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*"; }

bold "Primer Designer installer"
echo "  project: $PROJECT_DIR"
echo

# ─── 1. Python ───────────────────────────────────────────────────────────────
bold "1/3  Python environment"

PYTHON_BIN="${PYTHON:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  fail "python3 not found. Install Python 3.11 or newer, then re-run."
  exit 1
fi

PY_VERSION=$("$PYTHON_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
if ! "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  fail "Python $PY_VERSION found, 3.11+ required."
  exit 1
fi
ok "python $PY_VERSION ($PYTHON_BIN)"

# A project-local venv on purpose: some conda base environments ship a
# starlette version FastAPI cannot use, and this must not depend on — or
# change — whatever else is installed on the machine.
if [[ ! -x .venv/bin/python ]]; then
  echo "  creating .venv …"
  "$PYTHON_BIN" -m venv .venv || { fail "could not create .venv"; exit 1; }
fi
ok ".venv ready"

echo "  installing Python packages (this can take a few minutes) …"
if .venv/bin/pip install -q --upgrade pip >/dev/null 2>&1 &&
   .venv/bin/pip install -q -r requirements.txt; then
  ok "$(.venv/bin/pip list 2>/dev/null | grep -ciE '^(fastapi|uvicorn|biopython|pydantic) ') of 4 core packages installed"
else
  fail "pip install failed — see the output above"
  exit 1
fi
echo

# ─── 2. External tools ───────────────────────────────────────────────────────
bold "2/3  External programs"

MISSING=()
check_tool() {
  local bin="$1" label="$2"
  if command -v "$bin" >/dev/null 2>&1; then
    ok "$label — $(command -v "$bin")"
  else
    fail "$label — not on PATH"
    MISSING+=("$bin")
  fi
}

check_tool mafft        "MAFFT (multiple sequence alignment)"
check_tool primer3_core "Primer3 (primer design)"
check_tool blastn       "BLAST+ blastn (homology search)"
check_tool makeblastdb  "BLAST+ makeblastdb"

APT_PACKAGES=""
for bin in "${MISSING[@]:-}"; do
  case "$bin" in
    mafft)                    APT_PACKAGES="$APT_PACKAGES mafft" ;;
    primer3_core)             APT_PACKAGES="$APT_PACKAGES primer3" ;;
    blastn|makeblastdb)       APT_PACKAGES="$APT_PACKAGES ncbi-blast+" ;;
  esac
done
# de-duplicate (blastn and makeblastdb both come from ncbi-blast+)
APT_PACKAGES=$(printf '%s\n' $APT_PACKAGES | sort -u | tr '\n' ' ')

if ((${#MISSING[@]} > 0)); then
  echo
  if ((WITH_APT)) && command -v apt-get >/dev/null 2>&1; then
    echo "  running: sudo apt-get install -y $APT_PACKAGES"
    if sudo apt-get update -qq && sudo apt-get install -y $APT_PACKAGES; then
      ok "installed: $APT_PACKAGES"
      MISSING=()
      for tool in mafft primer3_core blastn makeblastdb; do
        command -v "$tool" >/dev/null 2>&1 || MISSING+=("$tool")
      done
      ((${#MISSING[@]} == 0)) && ok "all external programs now on PATH"
    else
      fail "apt install failed — see the output above"
    fi
  elif ((WITH_APT)); then
    fail "--with-apt needs apt-get; this does not look like a Debian/Ubuntu system"
  elif ((WITH_CONDA)) && command -v conda >/dev/null 2>&1; then
    echo "  installing ${MISSING[*]} into conda env '$CONDA_ENV_NAME' …"
    if conda create -y -n "$CONDA_ENV_NAME" -c conda-forge -c bioconda \
         mafft primer3 blast >/dev/null 2>&1; then
      ok "conda env '$CONDA_ENV_NAME' created"
      warn "activate it before starting: conda activate $CONDA_ENV_NAME"
    else
      fail "conda create failed — install the tools manually (see below)"
    fi
  else
    warn "install the missing tools with one of:"
    echo "      sudo apt install$APT_PACKAGES                    # Debian/Ubuntu"
    echo "      conda install -c conda-forge -c bioconda mafft primer3 blast"
    echo "      brew install mafft primer3 blast                 # macOS"
    echo "      (or re-run: bash install.sh --with-apt   /   --with-conda)"
  fi
fi
echo

# ─── 3. Verify ───────────────────────────────────────────────────────────────
bold "3/3  Verifying"

if PRIMER_DATA_DIR="$(mktemp -d)" .venv/bin/python tests/test_entrez_query.py >/dev/null 2>&1 &&
   PRIMER_DATA_DIR="$(mktemp -d)" .venv/bin/python tests/test_db.py >/dev/null 2>&1; then
  ok "offline tests pass (query builder, run database)"
else
  warn "offline tests did not pass — run them directly to see why:"
  echo "      .venv/bin/python tests/test_entrez_query.py"
  echo "      .venv/bin/python tests/test_db.py"
fi

if ((${#MISSING[@]} == 0)); then
  if .venv/bin/python tests/test_core_offline.py >/dev/null 2>&1; then
    ok "pipeline test passes (real MAFFT and Primer3)"
  else
    warn "pipeline test failed — run: .venv/bin/python tests/test_core_offline.py"
  fi
fi

echo
bold "Done."
if ((${#MISSING[@]} > 0)); then
  warn "${#MISSING[@]} external program(s) still missing; the app will start but"
  echo "    /api/health will report them and designs will fail until installed."
fi
echo "  Start the app:   ./run.sh          → http://127.0.0.1:8090"
echo "  Different port:  PORT=9000 ./run.sh"
echo "  Optional, for faster NCBI downloads:"
echo "      export NCBI_API_KEY=<your key>   # ncbi.nlm.nih.gov/account/settings"
echo "      export NCBI_EMAIL=you@example.com"
