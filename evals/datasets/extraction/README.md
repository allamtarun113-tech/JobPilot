# Requirement extraction eval

`starter.jsonl` holds 10 **synthetic** labelled JDs so the harness runs from day one.
The design's gate is 50 real JDs with hand-labelled requirements: add them as more `.jsonl` files with the same
fields (`id, title, jd, must, nice, years_min, years_max`, optional `is_job_description`).
Run before the scoring eval on every prompt or model change: `make eval`.
