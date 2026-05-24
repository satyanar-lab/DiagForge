# Architecture

## Design goals

1. **Explainability over opacity.** Every hypothesis must cite specific timing
   evidence drawn from the deterministic analyzer. The LLM cannot fabricate
   numeric observations — it can only reason over what the analyzer measured.
2. **Deterministic layers under the AI.** Layers 1 and 2 are 100% deterministic.
   Same input always produces same output. The LLM (Layer 3) operates only on
   structured pattern features, not raw traces.
3. **Composable mitigation patterns.** Each pattern in the library is a
   standalone YAML entry with explicit applicability rules. New patterns can
   be added without touching code.
4. **No vendor lock-in.** Industry-standard log formats only (`python-can`
   supported set). No proprietary CAN tools required to use the output.
5. **Auditable output.** Every hypothesis, every matched pattern, and every
   referenced standard clause is captured in the JSON report.

## The five layers

### Layer 1 — Trace Ingestion

**Responsibility:** Parse heterogeneous diagnostic logs into a normalized
event stream.

**Components:**
- `ingestion/base.py` — abstract `TraceParser` interface
- `ingestion/can_asc.py` — ASCII CAN log parser (built on `python-can`)
- `ingestion/can_blf.py` — Vector BLF (Phase 0 proper)
- `ingestion/uds.py` — UDS message decoder (Phase 0 proper)
- `ingestion/dtc_json.py` — DTC snapshot in our defined JSON format

**Normalized event schema (pydantic):**
```python
class TraceEvent(BaseModel):
    timestamp_us: int            # microseconds since trace start
    channel: int
    frame_id: int                # CAN ID
    is_extended: bool
    is_fd: bool
    dlc: int
    data: bytes
    decoded_signals: dict[str, float] | None  # if DBC was provided
```

```python
class DTCSnapshot(BaseModel):
    dtc_code: str                # e.g. "P0420", "U0100", or UDS DID hex
    standard: Literal["obd2", "uds", "j1939"]
    status_byte: int | None      # UDS status (ISO 14229-1 §11.3)
    timestamp_first_us: int
    timestamp_latest_us: int
    occurrence_count: int
    description: str | None
```

### Layer 2 — Pattern Analyzer (deterministic)

**Responsibility:** Compute statistical and structural features around DTC
occurrences. No AI in this layer.

**Components:**
- `analyzer/timing.py`:
  - Inter-event interval statistics (mean, median, p99) for relevant signals
  - Transition anomaly detection: signals with > N transitions in a short window
  - Value anomaly detection: brief dropouts/spikes outside rolling baseline
  - Power-cycle / supply-rail burst detection
- `analyzer/correlation.py`:
  - Multi-signal lag correlation (e.g. does a DTC fire ~7ms after an ignition-line toggle?)
  - Cross-channel anomaly detection (signal disagreement between redundant inputs)

**Output schema:**
```python
class PatternFeatures(BaseModel):
    dtc_code: str
    window_us: int               # analysis window around DTC events
    signal_summaries: list[SignalSummary]
    transition_anomalies: list[TransitionAnomaly]
    correlations: list[CrossSignalCorrelation]
    notable_findings: list[str]  # e.g. "engine_rpm dropped to <100 RPM 4 times within 200ms"
```

### Layer 3 — Diagnostic Agent (LLM)

**Responsibility:** Given `PatternFeatures` for a DTC, propose ranked root-cause
hypotheses with explicit reference to the feature evidence.

**Components:**
- `diagnostic/agent.py` — orchestration + retry
- `diagnostic/prompts.py` — versioned templates

**Algorithm:**
1. Build prompt from: DTC info, `PatternFeatures` (serialized), relevant
   standards excerpts (looked up by DTC type), and the available mitigation
   pattern IDs.
2. Call Claude with strict JSON-mode output.
3. Validate the response.
4. Each hypothesis must reference at least one observation from
   `notable_findings` — reject and retry if it doesn't.

**Output:**
```python
class Hypothesis(BaseModel):
    rank: int                    # 1 = most likely
    description: str
    confidence: Literal["low", "medium", "high"]
    evidence: list[str]          # quoted from notable_findings/anomalies
    suggested_pattern_id: str | None
    reasoning: str

class DiagnosticResult(BaseModel):
    dtc_code: str
    hypotheses: list[Hypothesis]
    model: str
    model_version: str
    prompt_template_version: str
```

### Layer 4 — Mitigation Recommender

**Responsibility:** Given hypotheses and the mitigation pattern library, return
matched patterns with parameter suggestions and a verification approach.

**Components:**
- `mitigation/library.py` — loads `claude/mitigation-patterns-*.yaml`
- `mitigation/recommender.py` — matches `Hypothesis.suggested_pattern_id` to
  library entries and computes parameter suggestions

**Pattern schema (YAML):**
```yaml
pattern_id: "duration_qualified_debounce"
name: "Duration-qualified debounce"
when_applies:
  - "Discrete signal toggles within the input's noise window"
  - "DTC fires on first transition without confirmation interval"
parameters:
  - name: qualification_time_ms
    suggestion_rule: "max observed bounce interval × 2, rounded to nearest 5ms"
  - name: confirmation_count
    suggestion_rule: "default 1; raise to 2 if noise persists"
verification:
  - "Inject sub-qualification-time pulses; verify no DTC fires"
  - "Inject pulse equal to qualification time; verify DTC fires"
standards:
  - "ISO 14229-1:2020 §11.3 (event qualification for fault confirmation)"
```

### Layer 5 — Evidence Report Emitter

**Responsibility:** Serialize everything into the canonical evidence format.

**Outputs:**
- `audit-bundle/report.json` — full structured report (matches `claude/report-schema.json`)
- `audit-bundle/report.html` — Jinja2-rendered human-readable summary
- `audit-bundle/manifest.json` — index + sha256 hashes

## Data flow (Phase 0-Lite)
analyze(trace.asc, dtcs.json)
├─ TraceParser.parse(trace.asc) → list[TraceEvent]
├─ DTCParser.parse(dtcs.json)   → list[DTCSnapshot]
└─ for each DTCSnapshot:
├─ PatternAnalyzer.compute(events, dtc) → PatternFeatures
├─ DiagnosticAgent.propose(features)    → DiagnosticResult
├─ MitigationRecommender.match(result)  → list[MitigationMatch]
└─ ReportEmitter.append(dtc, result, matches)
└─ ReportEmitter.finalize() → audit-bundle/

## Tech choice rationale

| Choice | Why |
|---|---|
| Python | Rich automotive ecosystem (`python-can`, `cantools`, `udsoncan`), no perf bottleneck for offline analysis |
| `python-can` | Industry-standard CAN log parsing — broadest format support |
| `cantools` | DBC parsing, well-maintained, used widely in automotive Python tooling |
| `udsoncan` | UDS protocol implementation aligned with ISO 14229 |
| Anthropic API | Direct LLM access; fits the developer-tool framing |
| pydantic v2 | Strict typing at boundaries; auto-generates JSON Schema; fast |
| Jinja2 | Plain HTML templating, no front-end build chain |
| YAML for patterns | Human-editable, diff-friendly, no compile step |

## What we deliberately don't do

- We don't read OEM-proprietary diagnostic file formats.
- We don't communicate with vehicles live.
- We don't certify any output — it's developer evidence, not a workshop verdict.
- We don't replace ISO 14229 / 26262 expertise — we make the routine 80% faster.

## Open ADR questions

- ADR-001: Do we ship pre-trained timing heuristics or learn thresholds per project?
- ADR-002: Should pattern matching be strict (pattern_id == library_id) or fuzzy (LLM ranks library matches)?
- ADR-003: What's our policy on reproducing DTC code lists from ISO standards?
- ADR-004: How do we handle multi-frame UDS responses (ISO-TP segmentation)?
