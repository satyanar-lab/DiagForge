# DTC Input Format

DiagForge expects DTC snapshots in a JSON file with this shape:

```json
{
  "dtcs": [
    {
      "dtc_code": "P0300",
      "standard": "obd2",
      "status_byte": null,
      "timestamp_first_us": 480000,
      "timestamp_latest_us": 690000,
      "occurrence_count": 1,
      "description": "Random/Multiple Cylinder Misfire Detected"
    }
  ]
}
```

## Field reference

| Field                | Required | Type             | Notes |
|----------------------|----------|------------------|-------|
| `dtc_code`           | yes      | string           | OBD-II (`P0300`), UDS DID hex (`U0100`), or J1939 SPN/FMI |
| `standard`           | yes      | enum             | `obd2`, `uds`, or `j1939` |
| `status_byte`        | no       | int 0-255 / null | UDS DTC status byte per ISO 14229-1 §11.3 (UDS only) |
| `timestamp_first_us` | yes      | int ≥ 0          | Microseconds since trace start; first time the DTC was observed |
| `timestamp_latest_us`| yes      | int ≥ first      | Microseconds since trace start; most recent occurrence |
| `occurrence_count`   | yes      | int ≥ 1          | How many times the DTC was observed in the window |
| `description`        | no       | string / null    | Human-readable label for the report |

## Common mistakes

- **Timestamps in seconds, not microseconds.** The trace events use microseconds; mixing units breaks the analyzer's correlation windows.
- **`timestamp_latest_us < timestamp_first_us`** — rejected at validation time.
- **`status_byte` set for an OBD-II DTC** — allowed by the schema, but ignored; status bytes are a UDS concept.
- **Multiple DTCs per array entry** — not supported. One DTC per object.

## Example: multiple DTCs in a single run

```json
{
  "dtcs": [
    { "dtc_code": "P0300", "standard": "obd2", "timestamp_first_us": 480000, "timestamp_latest_us": 690000, "occurrence_count": 1 },
    { "dtc_code": "U0100", "standard": "uds", "status_byte": 47, "timestamp_first_us": 1200000, "timestamp_latest_us": 1850000, "occurrence_count": 3 }
  ]
}
```
