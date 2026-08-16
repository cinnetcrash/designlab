# DesignLab

Web-based PCR / qPCR primer design against a group of homologous sequences.

You paste a gene sequence (or a gene name), the app asks how many records to
download from NCBI, aligns them with MAFFT, locates the conserved blocks, and
lets Primer3 place primers **only inside those blocks**. Every oligo is then
checked base by base against every downloaded sequence, and the result is drawn:
which oligo sits where, on which strand, and which sequence carries a mismatch.

## Install

### Ubuntu (tested on 24.04 LTS)

Everything the app needs is in the standard Ubuntu archive — no PPA, no conda,
no compiling:

```bash
sudo apt update
sudo apt install -y git python3-venv python3-pip mafft primer3 ncbi-blast+

git clone https://github.com/cinnetcrash/primer-designer.git
cd primer-designer
bash install.sh
./run.sh                       # http://127.0.0.1:8090
```

Or let the installer pull the system packages itself — it prints the exact
`apt-get` line before running it, and only asks for sudo when something is
actually missing:

```bash
git clone https://github.com/cinnetcrash/primer-designer.git
cd primer-designer && bash install.sh --with-apt && ./run.sh
```

What the apt packages provide, with the versions this was verified against on
24.04:

| Package | Provides | Version on 24.04 |
|---|---|---|
| `mafft` | `mafft` — multiple sequence alignment | 7.505-1 |
| `primer3` | `primer3_core` and `/etc/primer3_config` | 2.6.1-4 |
| `ncbi-blast+` | `blastn`, `makeblastdb` | 2.12.0+ds-4build2 |
| `python3-venv` | the `venv` module `install.sh` needs | 3.12.3 |

Ubuntu 24.04 ships Python 3.12, which the app runs on — the offline test suites
and the server were verified on it. **22.04 ships Python 3.10**: the source
parses cleanly under 3.10 and uses no 3.11-only stdlib call, but it has not been
run there. On 22.04 either install a newer interpreter
(`sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt install python3.12-venv`,
then `PYTHON=python3.12 bash install.sh`) or use the conda route below.

Ubuntu's `ncbi-blast+` is 2.12 while bioconda ships 2.16. Both work here; 2.16
is only worth chasing if you also use BLAST elsewhere and want one version.

### Windows

**These scripts have not been run on Windows itself.** What has been done:
they were parsed by PowerShell 7.6.5, checked with PSScriptAnalyzer (clean apart
from `PSAvoidUsingWriteHost`, which is the point of a coloured installer), and
*executed end to end* under `pwsh` on Linux — `install.ps1` creates the venv,
installs the packages, detects the tools and runs the verification suite, and
`run.ps1` starts the server and serves `/api/health`. The `-WithTools` path was
run against the real Primer3 Windows archive.

That leaves Windows-only behaviour untested: path handling under `cmd.exe`,
the MAFFT `.bat` wrapper, and NCBI's BLAST+ installer. Report what breaks
rather than working around it.

```powershell
git clone https://github.com/cinnetcrash/primer-designer.git
cd primer-designer
.\install.ps1                 # add -WithTools to fetch the portable Primer3 build
.\run.ps1                     # http://127.0.0.1:8090
```

If PowerShell refuses to run them, that is the execution policy, not the script:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

The three external programs, all verified to exist as Windows builds:

| Tool | Where | Note |
|---|---|---|
| Primer3 | [`primer3-2.6.1_exe_for_windows.zip`](https://github.com/primer3-org/primer3/releases) | Official release asset; `-WithTools` downloads and unpacks this one |
| BLAST+ | [`ncbi-blast-<version>+-win64.exe`](https://ftp.ncbi.nlm.nih.gov/blast/executables/blast+/LATEST/) | NCBI installer, or the `x64-win64.tar.gz` portable archive |
| MAFFT | [all-in-one package](https://mafft.cbrc.jp/alignment/software/windows.html) | MAFFT's own documentation notes it "may be slow, depending on anti-virus software" and lacks RNA structural alignment |

Two Windows details the code handles: the MAFFT all-in-one package installs
`mafft.bat`, and Python's subprocess cannot execute a `.bat` directly
(WinError 193), so batch files are dispatched through `cmd.exe`; and the Windows
Primer3 zip keeps `primer3_config` next to `primer3_core.exe`, which is now one
of the places the thermodynamic parameter directory is searched for.

MAFFT's maintainers recommend the WSL build over the all-in-one one for speed.
If you hit that, `wsl --install` followed by the Ubuntu instructions above is
the fallback — it is the same application, and that path is tested.

### Other systems

```bash
git clone https://github.com/cinnetcrash/primer-designer.git
cd primer-designer && bash install.sh --with-conda
./run.sh                # http://127.0.0.1:8090
PORT=9000 ./run.sh      # different port
```

`install.sh` creates the project venv, installs the Python packages, checks the
four external programs and prints exactly how to get any that are missing, then
runs the offline tests so you know the install works before you open the
browser. With no flag it only reports on missing tools; `--with-apt` and
`--with-conda` install them.

### Keeping it running (optional)

For a lab machine that should serve the app after a reboot, `deploy/designlab.service`
is a systemd user unit with the install commands in its header. It binds to
127.0.0.1 deliberately: **the app has no authentication**, so it must not be
exposed on a network without a reverse proxy that provides one. `HOST=0.0.0.0 ./run.sh`
does work, but that is a decision about who can reach your NCBI quota and your
run history, not just a convenience flag.

### What it needs

| | |
|---|---|
| Python | 3.11 and 3.12 verified; 3.10 parses but is untested |
| Python packages | fastapi, uvicorn, biopython, pydantic — installed into `.venv/` by the installer |
| External programs | `mafft`, `primer3_core`, `blastn`, `makeblastdb` on PATH |
| Network | NCBI access (BLAST and Entrez); everything else runs locally |
| Disk | ~160 MB for `.venv/`, plus a few MB per run under `data/` |

Installing the external programs by hand, if you prefer:

```bash
conda install -c conda-forge -c bioconda mafft primer3 blast   # any OS
sudo apt install mafft primer3 ncbi-blast+                     # Debian/Ubuntu
brew install mafft primer3 blast                               # macOS
```

`/api/health` reports which programs are missing and which versions are in use;
the same versions are written into the methods block of every result. The
header shows a green badge when all four are present.

### Manual install

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./run.sh
```

The app deliberately uses a project-local `.venv/` rather than whatever Python
is active: conda base environments often carry a starlette version FastAPI
cannot use, and installing into the base environment could break other tools on
the machine.

### Optional

```bash
export NCBI_API_KEY=<key>          # ncbi.nlm.nih.gov/account/settings — lifts
export NCBI_EMAIL=you@example.com  # the Entrez rate limit from 3 to 10 req/s
export PORT=9000                   # default 8090
export PRIMER_DATA_DIR=/data/pd    # where runs and the database are kept
```

### Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Header badge says a tool is missing | That binary is not on PATH. If you installed with `--with-conda`, run `conda activate designlab` first, then `./run.sh` |
| `Router.__init__() got an unexpected keyword argument` on startup | The app is running on a Python that has an incompatible starlette. Use `./run.sh`, which picks `.venv/bin/python` |
| Search hangs for minutes | A sequence search runs at NCBI and typically takes 30-90 s. The header badge shows what is running; the gene-name search answers in seconds |
| History is empty after moving the machine | The database is an index — `POST /api/history/rebuild` re-reads `data/jobs/` |
| `ensurepip is not available` / venv cannot be created (Ubuntu) | `sudo apt install python3-venv`. Ubuntu ships `python3` without the venv module |
| `externally-managed-environment` from pip (Ubuntu 23.04+) | You are pip-installing into the system Python. Use `./run.sh` / `install.sh`, which install into `.venv/` |
| `primer3_core: cannot find thermodynamic parameters` | The `primer3` apt package puts them in `/etc/primer3_config`, which the app finds automatically. On a manual build, set `PRIMER3_CONFIG=/path/to/primer3_config` |

## Using it

**Two ways to find sequences.** Paste a gene sequence and it is BLASTed at NCBI,
which gives identity, coverage and — importantly — the coordinates of the hit,
so only the matching locus is downloaded rather than a whole chromosome. Or type
a gene name: `invA`, optionally with an organism. No Entrez field tags are
needed; the backend assembles the query and the page shows it as you type, so
what actually gets searched is never hidden:

```
("invA"[Gene] OR "invA"[Title]) AND "Salmonella enterica"[Organism]
  AND 1500:5000[SLEN] NOT "wgs master"[Properties]
```

The gene name is matched against both the gene field and the record title, since
plenty of records carrying a gene are not annotated with it as a gene symbol.
The length window and the WGS-master exclusion are always applied — master
records describe a sequencing project and contain no sequence, so they would
fail at download. Tick *"Entrez sorgusunu kendim yazacağım"* to write the query
by hand instead.

**You choose how many records to pull.** The search returns candidates in a
table; pick them individually or take the best N.

**You can see what is running.** While a job runs, the page names the external
program currently executing — NCBI Entrez, efetch, blastn, MAFFT, Primer3 — with
the seconds it has been in that step and the pipeline stages ticked off as they
complete. The header badge shows the same thing from anywhere in the app, so a
long BLAST is never a silent wait. `GET /api/jobs/active` returns it as JSON.

## Pipeline

| Step | Tool | What happens |
|---|---|---|
| 1. Homologue search | NCBI BLAST URL API (sequence input) or Entrez esearch (gene-name input) | Candidate accessions with identity / coverage / E-value. WGS master records are filtered out — they carry no sequence. `blastn -remote` is deliberately not used: it submits the job but never returns a result on this host, while the URL API answers the same query in about 30 s |
| 2. Download | Entrez efetch | Only the aligned locus ±200 bp is pulled when BLAST supplied coordinates, so a hit on a whole chromosome does not drag in megabases |
| 3. Homology trimming | `blastn` (local) | Each record is cut back to the region matching the anchor. Without this, whole-contig records drag unrelated flanks into the alignment and the conserved blocks break into ~9 bp fragments |
| 4. Alignment | MAFFT `--auto --adjustdirection` | Direction adjustment is required: NCBI contigs carry the gene on either strand, and a plus-strand copy aligned against a minus-strand one yields no conserved columns at all |
| 5. Coverage filter | in-house | Records covering less than `min_record_coverage` of the alignment are excluded from the conservation calculation (still validated against). A contig truncated mid-gene would otherwise veto every conserved column on its own |
| 6. Conservation | in-house | Per-column identity, gap fraction and Shannon entropy; contiguous runs above the threshold become conserved blocks |
| 7. Design | Primer3 `primer3_core` | Runs on the reference with the variable stretches passed as `SEQUENCE_EXCLUDED_REGION` |
| 8. Validation | in-house + `blastn -task blastn-short` | Per-sequence mismatch map, per-sequence product size, dimer/hairpin geometry, off-target sites within the downloaded set |

### How the anchor is chosen

With a pasted query sequence, that sequence is the anchor. For a gene-name
search there is none, and two obvious heuristics both fail on real records:
the *shortest* record may share only a few hundred bases with the rest, so
everything else gets trimmed to that sliver; the *highest total bitscore*
record is simply the longest contig, which reinstates the flanks trimming
exists to remove. The anchor is therefore built rather than picked — take the
record matching the most others, then keep only the part of it covered by at
least half of them. That region is the shared core.

### Why excluded regions rather than a cut-out fragment

Cutting the conserved block out and designing on the fragment loses the
coordinate frame — the returned primer positions no longer refer to anything you
can draw. Passing the variable stretches as `SEQUENCE_EXCLUDED_REGION` keeps the
design on the full reference, so every primer position maps back to an alignment
column and can be read off each sequence. It is also biologically correct:
only the primer *binding sites* need to be conserved, while the amplicon
interior is free to vary.

## What the numbers mean

Two kinds of values are reported and they are never mixed:

* **Base-pairing geometry** — mismatch counts, per-sequence binding maps, the
  dimer and hairpin diagrams. Computed here by exact comparison over the
  alignment columns and a Watson-Crick pairing scan. These are counts, not
  predictions.
* **Thermodynamics** — Tm, hairpin/self-dimer/pair-complementarity temperatures.
  Taken from Primer3 (`PRIMER_THERMODYNAMIC_OLIGO_ALIGNMENT=1`) only. The app
  never computes a thermodynamic value of its own.

The 3'-end window is the last 5 nt; a mismatch inside it is flagged separately
because it blocks extension far more effectively than an internal mismatch.

## Limits

* The specificity check searches **only the downloaded sequences**. It is not a
  genome-wide or host-background check — a primer that also binds somewhere in a
  host genome cannot be seen here. Run a full `blastn` against the relevant
  background separately before ordering.
* `blastn -remote` runs on NCBI servers and typically takes 1-5 minutes; it can
  fail when NCBI is busy. The gene-name (Entrez) route is fast but returns no
  identity/coverage figures.
* Records longer than 50 kb are skipped at download, and a job accepts at most
  200 sequences.
* Primers are designed for research use. Nothing here replaces wet-lab
  validation.

## Run history

Every run is indexed in a SQLite database at `data/runs.sqlite3`, and the
**Geçmiş** button in the header lists them: date, gene, mode, sequence count,
conserved bp, pair count, best coverage, runtime and status. A finished run can
be reopened straight into the results view with all its visualisations, and
failed runs are kept too, with the reason they failed.

The search box matches the gene label, an accession, or **an oligo sequence** —
paste a primer back in and it finds the run that designed it.

The database is an index, not the source of truth. Everything a run produced
lives in its job directory; if the database is deleted it is rebuilt from those
directories at startup, or on demand with `POST /api/history/rebuild`. Deleting
a run from the history removes the index row only — the files stay on disk, and
the next rebuild indexes them again.

| Table | Contents |
|---|---|
| `runs` | One row per run: status, timings, gene, mode, block/pair counts, settings JSON, error |
| `run_primers` | Every oligo with its Tm, GC, coordinates, product size and perfect-match percentage |
| `run_records` | The NCBI records each run used, with alignment coverage and whether they counted toward conservation |

## Outputs

Every run keeps its own directory under `data/jobs/<job_id>/`, nothing is
deleted afterwards:

| File | Content |
|---|---|
| `input.fasta` | Downloaded sequences as they entered the alignment |
| `aligned.fasta` | The MAFFT alignment |
| `primer3_input.txt` | The exact boulder-IO block sent to Primer3 |
| `results.json` | Full result incl. per-column conservation and per-sequence binding |
| `primers.tsv` | Flat table of every oligo with its metrics |
| `job.json` | Status and the complete run log |

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/search` | Start a homologue search; returns `job_id` |
| `POST /api/design` | Start a design run on chosen accessions; returns `job_id` |
| `GET /api/job/{id}` | Status, stage, progress, log tail |
| `GET /api/job/{id}/result` | Full result document |
| `GET /api/job/{id}/file/{name}` | Download a run artefact |
| `POST /api/validate-oligo` | Check a hand-written oligo against a finished job |
| `GET /api/entrez-query` | Preview the query plain words will produce |
| `GET /api/jobs/active` | What is running now and in which external program |
| `GET /api/history` | Past runs; `q` matches gene, accession or oligo sequence |
| `GET /api/history/{id}` | One run with its oligos and the records it used |
| `DELETE /api/history/{id}` | Drop a run from the index (files stay on disk) |
| `POST /api/history/rebuild` | Re-read the job directories into the database |
| `GET /api/health` | Tool availability and versions |

## Tools

* MAFFT — Katoh & Standley (2013) *Mol Biol Evol* 30:772-780. doi:10.1093/molbev/mst010
* Primer3 — Untergasser et al. (2012) *Nucleic Acids Res* 40:e115. doi:10.1093/nar/gks596
* BLAST+ — Camacho et al. (2009) *BMC Bioinformatics* 10:421. doi:10.1186/1471-2105-10-421
* Biopython — Cock et al. (2009) *Bioinformatics* 25:1422-1423. doi:10.1093/bioinformatics/btp163

## Tests

```bash
python3 tests/test_core_offline.py     # no network; runs real MAFFT and Primer3
python3 tests/test_entrez_query.py     # Entrez query builder, no network
python3 tests/test_diagnostics.py      # failure messages name the right cause
python3 tests/test_portability.py      # Windows branches, exercised from Linux
python3 tests/test_db.py               # run database, in a temporary data dir
node    tests/test_i18n.js             # TR/EN dictionaries and markup coverage
node    tests/test_viz_headless.js     # renders the newest job result in a stubbed DOM
node    tests/test_frontend_flow.js    # whole UI flow against a running server
node    tests/test_frontend_flow.js --blast --lang=en   # BLAST route, English UI
```

`install.sh` runs the offline ones for you at the end of the install.

`test_core_offline.py` checks that mutated windows never end up inside a
conserved block, that Primer3 coordinates match the reference sequence, that
reverse-primer coordinates reverse-complement correctly, and that a planted
substitution is reported at the right offset from the 3' end.

`test_diagnostics.py` is a regression test for failure messages that were wrong
in the field. A design that fails is normal; a design that fails and blames the
wrong constraint costs an afternoon of turning the wrong dial. It pins the real
counters from a run where a single 28 bp conserved block held 60 candidate
primers that all failed the GC window — the message must blame the block's base
composition, not its length.

`test_portability.py` fakes `os.name` and the tool locations to exercise the
Windows branches from Linux: batch files dispatched through `cmd.exe`, a
backslash `primer3_config` path reaching Primer3 with a trailing separator, and
no POSIX-only module imported anywhere in the backend. It cannot tell you
whether the Windows builds of MAFFT, Primer3 and BLAST+ behave — that needs a
Windows machine.

`test_i18n.js` fails if the two dictionaries drift apart, if a key is used but
undefined (or defined but unused), or if any Turkish string is hard-coded
outside the dictionary.

`test_entrez_query.py` checks that plain words become a valid query, that a raw
query overrides them but still gets the length and master-record filters, that
the master filter is never doubled, that a quote in a gene name cannot unbalance
the query, and that an empty request is refused rather than searching for
everything.

`test_db.py` checks the run index round-trips a design, finds a run from one of
its oligo sequences, does not duplicate rows when the same run is indexed twice,
keeps failed runs with their reason, and that deleting a run touches the index
only.

`test_frontend_flow.js` is the end-to-end one: it loads the real `app.js` and
`viz.js` into a DOM stub built from `index.html` and drives the same functions
the buttons call — search, hit table, design, result view, downloads, history,
reopening a past run. It needs the server running (`./run.sh`) and reaches NCBI.
It catches wiring bugs no backend test can see: a request field the frontend
never sends, a result key the renderers read under a different name, a step that
never becomes visible.

`test_viz_headless.js` runs the visualisation code against the most recent
`results.json` with a stubbed DOM. It asserts that the field names the renderers
read exist in the backend output, that the binding panel highlights exactly as
many mismatches as the data reports, and that the heat map emits one cell per
sequence per oligo — a renamed key would otherwise only surface as a blank panel
in the browser.
