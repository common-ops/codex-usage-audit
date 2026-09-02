# Codex Usage Audit

A reusable Codex skill that audits token usage stored in local `~/.codex` JSONL session logs. It groups child-agent usage under parent tasks, separates a rolling calendar period from the currently observed weekly-reset window, and avoids treating raw tokens as subscription-weighted units.

## Install

Copy or clone this repository into your Codex skills directory:

```bash
git clone https://github.com/common-ops/codex-usage-audit.git ~/.codex/skills/codex-usage-audit
```

Restart Codex if the skill is not discovered immediately.

## Use

Ask Codex:

```text
$codex-usage-audit Audit this computer's current weekly Codex usage and the last 7 days.
```

The analyzer can also be run directly:

```bash
python3 scripts/calculate_usage.py --format markdown
python3 scripts/calculate_usage.py --format json
```

The analyzer is read-only and uses only the Python standard library.
