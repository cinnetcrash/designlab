"""Request/response schemas."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    """Step 1 — find candidate homologues on NCBI."""

    input_type: Literal["sequence", "query"] = "sequence"
    # input_type == "sequence": raw or FASTA nucleotide sequence
    # input_type == "query":    Entrez query, e.g. 'rpoB[Gene] AND Salmonella[Organism]'
    text: str = Field(..., min_length=3)
    database: Literal["nt", "core_nt", "refseq_rna"] = "core_nt"
    max_hits: int = Field(50, ge=1, le=200)
    min_identity: float = Field(80.0, ge=50.0, le=100.0)
    min_coverage: float = Field(60.0, ge=10.0, le=100.0)
    min_length: int = Field(200, ge=50)
    max_length: int = Field(20_000, ge=100)
    organism: str = ""          # optional Entrez organism filter for query mode


class Primer3Settings(BaseModel):
    mode: Literal["standard", "qpcr"] = "standard"
    primer_min_size: int = Field(18, ge=15, le=36)
    primer_opt_size: int = Field(20, ge=15, le=36)
    primer_max_size: int = Field(25, ge=15, le=36)
    primer_min_tm: float = Field(57.0, ge=40.0, le=80.0)
    primer_opt_tm: float = Field(60.0, ge=40.0, le=80.0)
    primer_max_tm: float = Field(63.0, ge=40.0, le=85.0)
    primer_min_gc: float = Field(40.0, ge=10.0, le=90.0)
    primer_max_gc: float = Field(60.0, ge=10.0, le=90.0)
    max_poly_x: int = Field(4, ge=2, le=10)
    gc_clamp: int = Field(1, ge=0, le=3)
    max_self_any_th: float = Field(45.0, ge=0.0, le=100.0)
    max_self_end_th: float = Field(35.0, ge=0.0, le=100.0)
    max_hairpin_th: float = Field(24.0, ge=0.0, le=100.0)
    max_pair_compl_any_th: float = Field(45.0, ge=0.0, le=100.0)
    max_pair_compl_end_th: float = Field(35.0, ge=0.0, le=100.0)
    salt_monovalent: float = Field(50.0, ge=0.0)
    salt_divalent: float = Field(1.5, ge=0.0)
    dntp_conc: float = Field(0.6, ge=0.0)
    dna_conc: float = Field(50.0, ge=1.0)
    product_min: int = Field(100, ge=40)
    product_max: int = Field(800, ge=60)
    num_return: int = Field(5, ge=1, le=20)
    # qPCR hydrolysis probe (internal oligo)
    probe_min_size: int = Field(18, ge=15, le=40)
    probe_opt_size: int = Field(22, ge=15, le=40)
    probe_max_size: int = Field(27, ge=15, le=40)
    probe_min_tm: float = Field(66.0, ge=45.0, le=90.0)
    probe_opt_tm: float = Field(70.0, ge=45.0, le=90.0)
    probe_max_tm: float = Field(74.0, ge=45.0, le=95.0)


class ConservationSettings(BaseModel):
    # A column counts as conserved when >= this fraction of sequences carry the
    # same base and the gap fraction stays below max_gap_fraction.
    identity_threshold: float = Field(1.0, ge=0.5, le=1.0)
    max_gap_fraction: float = Field(0.0, ge=0.0, le=0.5)
    min_block_length: int = Field(24, ge=15, le=500)
    # Reference sequence the primer coordinates are reported against.
    reference: Literal["consensus", "first", "query"] = "consensus"
    # Records covering less than this fraction of the alignment are excluded
    # from the conservation calculation (they are still validated against).
    min_record_coverage: float = Field(0.6, ge=0.0, le=1.0)


class DesignRequest(BaseModel):
    """Step 2 — download the chosen records, align, design."""

    accessions: list[str] = Field(..., min_length=1)
    # Optional per-accession subranges (1-based, inclusive) from the BLAST step.
    ranges: dict[str, list[int]] = Field(default_factory=dict)
    query_sequence: str = ""     # pasted sequence, included in the alignment
    query_label: str = "QUERY"
    gene_label: str = "target"
    primer3: Primer3Settings = Field(default_factory=Primer3Settings)
    conservation: ConservationSettings = Field(default_factory=ConservationSettings)
    specificity_check: bool = True   # local BLAST of primers vs the downloaded set
    # Cut every record back to the region matching the anchor before aligning.
    # Without this, whole-contig records drag unrelated flanks into the
    # alignment and the conserved blocks break into unusably short fragments.
    trim_to_homology: bool = True
    anchor_flank: int = Field(60, ge=0, le=1000)


class ValidatePrimerRequest(BaseModel):
    """Ad-hoc check of a user-supplied oligo against a finished job."""

    job_id: str
    sequence: str = Field(..., min_length=10)
    role: Literal["forward", "reverse", "probe"] = "forward"
