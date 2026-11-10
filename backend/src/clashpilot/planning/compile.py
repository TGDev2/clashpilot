"""Compile a village into a planning problem (SPEC section 4, Compilation, rules 1 to 9).

Pure: no I/O, no clock (`now` is a parameter). Only the home village is planned. An item whose lane
is `none` gives no task, and a level without a pack row is never estimated (ADR-2): it is listed in
`Problem.excluded` with the reason `missing_pack_row`.

Two readings the SPEC leaves open:

- The town hall requirement of a level is transitive: a troop level that needs a laboratory level
  needs the town hall level of that laboratory level. So the levels beyond the target town hall are
  neither created nor listed, whether the town hall is required directly or through a building.
- A running job counts for its `to_level` in the current level of an instance (the default target
  town hall and the gap used by `max_tasks`), but a `requires` is only met by a finished level; a
  level still running is required through the id of its frozen job.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from clashpilot.gamedata.pack import INSTANT_UPGRADE_TAG, GamePack, Item, LevelRow
from clashpilot.ingest.state import ManualState, RunningJob, VillageSnapshot
from clashpilot.planning.model import (
    ExcludedTask,
    FrozenJob,
    Modifier,
    Policy,
    Problem,
    ResourceModel,
    Task,
    task_id,
)
from clashpilot.schedule.expand import expand_windows
from clashpilot.schedule.profile import ScheduleProfile

DAY_S = 86_400
HOUR_S = 3_600
HOME = "home"
TOWN_HALL_TAG = "town_hall"
BUILDER_HUT_TAG = "builder_hut"
# Lanes with one worker when the building that owns them stands in the village, with its tag.
SINGLE_LANES = {"lab": "laboratory", "pet_house": "pet_house", "blacksmith": "blacksmith"}

MISSING_PACK_ROW = "missing_pack_row"
EXCEEDS_STORAGE = "exceeds_storage"
WALL_SINK = "wall_sink"
INSTANT_UPGRADE = "instant_upgrade"
MISSING_PREREQUISITE = "missing_prerequisite"
HORIZON_LIMIT = "horizon_limit"


class MissingInputError(ValueError):
    """An input the problem cannot do without; `missing` names each one."""

    def __init__(self, missing: Iterable[str]) -> None:
        self.missing: tuple[str, ...] = tuple(missing)
        super().__init__("missing input: " + ", ".join(self.missing))


@dataclass(frozen=True)
class _Instance:
    data_id: int
    index: int
    level: int  # last finished level
    job: RunningJob | None = None  # running after now, towards job.to_level
    town_hall: int = 0  # town hall level needed to build it (rule 3), 0 if owned

    @property
    def base(self) -> int:
        """The level the instance has once its frozen job ends."""
        return self.job.to_level if self.job is not None else self.level


@dataclass
class _Candidate:
    task: Task
    gap: int
    reason: str | None


# --------------------------------------------------------------------------- instances (rule 2)


def _owned_instances(
    snapshot: VillageSnapshot, pack: GamePack, now: int
) -> dict[int, list[_Instance]]:
    """Instances of each home item, ranked by level reached, running first, earliest end first."""
    levels: dict[int, list[int]] = defaultdict(list)
    for entry in snapshot.items:
        known = pack.item(entry.data_id)
        if entry.village != HOME or entry.level is None or known is None or known.village != HOME:
            continue
        levels[entry.data_id] += [entry.level] * entry.count
    jobs: dict[int, list[RunningJob]] = defaultdict(list)
    for job in snapshot.running:
        if job.village == HOME and job.data_id in levels:
            jobs[job.data_id].append(job)

    owned: dict[int, list[_Instance]] = {}
    for data_id in sorted(levels):
        free = sorted(levels[data_id])
        states: list[tuple[int, RunningJob | None]] = []
        for job in sorted(jobs[data_id], key=lambda j: (j.finish_at, j.to_level)):
            if job.to_level - 1 not in free:
                continue  # a job that matches no instance of the export
            free.remove(job.to_level - 1)
            # A job finished at `now` has left its instance at `to_level`, at no cost.
            states.append((job.to_level, None) if job.finish_at <= now else (job.to_level - 1, job))
        states += [(level, None) for level in free]
        states.sort(
            key=lambda s: (
                -(s[1].to_level if s[1] is not None else s[0]),
                0 if s[1] is not None else 1,
                s[1].finish_at if s[1] is not None else 0,
            )
        )
        owned[data_id] = [
            _Instance(data_id, index, level, job) for index, (level, job) in enumerate(states)
        ]
    return owned


def _count_at(item: Item, town_hall: int | None) -> int:
    """`max_count_by_th` at `town_hall`: the value of the largest key not above it."""
    keys = sorted(int(key) for key in item.max_count_by_th)
    usable = [key for key in keys if town_hall is None or key <= town_hall]
    return item.max_count_by_th[str(usable[-1])] if usable else 0


def _unlocked_instances(
    pack: GamePack, owned: Mapping[int, list[_Instance]], target: int | None
) -> dict[int, list[_Instance]]:
    """Rule 3: the instances the target town hall allows beyond those owned, from level 0."""
    added: dict[int, list[_Instance]] = {}
    for item in pack.data.items:
        if item.village != HOME or not item.max_count_by_th:
            continue
        have = len(owned.get(item.data_id, []))
        allowed = _count_at(item, target)
        keys = sorted(int(key) for key in item.max_count_by_th)
        fresh = []
        for index in range(have, allowed):
            needed = next(key for key in keys if _count_at(item, key) >= index + 1)
            fresh.append(_Instance(item.data_id, index, 0, None, needed))
        if fresh:
            added[item.data_id] = fresh
    return added


# --------------------------------------------------------------------------- town hall (rule 1)


class _TownHallNeeds:
    """Town hall level a pack level needs, through its requirements and its previous level."""

    def __init__(self, pack: GamePack, town_hall_id: int | None) -> None:
        self._pack = pack
        self._town_hall_id = town_hall_id
        self._known: dict[tuple[int, int], int] = {}
        self._visiting: set[tuple[int, int]] = set()

    def __call__(self, data_id: int, level: int) -> int:
        if self._town_hall_id is None or level < 1:
            return 0
        if data_id == self._town_hall_id:
            return level
        key = (data_id, level)
        if key in self._known:
            return self._known[key]
        if key in self._visiting:
            return 0  # a cycle in the pack requirements
        self._visiting.add(key)
        needed = self(data_id, level - 1)
        row = self._pack.level(data_id, level)
        for requirement in row.requires if row is not None else ():
            needed = max(needed, self(requirement.data_id, requirement.level))
        self._visiting.discard(key)
        self._known[key] = needed
        return needed


def _top_level(pack: GamePack, data_id: int, needs: _TownHallNeeds, target: int | None) -> int:
    """Rule 1: the highest pack level whose town hall requirement is within the target."""
    return max(
        (
            row.level
            for row in pack.levels_of(data_id)
            if target is None or needs(data_id, row.level) <= target
        ),
        default=0,
    )


# --------------------------------------------------------------------------- lanes and storage


def _lane_owners(pack: GamePack, town_hall_id: int | None) -> dict[str, set[int]]:
    """Items owning each single lane: tagged as such, or required by the levels of that lane."""
    owners: dict[str, set[int]] = {lane: set() for lane in SINGLE_LANES}
    lane_of: dict[int, str] = {item.data_id: item.lane for item in pack.data.items}
    for item in pack.data.items:
        for lane, tag in SINGLE_LANES.items():
            if tag in item.tags:
                owners[lane].add(item.data_id)
    for row in pack.data.levels:
        owner_lane = lane_of.get(row.data_id)
        if owner_lane is None or owner_lane not in owners:
            continue
        for requirement in row.requires:
            if (
                requirement.data_id != town_hall_id
                and lane_of.get(requirement.data_id) != owner_lane
            ):
                owners[owner_lane].add(requirement.data_id)
    return owners


def _caps(
    pack: GamePack, owned: Mapping[int, list[_Instance]], manual: ManualState
) -> dict[str, int]:
    """Storage of the finished levels of the village, unless `storage_override` gives it."""
    capacity: dict[tuple[int, int], list[tuple[str, int]]] = defaultdict(list)
    for entry in pack.data.storage:
        capacity[(entry.data_id, entry.level)].append((entry.resource, entry.capacity))
    caps: dict[str, int] = defaultdict(int)
    for data_id, instances in owned.items():
        for instance in instances:
            for resource, amount in capacity.get((data_id, instance.level), []):
                caps[resource] += amount
    for resource, amount in manual.storage_override.items():
        caps[resource] = amount
    return dict(caps)


# --------------------------------------------------------------------------- candidates (rules 4-5)


def _reason(item: Item, row: LevelRow | None, caps: Mapping[str, int]) -> str | None:
    if row is None:
        return MISSING_PACK_ROW
    if any(resource in caps and amount > caps[resource] for resource, amount in row.cost.items()):
        return EXCEEDS_STORAGE
    if item.kind == "wall":
        return WALL_SINK
    if INSTANT_UPGRADE_TAG in item.tags:
        return INSTANT_UPGRADE
    return None


def _candidates(
    pack: GamePack,
    instances: Mapping[int, list[_Instance]],
    needs: _TownHallNeeds,
    target: int | None,
    town_hall_id: int | None,
    caps: Mapping[str, int],
) -> dict[str, _Candidate]:
    def finished(data_id: int, level: int) -> bool:
        first = instances.get(data_id)
        return first is not None and len(first) > 0 and first[0].level >= level

    candidates: dict[str, _Candidate] = {}
    for data_id in sorted(instances):
        item = pack.item(data_id)
        if item is None or item.lane == "none":
            continue
        top = _top_level(pack, data_id, needs, target)
        tags = tuple(dict.fromkeys([*item.tags, item.kind]))
        for instance in instances[data_id]:
            for level in range(instance.base + 1, top + 1):
                row = pack.level(data_id, level)
                requires: set[str] = set()
                if level - 1 > instance.level:
                    requires.add(task_id(data_id, instance.index, level - 1))
                needed = [(r.data_id, r.level) for r in row.requires] if row is not None else []
                if level == 1 and instance.town_hall and town_hall_id is not None:
                    needed.append((town_hall_id, instance.town_hall))
                requires |= {
                    task_id(other, 0, at_least)
                    for other, at_least in needed
                    if not finished(other, at_least)
                }
                duration = row.duration_s if row is not None else 0
                task = Task(
                    id=task_id(data_id, instance.index, level),
                    data_id=data_id,
                    instance=instance.index,
                    to_level=level,
                    lane=item.lane,
                    duration_s=duration,
                    cost=dict(row.cost) if row is not None else {},
                    requires=tuple(sorted(requires)),
                    value=duration / HOUR_S,
                    category=item.category,
                    tags=tags,
                )
                candidates[task.id] = _Candidate(
                    task, level - instance.base, _reason(item, row, caps)
                )
    return candidates


def _cascade(candidates: Mapping[str, _Candidate], frozen_ids: set[str]) -> dict[str, str]:
    """Reason of each excluded candidate, a dependent taking the reason of its cause."""
    resolved: dict[str, str | None] = {}

    def reason_of(name: str, visiting: set[str]) -> str | None:
        if name in resolved:
            return resolved[name]
        candidate = candidates[name]
        reason = candidate.reason
        if reason is None:
            visiting.add(name)
            for required in candidate.task.requires:
                if required in frozen_ids:
                    continue
                if required not in candidates or required in visiting:
                    reason = MISSING_PREREQUISITE
                else:
                    reason = reason_of(required, visiting)
                if reason is not None:
                    break
            visiting.discard(name)
        resolved[name] = reason
        return reason

    out: dict[str, str] = {}
    for name in sorted(candidates):
        reason = reason_of(name, set())
        if reason is not None:
            out[name] = reason
    return out


def _select(
    candidates: dict[str, _Candidate], frozen_ids: set[str], max_tasks: int
) -> tuple[list[Task], list[ExcludedTask]]:
    """Rule 5: exclusions, then the task cap, each cascading to the dependents."""
    reasons = _cascade(candidates, frozen_ids)
    kept = sorted(
        (c for name, c in candidates.items() if name not in reasons),
        key=lambda c: (c.gap, c.task.id),
    )
    if len(kept) > max_tasks:
        for candidate in kept[max_tasks:]:
            candidate.reason = HORIZON_LIMIT
        reasons = _cascade(candidates, frozen_ids)

    def order(task: Task) -> tuple[int, int, int]:
        return (task.data_id, task.instance, task.to_level)

    tasks = sorted((c.task for name, c in candidates.items() if name not in reasons), key=order)
    excluded = [
        ExcludedTask(
            data_id=candidates[name].task.data_id,
            to_level=candidates[name].task.to_level,
            reason=reason,
            instance=candidates[name].task.instance,
        )
        for name, reason in sorted(reasons.items(), key=lambda kv: order(candidates[kv[0]].task))
    ]
    return tasks, excluded


# --------------------------------------------------------------------------- frozen jobs (rule 6)


def _frozen_jobs(
    pack: GamePack,
    owned: Mapping[int, list[_Instance]],
    snapshot: VillageSnapshot,
    lanes: Mapping[str, int],
    now: int,
) -> list[FrozenJob]:
    drafts: list[tuple[str, str, int, int, bool]] = []
    for data_id, instances in owned.items():
        item = pack.item(data_id)
        if item is None or item.lane == "none":
            continue
        for instance in instances:
            job = instance.job
            if job is None:
                continue
            row = pack.level(data_id, job.to_level)
            begun = job.finish_at - row.duration_s if row is not None else snapshot.exported_at
            drafts.append(
                (
                    task_id(data_id, instance.index, job.to_level),
                    item.lane,
                    min(now, begun),
                    job.finish_at,
                    job.extra,
                )
            )

    frozen: list[FrozenJob] = []
    for lane in sorted({draft[1] for draft in drafts}):
        mine = sorted((d for d in drafts if d[1] == lane), key=lambda d: (d[3], d[0]))
        regular = [d for d in mine if not d[4]]
        extra = [d for d in mine if d[4]]
        # Every regular job runs at `now`, so each takes its own slot; extra ones come after.
        first_extra_slot = max(lanes.get(lane, 0), len(regular))
        slots = [*range(len(regular)), *range(first_extra_slot, first_extra_slot + len(extra))]
        for (name, _, start, end, is_extra), slot in zip([*regular, *extra], slots, strict=True):
            frozen.append(FrozenJob(name, lane, slot, start, end, is_extra))
    return sorted(frozen, key=lambda job: (job.lane, job.lane_slot))


# --------------------------------------------------------------------------- resources (rule 7)


def _resources(
    manual: ManualState, caps: Mapping[str, int], consumed: set[str], now: int
) -> ResourceModel:
    income = {
        name: manual.farm_rate[name].expected if name in manual.farm_rate else 0
        for name in sorted(consumed | set(manual.resources) | set(manual.farm_rate))
    }
    initial: dict[str, int] = {}
    for name, typed in manual.resources.items():
        elapsed = max(0, now - typed.as_of)
        amount = (typed.value * HOUR_S + income[name] * elapsed) // HOUR_S
        initial[name] = min(amount, caps[name]) if name in caps else amount
    return ResourceModel(initial=initial, cap=dict(caps), income_per_hour=income)


# --------------------------------------------------------------------------- entry point


def compile_problem(
    snapshot: VillageSnapshot,
    manual: ManualState,
    pack: GamePack,
    schedule_profile: ScheduleProfile | None,
    now: int,
    horizon_days: int,
    target_th: int | None = None,
    max_tasks: int = 400,
) -> Problem:
    """The problem of planning the village of `snapshot` from `now` for `horizon_days` days."""
    owned = _owned_instances(snapshot, pack, now)
    town_hall = next(
        (i for i in pack.data.items if TOWN_HALL_TAG in i.tags and i.village == HOME), None
    )
    town_hall_id = town_hall.data_id if town_hall is not None else None

    # Rule 1: the target town hall.
    target: int | None = None
    if town_hall_id is not None:
        current = owned[town_hall_id][0].base if owned.get(town_hall_id) else 0
        if target_th is not None:
            target = target_th
        elif pack.level(town_hall_id, current + 1) is not None:
            target = current + 1
        else:
            target = current

    # Rules 2 to 5: instances, unlocks, prerequisites and exclusions.
    instances = {data_id: list(found) for data_id, found in owned.items()}
    for data_id, fresh in _unlocked_instances(pack, owned, target).items():
        instances.setdefault(data_id, []).extend(fresh)
    caps = _caps(pack, owned, manual)
    needs = _TownHallNeeds(pack, town_hall_id)
    candidates = _candidates(pack, instances, needs, target, town_hall_id, caps)

    # Rule 8 (builders) is needed by rule 6 for the slots of the frozen jobs.
    huts = sum(
        len(found)
        for data_id, found in owned.items()
        if (item := pack.item(data_id)) is not None and BUILDER_HUT_TAG in item.tags
    )
    builders = manual.builders_total if manual.builders_total is not None else huts or None
    lanes: dict[str, int] = {"builder": builders or 0}
    for lane, owners in _lane_owners(pack, town_hall_id).items():
        if any(data_id in owned for data_id in owners):
            lanes[lane] = 1

    # Rule 6: running jobs.
    frozen = _frozen_jobs(pack, owned, snapshot, lanes, now)
    tasks, excluded = _select(candidates, {job.task_id for job in frozen}, max_tasks)

    # Rules 7 and 8: resources and mandatory inputs.
    consumed = {name for task in tasks for name, amount in task.cost.items() if amount > 0}
    missing = sorted(name for name in consumed if name not in manual.resources)
    if schedule_profile is None:
        missing.append("schedule_profile")
    if builders is None:
        missing.append("builders_total")
    if missing or schedule_profile is None:
        raise MissingInputError(missing)

    horizon_end = now + horizon_days * DAY_S
    rules = schedule_profile.policy
    return Problem(
        now=now,
        horizon_end=horizon_end,
        lanes=lanes,
        tasks=tuple(tasks),
        frozen=tuple(frozen),
        resources=_resources(manual, caps, consumed, now),
        calendar=expand_windows(schedule_profile, now, horizon_end),
        policy=Policy(
            mode=rules.mode,
            tolerance_s=rules.tolerance_minutes * 60,
            max_logins_per_day=rules.max_logins_per_day,
        ),
        # No pack rule describes the gold pass yet, so `manual.gold_pass` adds no modifier.
        modifiers=tuple(
            Modifier(e.start, e.end, e.lane, e.duration_pct, e.cost_pct) for e in manual.events
        ),
        excluded=tuple(excluded),
    )
