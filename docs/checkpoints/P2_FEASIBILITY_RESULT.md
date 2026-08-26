# SWE-bench Verified feasibility and security spike

> Environment/security evidence only. No live-agent SWE-bench solve was attempted.

| Instance | Official gold | F2P | P2P | Security | Leakage | Six tools | Decision |
|---|---:|---:|---:|---:|---:|---:|---:|
| `pallets__flask-5014` | pass | 1/1 | 59/59 | 16/16 | pass | 6/6 | **PASS** |
| `psf__requests-5414` | fail | 1/1 | 126/130 | gated | gated | gated | **FAIL** |
| `pytest-dev__pytest-10051` | pass | 1/1 | 15/15 | 16/16 | pass | 6/6 | **PASS** |

Overall: **2/3 feasible**.

Recommendation: **REVIEW PARTIAL FEASIBILITY**. Do not proceed automatically to behavioral evaluation.

The failed Requests instance was not replaced. Its pinned official image produced 158 setup errors and four PASS_TO_PASS failures because a required HTTP test dependency was missing. Flask and pytest preserved network isolation, a non-root user, read-only root, dropped capabilities, no-new-privileges, CPU/memory/PID/time bounds, a single staged writable repository, no Docker socket or credentials, and the canonical six-tool interface.

Setup used network for pinned harness/dataset/image/repository acquisition. RepoPilot tool execution used Docker network mode `none` and received no API credentials. The official gold harness ran separately as controller-side environment validation. Full structured evidence is in `P2_FEASIBILITY_RESULT.json`.
