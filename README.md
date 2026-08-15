# Primer Designer

Web-based PCR / qPCR primer design against a group of homologous sequences.

You paste a gene sequence (or a gene name), the app asks how many records to
download from NCBI, aligns them with MAFFT, locates the conserved blocks, and
lets Primer3 place primers **only inside those blocks**. Every oligo is then
checked base by base against every downloaded sequence, and the result is drawn:
which oligo sits where, on which strand, and which sequence carries a mismatch.

## Running

```bash
./run.sh                # http://127.0.0.1:8090
PORT=9000 ./run.sh      # different port
```

`run.sh` uses the project venv at `.venv/` (created once with
`python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`). The base
conda environment on this machine ships a starlette version FastAPI cannot use,
which is why the app has its own venv rather than running on the base env.

External binaries must be on PATH: `mafft`, `primer3_core`, `blastn`,
`makeblastdb`. `/api/health` reports what is missing and which versions are in
use; the same versions are written into the methods block of every result.

## Pipeline

| Step | Tool | What happens |
|---|---|---|
| 1. Homologue search | `blastn -remote` (sequence input) or Entrez esearch (gene-name input) | Candidate accessions with identity / coverage / E-value. WGS master records are filtered out — they carry no sequence |
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
python3 tests/test_db.py               # run database, in a temporary data dir
node    tests/test_viz_headless.js     # renders the newest job result in a stubbed DOM
```

`test_core_offline.py` checks that mutated windows never end up inside a
conserved block, that Primer3 coordinates match the reference sequence, that
reverse-primer coordinates reverse-complement correctly, and that a planted
substitution is reported at the right offset from the 3' end.

`test_db.py` checks the run index round-trips a design, finds a run from one of
its oligo sequences, does not duplicate rows when the same run is indexed twice,
keeps failed runs with their reason, and that deleting a run touches the index
only.

`test_viz_headless.js` runs the visualisation code against the most recent
`results.json` with a stubbed DOM. It asserts that the field names the renderers
read exist in the backend output, that the binding panel highlights exactly as
many mismatches as the data reports, and that the heat map emits one cell per
sequence per oligo — a renamed key would otherwise only surface as a blank panel
in the browser.
