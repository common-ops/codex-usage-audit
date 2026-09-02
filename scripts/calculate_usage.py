#!/usr/bin/env python3
"""Read-only audit of token usage stored in local Codex JSONL rollouts."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TOKEN_FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def zero_usage() -> dict[str, int]:
    return {key: 0 for key in TOKEN_FIELDS}


def add_usage(target: dict[str, int], source: dict[str, int]) -> None:
    for key in TOKEN_FIELDS:
        target[key] += int(source.get(key) or 0)


def subtract_usage(newer: dict[str, int], older: dict[str, int]) -> dict[str, int]:
    return {key: max(0, int(newer.get(key) or 0) - int(older.get(key) or 0)) for key in TOKEN_FIELDS}


@dataclass
class Event:
    at: datetime
    usage: dict[str, int]
    percent: float | None
    resets_at: int | None


@dataclass
class Session:
    sid: str
    started: datetime
    kind: str
    parent: str | None
    events: list[Event] = field(default_factory=list)
    title: str = ""

    @property
    def ended(self) -> datetime:
        return self.events[-1].at if self.events else self.started


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return rows


def source_parent(source: Any) -> str | None:
    if not isinstance(source, dict):
        return None
    spawn = source.get("subagent", {}).get("thread_spawn", {})
    return spawn.get("parent_thread_id") or source.get("parent_thread_id")


def load_titles(path: Path) -> dict[str, str]:
    titles: dict[str, str] = {}
    for row in read_jsonl(path):
        if row.get("id") and row.get("thread_name"):
            titles[row["id"]] = row["thread_name"]
    return titles


def load_sessions(root: Path, titles: dict[str, str]) -> dict[str, Session]:
    sessions: dict[str, Session] = {}
    for path in sorted(root.rglob("*.jsonl")):
        rows = read_jsonl(path)
        metas = [row.get("payload", {}) for row in rows if row.get("type") == "session_meta"]
        if not metas:
            continue
        meta = metas[0]
        sid, timestamp = meta.get("id") or meta.get("session_id"), meta.get("timestamp")
        if not sid or not timestamp:
            continue
        events: list[Event] = []
        for row in rows:
            payload, stamp = row.get("payload", {}), row.get("timestamp")
            info = payload.get("info") or {}
            usage = info.get("total_token_usage")
            if row.get("type") != "event_msg" or payload.get("type") != "token_count" or not usage or not stamp:
                continue
            primary = (payload.get("rate_limits") or {}).get("primary") or {}
            events.append(Event(parse_time(stamp), usage, primary.get("used_percent"), primary.get("resets_at")))
        events.sort(key=lambda event: event.at)
        candidate = Session(sid, parse_time(timestamp), meta.get("thread_source", "unknown"), source_parent(meta.get("source")), events, titles.get(sid, sid))
        previous = sessions.get(sid)
        if previous is None or (candidate.events and (not previous.events or candidate.events[-1].usage.get("total_tokens", 0) > previous.events[-1].usage.get("total_tokens", 0))):
            sessions[sid] = candidate
    return sessions


def assign_orphans(sessions: dict[str, Session]) -> None:
    users = [session for session in sessions.values() if session.kind == "user"]
    for session in sessions.values():
        if session.kind == "user" or session.parent:
            continue
        candidates = [user for user in users if user.started <= session.started <= user.ended + timedelta(minutes=2)]
        if candidates:
            session.parent = min(candidates, key=lambda user: abs((session.started - user.started).total_seconds())).sid


def root_id(session: Session, sessions: dict[str, Session]) -> str:
    current, seen = session, {session.sid}
    while current.parent and current.parent in sessions and current.parent not in seen:
        seen.add(current.parent)
        current = sessions[current.parent]
    return current.sid


def usage_between(session: Session, start: datetime, end: datetime) -> dict[str, int]:
    before = [event for event in session.events if event.at < start]
    within = [event for event in session.events if start <= event.at <= end]
    if not within:
        return zero_usage()
    return subtract_usage(within[-1].usage, before[-1].usage if before else zero_usage())


def detect_current_window(sessions: dict[str, Session]) -> tuple[datetime | None, int | None]:
    events = sorted((event for session in sessions.values() for event in session.events if event.resets_at), key=lambda event: event.at)
    if not events:
        return None, None
    reset_epoch = max(event.resets_at for event in events if event.resets_at is not None)
    matching = [event for event in events if event.resets_at is not None and abs(event.resets_at - reset_epoch) <= 5]
    return matching[0].at, reset_epoch


def aggregate(sessions: dict[str, Session], start: datetime, end: datetime, include_meter: bool = False) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    percents: dict[str, list[float]] = defaultdict(list)
    for session in sessions.values():
        root, usage = root_id(session, sessions), usage_between(session, start, end)
        if not usage["total_tokens"]:
            continue
        root_session = sessions.get(root, session)
        row = grouped.setdefault(root, {"id": root, "title": root_session.title, "usage": zero_usage(), "components": 0})
        add_usage(row["usage"], usage)
        row["components"] += 1
        if include_meter:
            percents[root].extend(event.percent for event in session.events if start <= event.at <= end and event.percent is not None)
    for root, row in grouped.items():
        values = percents[root] if include_meter else []
        row["observed_active_meter_increase"] = max(values) - min(values) if values else None
    return sorted(grouped.values(), key=lambda row: row["usage"]["total_tokens"], reverse=True)


def summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    result = zero_usage()
    for row in rows:
        add_usage(result, row["usage"])
    return result


def markdown_section(name: str, start: datetime, end: datetime, rows: list[dict[str, Any]]) -> list[str]:
    total = summary(rows)
    lines = [f"## {name}", "", f"Window (UTC): {start.isoformat()} to {end.isoformat()}", f"Total raw tokens: {total['total_tokens']:,}", "", "| Task | Raw total | Input | Cached input | Output | Reasoning | Active meter increase |", "|---|---:|---:|---:|---:|---:|---:|"]
    for row in rows:
        usage, meter = row["usage"], row["observed_active_meter_increase"]
        meter_text = "n/a" if meter is None else f"{meter:g} pt"
        lines.append(f"| {row['title'].replace('|', '/')} | {usage['total_tokens']:,} | {usage['input_tokens']:,} | {usage['cached_input_tokens']:,} | {usage['output_tokens']:,} | {usage['reasoning_output_tokens']:,} | {meter_text} |")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions-root", type=Path, default=Path.home() / ".codex" / "sessions")
    parser.add_argument("--session-index", type=Path, default=Path.home() / ".codex" / "session_index.jsonl")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    args = parser.parse_args()
    sessions = load_sessions(args.sessions_root, load_titles(args.session_index))
    assign_orphans(sessions)
    now = datetime.now(timezone.utc)
    calendar_start = now - timedelta(days=args.days)
    current_start, reset_epoch = detect_current_window(sessions)
    calendar_rows = aggregate(sessions, calendar_start, now)
    current_rows = aggregate(sessions, current_start, now, include_meter=True) if current_start else []
    result = {
        "analyzed_at": now,
        "oldest_session": min((session.started for session in sessions.values()), default=None),
        "current_window_observed_start": current_start,
        "current_window_resets_at": datetime.fromtimestamp(reset_epoch, timezone.utc) if reset_epoch else None,
        "last_days": {"days": args.days, "start": calendar_start, "end": now, "totals": summary(calendar_rows), "tasks": calendar_rows},
        "current_window": {"start": current_start, "end": now, "totals": summary(current_rows), "tasks": current_rows},
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value)))
    else:
        print(f"Analyzed at (UTC): {now.isoformat()}")
        if current_start:
            print(f"Observed current-window boundary (UTC): {current_start.isoformat()}")
            print(f"Scheduled reset (UTC): {datetime.fromtimestamp(reset_epoch, timezone.utc).isoformat()}")
        else:
            print("Observed current-window boundary: unavailable")
        print("\n" + "\n".join(markdown_section(f"Last {args.days} calendar days", calendar_start, now, calendar_rows)))
        if current_start:
            print("\n" + "\n".join(markdown_section("Current observed weekly window", current_start, now, current_rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
