"""Progression strategies (SPEC section 4, Objective; configurable value hypothesis).

A strategy is data read from `strategies.json`: objective weights, value multipliers by category
and by tag, a multiplier for the items of the army composition, and named constraints. It never
ranks tasks: the order still comes out of the engine and the simulation. `apply_strategy` only
changes task values, task prerequisites, the goal and the hero blackout windows of a problem,
never a duration or a cost. Each constraint is generic code, chosen by name in the data:

- `town_hall_last`: a town hall upgrade requires every task that does not depend on a town hall;
- `war_windows`: the war windows become `Problem.hero_blackouts`;
- `goal`: the goal given becomes `Problem.goal`.

Pure module apart from `load_strategies`, which reads the file it is given (or the packaged one).
"""

from __future__ import annotations

import dataclasses
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from clashpilot.planning.model import Goal, Problem, Task
from clashpilot.planning.objective import Objective

STRATEGIES_PATH = Path(__file__).with_name("strategies.json")
TOWN_HALL_TAG = "town_hall"

TOWN_HALL_LAST = "town_hall_last"
WAR_WINDOWS = "war_windows"
GOAL = "goal"
CONSTRAINTS = frozenset({TOWN_HALL_LAST, WAR_WINDOWS, GOAL})

_WEIGHTS = frozenset(field.name for field in dataclasses.fields(Objective))


class StrategyFileError(ValueError):
    """The strategies file does not follow its schema."""


@dataclass(frozen=True)
class Strategy:
    key: str
    label: str  # French
    weights: Mapping[str, float]  # names of `Objective` fields
    category_multipliers: Mapping[str, float]
    tag_multipliers: Mapping[str, float]
    composition_multiplier: float = 1.0
    constraints: frozenset[str] = frozenset()

    def objective(self) -> Objective:
        return Objective(**self.weights)


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise StrategyFileError(f"{where}: a finite number is expected")
    return float(value)


def _multipliers(value: Any, where: str) -> dict[str, float]:
    if not isinstance(value, dict):
        raise StrategyFileError(f"{where}: an object is expected")
    multipliers = {str(name): _number(factor, f"{where}.{name}") for name, factor in value.items()}
    if any(factor < 0 for factor in multipliers.values()):
        raise StrategyFileError(f"{where}: a multiplier is never negative")
    return multipliers


def _strategy(key: str, entry: Any) -> Strategy:
    where = f"strategies.{key}"
    if not isinstance(entry, dict):
        raise StrategyFileError(f"{where}: an object is expected")
    label = entry.get("label")
    if not isinstance(label, str) or not label.strip():
        raise StrategyFileError(f"{where}.label: a non-empty text is expected")
    weights = entry.get("weights")
    if not isinstance(weights, dict):
        raise StrategyFileError(f"{where}.weights: an object is expected")
    unknown = sorted(set(weights) - _WEIGHTS)
    if unknown:
        raise StrategyFileError(f"{where}.weights: unknown weights {unknown}")
    constraints = entry.get("constraints", [])
    if not isinstance(constraints, list) or not all(isinstance(c, str) for c in constraints):
        raise StrategyFileError(f"{where}.constraints: a list of names is expected")
    if set(constraints) - CONSTRAINTS:
        raise StrategyFileError(
            f"{where}.constraints: unknown {sorted(set(constraints) - CONSTRAINTS)}"
        )
    return Strategy(
        key=key,
        label=label,
        weights={
            name: _number(value, f"{where}.weights.{name}") for name, value in weights.items()
        },
        category_multipliers=_multipliers(
            entry.get("category_multipliers", {}), f"{where}.category_multipliers"
        ),
        tag_multipliers=_multipliers(entry.get("tag_multipliers", {}), f"{where}.tag_multipliers"),
        composition_multiplier=_number(
            entry.get("composition_multiplier", 1.0), f"{where}.composition_multiplier"
        ),
        constraints=frozenset(constraints),
    )


def parse_strategies(content: Any) -> dict[str, Strategy]:
    """The strategies of a decoded strategies file, in file order."""
    if not isinstance(content, dict) or not isinstance(content.get("strategies"), dict):
        raise StrategyFileError("strategies: an object is expected")
    return {str(key): _strategy(str(key), entry) for key, entry in content["strategies"].items()}


@lru_cache(maxsize=1)
def _packaged() -> dict[str, Strategy]:
    return parse_strategies(json.loads(STRATEGIES_PATH.read_text(encoding="utf-8")))


def load_strategies(path: Path | None = None) -> dict[str, Strategy]:
    """The strategies of `path`, or of the packaged `strategies.json` without it."""
    if path is None:
        return dict(_packaged())
    return parse_strategies(json.loads(Path(path).read_text(encoding="utf-8")))


def goal_of(problem: Problem, data_id: int, level: int) -> Goal:
    """The goal "`data_id` at `level`": every task of that item up to that level."""
    return Goal(
        task_ids=frozenset(
            task.id for task in problem.tasks if task.data_id == data_id and task.to_level <= level
        )
    )


def _value(task: Task, strategy: Strategy, composition: frozenset[int]) -> float:
    factor = strategy.category_multipliers.get(task.category, 1.0)
    for tag in dict.fromkeys(task.tags):
        factor *= strategy.tag_multipliers.get(tag, 1.0)
    if task.data_id in composition:
        factor *= strategy.composition_multiplier
    return task.value * factor


def _town_hall_last(tasks: Sequence[Task]) -> tuple[Task, ...]:
    """Each town hall upgrade requires every task that does not depend on a town hall."""
    town_halls = {task.id for task in tasks if TOWN_HALL_TAG in task.tags}
    if not town_halls:
        return tuple(tasks)
    after = set(town_halls)
    grown = True
    while grown:
        grown = False
        for task in tasks:
            if task.id not in after and after.intersection(task.requires):
                after.add(task.id)
                grown = True
    current = tuple(task.id for task in tasks if task.id not in after)
    return tuple(
        dataclasses.replace(task, requires=tuple(dict.fromkeys((*task.requires, *current))))
        if task.id in town_halls
        else task
        for task in tasks
    )


def apply_strategy(
    problem: Problem,
    strategy: Strategy,
    composition: Iterable[int] = (),
    *,
    goal: Goal | None = None,
    war_windows: Sequence[tuple[int, int]] = (),
) -> tuple[Problem, Objective]:
    """The problem as `strategy` sees it, and the objective of its weights."""
    chosen = frozenset(composition)
    tasks: tuple[Task, ...] = tuple(
        dataclasses.replace(task, value=_value(task, strategy, chosen)) for task in problem.tasks
    )
    if TOWN_HALL_LAST in strategy.constraints:
        tasks = _town_hall_last(tasks)
    blackouts = problem.hero_blackouts
    if WAR_WINDOWS in strategy.constraints:
        blackouts = tuple(sorted((int(start), int(end)) for start, end in war_windows))
    applied = dataclasses.replace(
        problem,
        tasks=tasks,
        goal=goal if GOAL in strategy.constraints else None,
        hero_blackouts=blackouts,
    )
    return applied, strategy.objective()
