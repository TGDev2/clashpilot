"""Sonde S11 (H11) : au changement d'heure, la nuit 23 h - 7 h reste 23 h - 7 h, heure locale.

Comportement sûr attendu : pour Europe/Paris, la nuit du 24 au 25 octobre 2026 (passage à l'heure
d'hiver) et celle du 28 au 29 mars 2026 (heure d'été) couvrent exactement 23:00 → 07:00 locales.
Adopted from the audit of 2026-11-12.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from clashpilot.schedule.expand import local_to_epoch


@pytest.mark.parametrize(
    ("evening", "morning", "hours"),
    [((2026, 10, 24), (2026, 10, 25), 9), ((2026, 3, 28), (2026, 3, 29), 7)],
)
def test_sleep_window_follows_local_clock_across_dst(
    evening: tuple[int, int, int], morning: tuple[int, int, int], hours: int
) -> None:
    from clashpilot.schedule.expand import expand_windows
    from clashpilot.schedule.profile import ScheduleProfile

    start = local_to_epoch(datetime(*evening, 23, 0), "Europe/Paris")
    end = local_to_epoch(datetime(*morning, 7, 0), "Europe/Paris")
    assert end - start == hours * 3600
    profile = ScheduleProfile.model_validate(
        {
            "tz": "Europe/Paris",
            "weekly": [{"weekday": None, "start": "23:00", "end": "07:00", "kind": "sleep"}],
            "exceptions": [],
            "policy": {"mode": "strict", "tolerance_minutes": 0, "max_logins_per_day": None},
        }
    )
    calendar = expand_windows(profile, start - 86_400, end + 86_400)
    assert (start, end) in [tuple(s) for s in calendar.sleep], "S11 sleep window shifted"
    assert calendar.is_sleep(start) and calendar.is_sleep(end - 1)
    assert not calendar.is_sleep(start - 1) and not calendar.is_sleep(end)
