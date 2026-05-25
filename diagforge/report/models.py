"""Pydantic models that mirror claude/report-schema.json.

The JSON schema is the source of truth (CLAUDE.md rule 4). Any change to the
schema must precede a change here, and the round-trip tests in tests/unit/
exist to keep these in lockstep.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from diagforge.ingestion.models import DtcStandard

SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"

# Accept UUIDv4 or UUIDv7 — the schema requires v7 specifically; we constrain to that.
_UUID_V7_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TraceFormat = Literal["asc", "blf", "log", "csv"]
AnomalyType = Literal[
    "debounce_candidate",
    "power_cycle_burst",
    "signal_dropout",
    "value_spike",
    "communication_gap",
]
Confidence = Literal["low", "medium", "high"]


class ToolInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Literal["diagforge"] = "diagforge"
    version: str


class TraceFileInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    format: TraceFormat
    sha256: str
    duration_us: int | None = None
    event_count: int | None = None

    @field_validator("sha256")
    @classmethod
    def _valid_sha256(cls, value: str) -> str:
        if not _SHA256_RE.match(value):
            raise ValueError("sha256 must be 64 lowercase hex chars")
        return value


class DtcFileInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    sha256: str
    dtc_count: int | None = None

    @field_validator("sha256")
    @classmethod
    def _valid_sha256(cls, value: str) -> str:
        if not _SHA256_RE.match(value):
            raise ValueError("sha256 must be 64 lowercase hex chars")
        return value


class DbcFileInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str | None = None
    sha256: str | None = None

    @field_validator("sha256")
    @classmethod
    def _valid_sha256(cls, value: str | None) -> str | None:
        if value is not None and not _SHA256_RE.match(value):
            raise ValueError("sha256 must be 64 lowercase hex chars")
        return value


class InputInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trace_file: TraceFileInfo
    dtc_file: DtcFileInfo
    dbc_file: DbcFileInfo | None = None


class DtcInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    standard: DtcStandard
    status_byte: int | None = Field(default=None, ge=0, le=255)
    occurrence_count: int | None = Field(default=None, ge=1)
    first_seen_us: int | None = None
    last_seen_us: int | None = None
    description: str | None = None


class IntervalStats(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mean_us: float | None = None
    median_us: float | None = None
    p99_us: float | None = None


class SignalSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signal_name: str
    transition_rate_hz: float
    interval_stats: IntervalStats


class TransitionAnomaly(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signal_name: str
    anomaly_type: AnomalyType
    description: str
    evidence_us: list[int] = Field(default_factory=list)


class CrossSignalCorrelation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signal_a: str | None = None
    signal_b: str | None = None
    lag_us: int | None = None
    correlation_coefficient: float | None = Field(default=None, ge=-1.0, le=1.0)


class PatternFeatures(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_us: int
    signal_summaries: list[SignalSummary] = Field(default_factory=list)
    transition_anomalies: list[TransitionAnomaly] = Field(default_factory=list)
    correlations: list[CrossSignalCorrelation] = Field(default_factory=list)
    notable_findings: list[str] = Field(default_factory=list)


class Hypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rank: int = Field(ge=1)
    description: str
    confidence: Confidence
    evidence: list[str]
    suggested_pattern_id: str | None = None
    reasoning: str


class DiagnosticResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hypotheses: list[Hypothesis]
    model: str
    model_version: str
    prompt_template_version: str
    prompt_hash: str | None = None


class ParameterSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    suggested_value: str | float | None = None
    rationale: str | None = None


class MitigationMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pattern_id: str
    pattern_name: str
    parameter_suggestions: list[ParameterSuggestion] = Field(default_factory=list)
    verification_steps: list[str] = Field(default_factory=list)
    standards_references: list[str] = Field(default_factory=list)


class DtcAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dtc: DtcInfo
    pattern_features: PatternFeatures
    diagnostic_result: DiagnosticResult
    mitigation_matches: list[MitigationMatch] = Field(default_factory=list)


CrossDtcType = Literal["co_occurring", "causal_ordering"]


class CrossDtcFinding(BaseModel):
    """One cross-DTC relationship surfaced by `cross_dtc.detect_findings`."""

    model_config = ConfigDict(extra="forbid")
    type: CrossDtcType
    dtc_codes: list[str] = Field(min_length=2)
    description: str
    delta_us: int | None = None


class Report(BaseModel):
    """Top-level diagnostic evidence report."""

    model_config = ConfigDict(extra="forbid")

    report_id: str
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    created_at: dt.datetime
    tool: ToolInfo
    input: InputInfo
    analyses: list[DtcAnalysis] = Field(default_factory=list)
    cross_dtc_findings: list[CrossDtcFinding] = Field(default_factory=list)

    @field_validator("report_id")
    @classmethod
    def _valid_uuid_v7(cls, value: str) -> str:
        if not _UUID_V7_RE.match(value):
            raise ValueError("report_id must be a UUIDv7 (xxxxxxxx-xxxx-7xxx-Nxxx-xxxxxxxxxxxx)")
        return value
