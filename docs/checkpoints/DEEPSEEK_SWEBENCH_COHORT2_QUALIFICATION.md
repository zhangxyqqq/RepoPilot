# DeepSeek SWE-bench Verified Cohort 2 qualification

> Qualification only. No model ran, no issue was solved, and no historical behavioral task was rerun.

## Decision

**5/5 QUALIFIED — READY FOR DEEPSEEK BEHAVIORAL PILOT.**

DeepSeek may receive one later frozen attempt on each of the five instances below, using the unchanged profile. It was not executed during qualification.

| Frozen order | Instance | Gold F2P / P2P | Test plan | Security | Contamination | Six tools | Verdict |
|---:|---|---:|---:|---:|---:|---:|---|
| 1 | `mwaskom__seaborn-3187` | 2/2 · 248/248 | PASS | 16/16 | PASS | 6/6 | **QUALIFIED** |
| 2 | `pydata__xarray-6938` | 1/1 · 430/430 | PASS | 16/16 | PASS | 6/6 | **QUALIFIED** |
| 3 | `pylint-dev__pylint-7277` | 1/1 · 122/122 | PASS | 16/16 | PASS | 6/6 | **QUALIFIED** |
| 4 | `sphinx-doc__sphinx-8551` | 1/1 · 32/32 | PASS | 16/16 | PASS | 6/6 | **QUALIFIED** |
| 5 | `sphinx-doc__sphinx-9230` | 1/1 · 44/44 | PASS | 16/16 | PASS | 6/6 | **QUALIFIED** |

## Frozen selection

The candidate pool and pre-screen rules were frozen at commit `ffdcae6` before image or test execution. Seventeen untouched instances across nine repository families entered the pool; seven passed the uniform environment-only screen. The five were then selected by the frozen SHA-256 ordering with no more than two per family and frozen at commit `3e3b755` before gold grading.

The screen considered only pinned image availability, exact-base reproducibility, architecture/local size, and whether one of at most five hash-ranked tracked test files passed as exact argv under the networkless sandbox. It did not inspect gold/test patches, expected fixes, issue difficulty, patch size, model history, or solve likelihood. Cohort 1’s warning and HTTP failures informed this general environment rule only.

All 17 pass/fail results and attempted paths are preserved in the JSON checkpoint. The two passing but unselected instances, `pydata__xarray-7229` and `pylint-dev__pylint-4661`, cannot be substituted later.

## Environment and controls

- SWE-bench 5.0.2; harness `7a21e05772954cc81471ae19d56f436cecf43c54`.
- Pinned Verified revisions: enriched `78f471bf…`, reference `c104f840…`.
- Docker 29.1.3 on ARM64; official AMD64 images under local emulation.
- Setup used network for pinned dataset/image acquisition; future agent runtime remains `--network none`.
- Exactly six model-visible tools; controller-owned argv-only test plans.
- Non-root, read-only root, all capabilities dropped, `no-new-privileges`, bounded CPU/memory/PIDs/time, staged worktree only, and no credentials or host answer mounts.
- Official gold: 5 submitted, 5 completed, 5 resolved, zero infrastructure or ambiguous failures.

## Regression and preservation

- Full pytest: **181/181 PASS**.
- Controlled deterministic: **12/12** task/public/hidden, F1 1.0, 72 calls, zero unnecessary.
- Reliability: **8/8**, including 6/6 recoverable scenarios.
- Explicit MCP/security/trace/redaction/taxonomy slice: **44/44 PASS**.
- Reference integrity: **5/5 PASS**, with live-agent success unavailable by design.
- Structural retrieval remains the default; credential scan passed.
- README SHA-256 remained `63e0d212…04d0d79`; CV and historical checkpoints were not modified.

## Limitations

This five-task cohort is not representative of SWE-bench Verified. The pre-screen intentionally examines at most five deterministic test files and can reject environments that might work with a different plan. Resource figures are local engineering measurements; gold memory sampling was coarse and missed both Sphinx runs. Qualification establishes that the examination environment and controls work, not that DeepSeek can solve the tasks.

## Next step

Stop after this checkpoint. A separate, explicitly authorized task may later execute exactly one DeepSeek attempt per frozen instance. Do not tune, replace, reorder, or reinterpret this cohort before that run.
