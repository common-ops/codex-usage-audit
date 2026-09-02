---
name: codex-usage-audit
description: Audit token usage recorded by the local Codex app, grouped by task and separated into rolling calendar and current weekly-reset windows. Use when comparing this computer's Codex sessions with the account usage meter. Do not present raw token counts as an exact subscription-limit percentage.
---

# Codex Usage Audit

Run the bundled analyzer instead of reconstructing totals manually:

```bash
python3 scripts/calculate_usage.py --format markdown
```

Use `--sessions-root` and `--session-index` when Codex data is outside the default `~/.codex` location. Use `--days N` for a period other than seven days. The script is read-only.

Report both sections returned by the script:

- `Last N calendar days`: usage recorded during that interval.
- `Current observed weekly window`: local usage after the most recent reset boundary visible in the logs.

Preserve these distinctions:

- Raw tokens are diagnostic counts, not subscription-weighted units. Never divide raw tokens by an invented fixed Pro/Plus token allowance.
- The account meter is account-wide. A meter increase between local sessions cannot be attributed to this computer.
- `observed_active_meter_increase` is only the rounded meter movement while local task logs were active. Describe it as an observation, not exact device attribution.
- Cached input, uncached input, output, and reasoning tokens have different implications. Keep the breakdown when requested.
- Include child agents in the parent task when the parent link is recorded. For guardian records without an explicit parent, the script assigns them only when their timestamp falls inside, or very near, a user task interval; otherwise it reports them separately.
- State when the oldest available log is newer than the requested start or when no reset boundary is observable.

Keep the final explanation concise and include the analysis timestamp, detected reset boundary, totals, task table, and material caveats.
