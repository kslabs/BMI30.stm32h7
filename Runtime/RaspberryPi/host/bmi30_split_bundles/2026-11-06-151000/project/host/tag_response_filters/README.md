# BMI30 tag response filters

The Tag Detection page lists every valid `.json` file in this directory.
The file name is the customer-visible filter name and may be changed freely.

Three filter slots are available. An empty slot is off. When several files are
selected, a candidate is accepted when any selected filter matches.

Each file uses this format:

```json
{
  "format": "bmi30-tag-response-filter-v1",
  "name": "Customer display name",
  "algorithm": "compact",
  "parameters": {
    "radius": 14,
    "frac": 0.22,
    "min_width": 4,
    "max_span": 40,
    "level_guard": "broad"
  }
}
```

Supported algorithms are `casino`, `barkhausen`, `microwire`, `paper`, and
`compact`. The first four retain the existing BMI30 detector behavior. The
generic `compact` algorithm accepts the parameters shown above; its
`level_guard` may be `broad`, `barkhausen`, or `none`.

Optional parameter overrides use the existing detector setting names, for
example `mark_gap`, `mark_gap_tol`, `mark_second_frac`,
`mark_window_start_frac`, `mark_window_end_frac`, `barkhausen_radius`,
`microwire_radius`, or `paper_radius`. Invalid files are omitted from the
selection list and never loaded by the detector.
