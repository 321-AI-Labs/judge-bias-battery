# Audit Trail

This folder is the complete internal audit trail behind the paper's numbers —
the verbatim judge-call logs and the full review chain, shipped so every
published figure can be re-derived and checked against the record.

## Contents

- `judge-corrections-REVIEW.md` — review of the judge-corrections derivation (correction-class assignment, counts).
- `REVIEW-P8.md` — adversarial review of the whole P8 battery and pipeline (131 items, defect D1 and its fix, acceptance record).
- `REVIEW-STEWARD.md` — data-steward review of artifact envelopes, provenance, and hash pins (1 accepted / 10 metadata-rejected, all resolved).
- `OPEN-ISSUES.md` — running issue register for the study (ISSUE-005, ISSUE-009, dispositions, root causes of record).
- `logs/battery-day2/` — main battery run: `judge-calls.jsonl` (741 verbatim judge calls, source sha256 `2e1eb9a0…`), runner stdout (main + top-up), per-class summary JSONs, the §4.2 recompute-diff artifact, the FT8 disposition, and the corrections derivation log (paths redacted).
- `logs/battery-day3-selfpref-grading/` — self-preference grading phase: verbatim judge calls, summary, derivation log (paths redacted).
- `logs/smoke/` — pre-flight smoke run: config, verbatim calls, summary.

## Integrity statement

Two files (`logs/*/derivation-log.txt`) contain path redactions
(`<lab-path>`); every other file is byte-identical to the lab original. The
table below lists the sha256 of each **source** file as archived in the lab.
Redacted copies can be verified against the hash-pinned lab archive on request.

| File (as shipped) | Source sha256 | Source bytes | Redactions |
|---|---|---|---|
| judge-corrections-REVIEW.md | 090b88a86093f562d0667b3382203993511e09d20aa3d398e61e463d71b217d2 | 4625 | 0 |
| REVIEW-P8.md | ff7412c6124fbc055ee1131e1debc7ce4c9884ac48819a5feede211d5c6ee0d7 | 23748 | 0 |
| REVIEW-STEWARD.md | 10b1872cd9af011131db81f8aab1235edaae5671b131aa9d2365c3ceac75847d | 15659 | 0 |
| OPEN-ISSUES.md | 6ac531e13fe86294effdd667fe66e107fad446662188991a853f6bc5740a1294 | 20364 | 0 |
| logs/battery-day2/FT8-DISPOSITION.json | cd0394e672f9c36ee59b01d2b362ded97785d65fbf612f7a636908753e7c87a4 | 3284 | 0 |
| logs/battery-day2/judge-calls.jsonl | 2e1eb9a04ded4185ad3beeb3f1893a5b8b744417866f54a20bcfbb31fbb93174 | 4324865 | 0 |
| logs/battery-day2/runner-stdout.log | 3cc75268b763db96ae1ad83025027d84b6318cb8a6d6e886ca400c76b2168826 | 12073 | 0 |
| logs/battery-day2/runner-topup-stdout.log | 46a53447249159e0c8d708b37dc776493617a018e14a80abc5a10bc314ae125a | 5647 | 0 |
| logs/battery-day2/summary-anchoring.json | a4eef8159646c8c88d53e48b15617ad499c64a79d642826d9970733871fd0f1e | 4388 | 0 |
| logs/battery-day2/summary-cbw-topup.json | 915cfb79626ca43e34c8cfc949837a068e2508772f31bfae29fcdfc9dd32f153 | 4864 | 0 |
| logs/battery-day2/summary-cbw.json | b25aac5b8dbcc23144a2db27a1e3c814bd2b16d400fca4e037d4098d3b830f4f | 4378 | 0 |
| logs/battery-day2/summary-position.json | 912f309fac20aa6e7430737034bb3734865209f387cebb53b590696a920e8877 | 4388 | 0 |
| logs/battery-day2/summary-recompute-diff.json | fc0bef2be9a1a41d755093182516765a4d4dbaa526536cb79c7f66c4b0bc40cd | 14665 | 0 |
| logs/battery-day2/summary-selfpref_generation.json | ad4ae0a47b73b4d8c03b2d84e7a3f57f0cc33a81455e866015b9037db620f4e7 | 4407 | 0 |
| logs/battery-day2/summary-verbosity-topup.json | 308bb6430b7da203d527389cf8f034d4f871dde35caf30defa85985ee0e792de | 4879 | 0 |
| logs/battery-day2/summary-verbosity.json | f1e8f05cbdf203739bd38c80eceaa92935b82daf7010fcf7f229b4b9d1ec7a07 | 4392 | 0 |
| logs/battery-day3-selfpref-grading/judge-calls.jsonl | b34776e9043e720fbd06a35267cdb4b3f556459d3aa4b92c4e1ebe202d4c28bd | 511563 | 0 |
| logs/battery-day3-selfpref-grading/summary-selfpref-grading.json | e0025c373c4ab59d80758fd93830897155f8883e7f578eeb3fedc444748a440e | 4883 | 0 |
| logs/smoke/judge-calls.jsonl | 7164b72a408bded1b51ba305faa8c0497a903dc848f6ad0b8b134f9a4d2ba870 | 190923 | 0 |
| logs/smoke/smoke-config.json | 70ead470c4aba8983104dcc2a79122dd7eaa6a308912c4174d1296b701f7df7b | 2325 | 0 |
| logs/smoke/smoke-summary.json | ce7b75cf025488a7051d42ef4403e691ab12aac03bbbe8b4125733e4777bb879 | 11572 | 0 |
| logs/battery-day2/derivation-log.txt | 9dc6a114e44ef50b0b6f4cf9d2cf1d3a2cdeea03b3cf8dd8a2ad19bbd7426343 | 5991 | 2 paths |
| logs/battery-day3-selfpref-grading/derivation-log.txt | ddb41a4c4c7c8b3fd4063b98b498c47b868afb57ea7e790551792bb74767174b | 1235 | 1 path |
