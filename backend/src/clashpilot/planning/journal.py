"""The journal of a village, replayed into what `compile_problem` needs (E11). Pure module.

An entry is `{type, at, task_id, payload}`. Its payload holds every value the replay reads, fixed
when the event was recorded (cost, slot and end of a start; lane, multiplier and duration of a
potion; value of a rune), so that a later pack never rewrites what already happened.

Entries are replayed by date. An event about a job dated before the export is already visible in
it and changes no job (the export shows the level it reached), but the cost of a start is paid
whenever it happened, after the stock observation it follows. A job of the journal keeps the task
id it was started under; `compile_problem` puts it back on its instance.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from clashpilot.gamedata.rules import PotionRule
from clashpilot.planning.compile import JobFact, JournalState, Payment, RunningRef, StockFact
from clashpilot.planning.objective import Objective

TASK_STARTED = "task_started"
TASK_FINISHED = "task_finished"
PRIORITY_CHANGED = "priority_changed"
ITEM_USED = "item_used"
RESOURCES_CORRECTED = "resources_corrected"
SCHEDULE_CHANGED = "schedule_changed"
GAME_DATA_UPDATED = "game_data_updated"
SNAPSHOT_IMPORTED = "snapshot_imported"
EVENT_TYPES = (
    TASK_STARTED,
    TASK_FINISHED,
    PRIORITY_CHANGED,
    ITEM_USED,
    RESOURCES_CORRECTED,
    SCHEDULE_CHANGED,
    GAME_DATA_UPDATED,
    SNAPSHOT_IMPORTED,
)

BOOK = "book"
POTION = "potion"
RUNE = "rune"


@dataclass(frozen=True)
class JournalEntry:
    entry_id: int
    type: str
    at: int
    task_id: str | None
    payload: Mapping[str, Any]
    plan_id: int | None = None


@dataclass(frozen=True)
class KnownJob:
    """A started job known at `now`: running in the export, or started through the journal."""

    task_id: str
    lane: str
    end: int
    start: int | None = None  # None: a job of the export, begun before it
    lane_slot: int | None = None
    journal: bool = False

    def runs_at(self, at: int) -> bool:
        return (self.start is None or self.start <= at) and at < self.end


def chronological(entries: Sequence[JournalEntry]) -> list[JournalEntry]:
    return sorted(entries, key=lambda entry: (entry.at, entry.entry_id))


def _ended(jobs: dict[str, KnownJob], name: str | None, at: int) -> None:
    job = jobs.get(name) if name is not None else None
    if job is not None and job.runs_at(at):
        jobs[job.task_id] = dataclasses.replace(job, end=at)


def _potion(jobs: dict[str, KnownJob], payload: Mapping[str, Any], at: int) -> None:
    rule = PotionRule(
        str(payload["item"]),
        str(payload["lane"]),
        int(payload["duration_multiplier"]),
        int(payload["duration_s"]),
    )
    for name, job in list(jobs.items()):
        if job.lane == rule.lane and job.runs_at(at):
            jobs[name] = dataclasses.replace(job, end=rule.end_after(at, job.end))


def replay(
    entries: Sequence[JournalEntry],
    running: Sequence[RunningRef],
    exported_at: int,
    carried: Mapping[str, tuple[int, int]] | None = None,
) -> tuple[JournalState, dict[str, KnownJob]]:
    """The journal state of `compile_problem` and every job known at `now`.

    `running` are the jobs of the export still running at `now`; `carried` gives, for some of them,
    the `(start, lane_slot)` an earlier plan listed, kept so that no event moves them.
    """
    carried = carried or {}
    jobs: dict[str, KnownJob] = {}
    for ref in running:
        start, slot = carried.get(ref.task_id, (None, None))
        jobs[ref.task_id] = KnownJob(ref.task_id, ref.lane, ref.end, start, slot)
    exported = {name: job for name, job in jobs.items()}
    stock: list[StockFact] = []
    payments: list[Payment] = []
    for entry in chronological(entries):
        payload, at = entry.payload, entry.at
        visible = at < exported_at  # the export already shows what this event did to a job
        if entry.type == TASK_STARTED:
            payments.append(Payment(at, {k: int(v) for k, v in payload["cost"].items()}))
            if not visible and entry.task_id is not None:
                jobs[entry.task_id] = KnownJob(
                    entry.task_id,
                    str(payload["lane"]),
                    int(payload["end"]),
                    at,
                    int(payload["lane_slot"]),
                    journal=True,
                )
        elif entry.type == TASK_FINISHED and not visible:
            _ended(jobs, entry.task_id, at)
        elif entry.type == ITEM_USED:
            effect = payload.get("effect")
            if effect == RUNE:
                stock.append(StockFact(str(payload["resource"]), at, int(payload["value"])))
            elif effect == BOOK and not visible:
                _ended(jobs, entry.task_id, at)
            elif effect == POTION and not visible:
                _potion(jobs, payload, at)
        elif entry.type == RESOURCES_CORRECTED:
            for name, value in sorted(payload["resources"].items()):
                stock.append(StockFact(str(name), at, int(value)))

    facts = tuple(
        JobFact(job.task_id, job.end, job.start, job.lane_slot)
        for name, job in sorted(jobs.items())
        if job != exported.get(name) or job.start is not None or job.lane_slot is not None
    )
    return JournalState(jobs=facts, stock=tuple(stock), payments=tuple(payments)), jobs


def objective_of(entries: Sequence[JournalEntry]) -> Objective | None:
    """The objective of the latest `priority_changed`, None if there is none."""
    latest = [entry for entry in chronological(entries) if entry.type == PRIORITY_CHANGED]
    if not latest:
        return None
    weights = latest[-1].payload["objective"]
    return Objective(**{name: float(value) for name, value in weights.items()})
