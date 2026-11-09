"""Comparison of two parsed exports of the same village (pure)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from clashpilot.ingest.state import RunningJob, VillageSnapshot

# Two sightings of one job, in two exports, are the same job when their absolute finish times are
# this close (the timers are rounded, and a boost may move them a little).
SAME_JOB_TOLERANCE_S = 120


@dataclass(frozen=True)
class Upgrade:
    """`count` instances of an item went from `from_level` to `to_level`."""

    data_id: int
    from_level: int
    to_level: int
    count: int = 1


@dataclass(frozen=True)
class Presence:
    """`count` instances at `level` that exist in one snapshot only."""

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


def _instances(snapshot: VillageSnapshot) -> dict[int, Counter[int | None]]:
    """For each item, how many instances stand at each level (None: the entry has no level)."""
    found: dict[int, Counter[int | None]] = {}
    for item in snapshot.items:
        found.setdefault(item.data_id, Counter())[item.level] += item.count
    return found


def _presence(data_id: int, levels: list[int | None]) -> list[Presence]:
    counted = Counter(levels)
    return [Presence(data_id, level, n) for level, n in sorted(counted.items(), key=_by_level)]


def _by_level(entry: tuple[int | None, int]) -> int:
    return -1 if entry[0] is None else entry[0]


def _compare_instances(
    data_id: int, old: Counter[int | None], new: Counter[int | None]
) -> tuple[list[Upgrade], list[Presence], list[Presence]]:
    """Cancel the instances that did not change, pair the rest from the highest level down.

    A paired instance that rose is an upgrade; what is left over is an addition or a removal.
    """
    kept = old & new
    gone = sorted((old - kept).elements(), key=lambda v: -1 if v is None else v, reverse=True)
    came = sorted((new - kept).elements(), key=lambda v: -1 if v is None else v, reverse=True)
    gone_levels = [v for v in gone if v is not None]
    came_levels = [v for v in came if v is not None]
    paired = min(len(gone_levels), len(came_levels))
    rose: Counter[tuple[int, int]] = Counter()
    not_rose_old: list[int] = []
    not_rose_new: list[int] = []
    for before, after in zip(gone_levels[:paired], came_levels[:paired], strict=True):
        if after > before:
            rose[(before, after)] += 1
        else:
            not_rose_old.append(before)
            not_rose_new.append(after)
    upgrades = [Upgrade(data_id, a, b, n) for (a, b), n in sorted(rose.items())]
    leftover_old: list[int | None] = [
        *gone_levels[paired:],
        *not_rose_old,
        *(v for v in gone if v is None),
    ]
    leftover_new: list[int | None] = [
        *came_levels[paired:],
        *not_rose_new,
        *(v for v in came if v is None),
    ]
    return upgrades, _presence(data_id, leftover_new), _presence(data_id, leftover_old)


def _group(jobs: tuple[RunningJob, ...]) -> dict[tuple[int, int], list[int]]:
    grouped: dict[tuple[int, int], list[int]] = {}
    for job in jobs:
        grouped.setdefault((job.data_id, job.to_level), []).append(job.finish_at)
    return {key: sorted(times) for key, times in grouped.items()}


def _unmatched(old: list[int], new: list[int]) -> tuple[list[int], list[int]]:
    """Finish times of each side left after pairing sightings of one job (closest first)."""
    free_new = list(new)
    left_old: list[int] = []
    for finish in old:
        near = [t for t in free_new if abs(t - finish) <= SAME_JOB_TOLERANCE_S]
        if near:
            free_new.remove(min(near, key=lambda t: abs(t - finish)))
        else:
            left_old.append(finish)
    return left_old, free_new


def _reaching(levels: Counter[int | None] | None, to_level: int) -> int:
    return sum(n for level, n in (levels or {}).items() if level is not None and level >= to_level)


def diff_snapshots(older: VillageSnapshot, newer: VillageSnapshot) -> SnapshotDiff:
    """What changed from `older` to `newer`, instance by instance.

    A job is identified by `(data_id, to_level)` and its finish time. `finished` counts the jobs of
    `older` that have no counterpart in `newer`, up to the number of instances that reached the
    job's level meanwhile: a job that vanished without any instance going up (cancelled) is not
    finished. `started` is a job of `newer` with no counterpart in `older` that is not simply an
    older job whose finish time moved.
    """
    before, after = _instances(older), _instances(newer)
    upgraded: list[Upgrade] = []
    added: list[Presence] = []
    removed: list[Presence] = []
    for data_id in sorted(before.keys() | after.keys()):
        up, add, rem = _compare_instances(
            data_id, before.get(data_id, Counter()), after.get(data_id, Counter())
        )
        upgraded += up
        added += add
        removed += rem

    old_jobs, new_jobs = _group(older.running), _group(newer.running)
    started: list[JobChange] = []
    finished: list[JobChange] = []
    for key in sorted(old_jobs.keys() | new_jobs.keys()):
        data_id, to_level = key
        gone, came = _unmatched(old_jobs.get(key, []), new_jobs.get(key, []))
        gain = max(
            0, _reaching(after.get(data_id), to_level) - _reaching(before.get(data_id), to_level)
        )
        done = min(len(gone), gain)
        # Jobs left without a counterpart or a level up were retimed or cancelled.
        retimed = len(gone) - done
        fresh = max(0, len(came) - retimed)
        finished += [JobChange(data_id, to_level, t) for t in gone[:done]]
        started += [JobChange(data_id, to_level, t) for t in came[len(came) - fresh :]]
    return SnapshotDiff(
        upgraded=tuple(upgraded),
        added=tuple(added),
        removed=tuple(removed),
        started=tuple(started),
        finished=tuple(finished),
    )
