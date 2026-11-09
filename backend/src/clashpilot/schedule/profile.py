"""The player's schedule profile: weekly local windows, absences and login policy (SPEC ADR-10).

Bounds stay strings in the profile, so a stored profile reads back exactly as it was written. A
rejected value carries the error type `invalid_timezone` or `invalid_window`, which the API answers
as its error code.
"""

from __future__ import annotations

from datetime import datetime
from functools import cache
from typing import Annotated, Literal, Self
from zoneinfo import available_timezones

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

CLOCK_FORMAT = "%H:%M"
LOCAL_FORMAT = "%Y-%m-%dT%H:%M:%S"
# The conversion to UTC overflows near the limits of `datetime`.
LOCAL_YEARS = range(1970, 2101)

_known_timezones = cache(available_timezones)


def _matches(value: str, fmt: str) -> bool:
    """True if `value` is exactly `fmt`, zero padding included."""
    try:
        return datetime.strptime(value, fmt).strftime(fmt) == value
    except ValueError:
        return False


def _check_timezone(value: str) -> str:
    # A set lookup, because `ZoneInfo(key)` raises a different error for each kind of bad key.
    if value not in _known_timezones():
        raise PydanticCustomError("invalid_timezone", "Fuseau horaire inconnu.")
    return value


def _check_clock(value: str) -> str:
    if not _matches(value, CLOCK_FORMAT):
        raise PydanticCustomError("invalid_window", "L'heure doit être au format HH:MM.")
    return value


def _check_local(value: str) -> str:
    if not _matches(value, LOCAL_FORMAT) or datetime.fromisoformat(value).year not in LOCAL_YEARS:
        raise PydanticCustomError(
            "invalid_window", "La date doit être au format AAAA-MM-JJTHH:MM:SS."
        )
    return value


Timezone = Annotated[str, AfterValidator(_check_timezone)]
ClockTime = Annotated[str, AfterValidator(_check_clock)]
LocalDateTime = Annotated[str, AfterValidator(_check_local)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class WeeklyWindow(_Strict):
    """A recurring local window; `end <= start` means that it ends on the next day."""

    weekday: Annotated[int, Field(ge=0, le=6)] | None  # 0 = Monday, None = every day
    start: ClockTime
    end: ClockTime
    kind: Literal["sleep", "work", "available", "preferred"]

    @model_validator(mode="after")
    def _has_a_duration(self) -> Self:
        if self.start == self.end:
            raise PydanticCustomError(
                "invalid_window", "Une fenêtre ne peut pas être de durée nulle."
            )
        return self


class AbsenceException(_Strict):
    """A one-off absence between two local date-times."""

    start_local: LocalDateTime
    end_local: LocalDateTime
    kind: Literal["absence"]

    @model_validator(mode="after")
    def _ends_after_its_start(self) -> Self:
        if datetime.fromisoformat(self.end_local) <= datetime.fromisoformat(self.start_local):
            raise PydanticCustomError("invalid_window", "Une absence doit finir après son début.")
        return self


class LoginPolicy(_Strict):
    mode: Literal["strict", "tolerance", "progress", "min_logins"]
    tolerance_minutes: Annotated[int, Field(ge=0)]
    max_logins_per_day: Annotated[int, Field(ge=1)] | None


class ScheduleProfile(_Strict):
    tz: Timezone  # IANA key
    weekly: list[WeeklyWindow]
    exceptions: list[AbsenceException]
    policy: LoginPolicy
