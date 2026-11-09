"""Comparison of two parsed exports of the same village (pure)."""

from __future__ import annotations

from dataclasses import dataclass

from clashpilot.ingest.state import VillageSnapshot


@dataclass(frozen=True)
class Upgrade:
    data_id: int
    from_level: int
    to_level: int


@dataclass(frozen=True)
class Presence:
    """An item that exists in one snapshot only."""

    data_id: int
    level: int | None
    count: int


@dataclass(frozen=True)
class JobChange:
    data_id: int
    to_level: int
    finish_at: int


@dataclass(frozen=True)
class SnapshotDiff:
    upgraded: tuple[Upgrade, ...]
    added: tuple[Presence, ...]
    removed: tuple[Presence, ...]
    started: tuple[JobChange, ...]
    finished: tuple[JobChange, ...]


def _levels(snapshot: VillageSnapshot) -> dict[int, int | None]:
    """The highest level of each item (None when none of its entries has a level)."""
    levels: dict[int, int | None] = {}
    for item in snapshot.items:
        current = levels.get(item.data_id)
        if item.level is not None and (current is None or item.level > current):
            levels[item.data_id] = item.level
        else:
            levels[item.data_id] = current
    return levels


def _presence(snapshot: VillageSnapshot, data_ids: set[int]) -> tuple[Presence, ...]:
    levels = _levels(snapshot)
    counts: dict[int, int] = {}
    for item in snapshot.items:
        counts[item.data_id] = counts.get(item.data_id, 0) + item.count
    return tuple(Presence(i, levels[i], counts[i]) for i in sorted(data_ids))


def diff_snapshots(older: VillageSnapshot, newer: VillageSnapshot) -> SnapshotDiff:
    """What changed from `older` to `newer`.

    An item is identified by its `data_id`, a job by `(data_id, to_level)`. `finished` is a job of
    `older` absent from `newer` whose item reached the job's level; one that vanished without the
    level going up (cancelled) is not.
    """
    before, after = _levels(older), _levels(newer)
    upgraded: list[Upgrade] = []
    for data_id in sorted(before.keys() & after.keys()):
        old, new = before[data_id], after[data_id]
        if old is not None and new is not None and new > old:
            upgraded.append(Upgrade(data_id, old, new))
    old_jobs = {(job.data_id, job.to_level): job for job in older.running}
    new_jobs = {(job.data_id, job.to_level): job for job in newer.running}
    started = tuple(
        JobChange(job.data_id, job.to_level, job.finish_at)
        for key, job in sorted(new_jobs.items())
        if key not in old_jobs
    )
    finished: list[JobChange] = []
    for key, job in sorted(old_jobs.items()):
        reached = after.get(job.data_id)
        if key not in new_jobs and reached is not None and reached >= job.to_level:
            finished.append(JobChange(job.data_id, job.to_level, job.finish_at))
    return SnapshotDiff(
        upgraded=tuple(upgraded),
        added=_presence(newer, after.keys() - before.keys()),
        removed=_presence(older, before.keys() - after.keys()),
        started=started,
        finished=tuple(finished),
    )
