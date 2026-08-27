# DeepSeek SWE-bench fresh cohort qualification

> Qualification evidence only. No model or agent was called, and no prior behavioral benchmark was rerun.

## Decision

**1/3 QUALIFIED — STOP PILOT.** Do not run DeepSeek on this cohort.

The cohort was frozen before environment testing in commits `5b1d832` (candidate selection) and `c60e212` (exact profile and trusted plans). No instance was replaced or reordered.

| Frozen order | Instance | Gold F2P / P2P | Security | Contamination | Six-tool smoke | Verdict |
|---:|---|---:|---:|---:|---:|---|
| 1 | `pytest-dev__pytest-8399` | 1/1 · 59/59 | PASS | PASS | FAIL: frozen test plan | **QUALIFICATION FAIL** |
| 2 | `psf__requests-1724` | 6/6 · 79/79 | PASS | PASS | FAIL: frozen test plan | **QUALIFICATION FAIL** |
| 3 | `matplotlib__matplotlib-22719` | 1/1 · 68/68 | PASS | PASS | PASS | **QUALIFIED** |

The pytest plan executes but fails because the image's installed `pkg_resources` emits a deprecation warning under that suite's warning policy. The Requests root suite contains httpbin network tests, so it fails under the required network-none sandbox. These outcomes were preserved; the plans were not substituted after qualification began.

## Frozen lineage and boundary

- SWE-bench 5.0.2; harness commit `7a21e05772954cc81471ae19d56f436cecf43c54`.
- Enriched Verified revision `78f471bf655a3137b2e8a75af1501690ec009ec3`; reference revision `c104f840cc67f8b6eec6f759ebc8b2693d585d4a`.
- Docker client/server 29.1.3, ARM64 host, official AMD64 images under Docker Desktop emulation.
- Setup acquisition used network only for pinned images/dataset metadata. Future agent runtime remains networkless.
- Model-visible tools remain exactly `list_files`, `search_code`, `read_file`, `apply_patch`, `run_tests`, and `git_diff`.
- The DeepSeek strict result remains **NOT COMPATIBLE, 9/12 (75%)**. The separate behavioral-admission result remains **ADMITTED FOR SMALL BEHAVIORAL PILOT**.

## Validation

- Official gold harness: 3 completed, 3 resolved, 0 infrastructure failures, 0 errors.
- Security: all 16 recorded assertions passed for every instance, including non-root, network-none, read-only root, dropped capabilities, bounded resources, restricted mounts, and no credentials.
- Contamination: no gold/test patch content, forbidden artifact names, or configured credential values appeared in model-visible worktrees or outputs.
- Regression: 181 tests passed; controlled benchmark 12/12; reliability 8/8; reference integrity 5/5; structural retrieval remained the default and retained recall@5 1.0.

Resource measurements and immutable image digests are recorded in the JSON checkpoint. Memory measurements are coarse local samples, not guaranteed true peaks or production claims.

## Next step

Stop here. Preserve this failed frozen cohort. If another cohort is desired, define a new predeclared selection/test-plan policy in a separate task; do not retrofit or replace these three instances.
