# Verification — 2026-10-09

- Local Python 3.13: `python -m unittest discover -s tests -v` — 39 tests passed.
- Native Tkinter GUI smoke under Xvfb: invoked offline demo button, observed success dialog, verified one XLSX produced; exited 0.
- Tests use synthetic HTML/CSV and mocked HTTP responses; no claimed live network coverage.
- Live dataset downloads and real forum parsing were not executable in the network-isolated development container.
- Windows launcher supplied, not executed on Windows here.
- Final review: self-review; no independent reviewer tool available.
- Review regressions fixed test-first: malformed history counter must not abort a run; pathological oversized counters must be ignored.
- Deliberate v1 boundary: CrUX snapshot + Majestic CSV + conservative public counters. No paid APIs, private content bypass, database or hosted application.
- Trade-off: no parent-domain fallback; unknown subforums remain unknown rather than inheriting unrelated traffic. Sparse closed forums require administrator statistics.
