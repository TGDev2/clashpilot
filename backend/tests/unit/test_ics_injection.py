"""Sonde S12 (H12) : un nom piégé ne crée aucune ligne ICS en plus.

Comportement sûr attendu : un nom contenant CRLF + BEGIN:VALARM reste échappé dans SUMMARY et
DESCRIPTION ; le nombre de BEGIN:VALARM et de BEGIN:VEVENT est celui d'une seule fin.
Adopted from the audit of 2026-11-12.
"""

from __future__ import annotations

from clashpilot.api.ics import calendar_of

NOW = 1_794_133_252


def test_hostile_name_does_not_inject_ics_lines() -> None:
    hostile = "Canon\r\nBEGIN:VALARM\r\nTRIGGER:-PT1M\r\nEND:VALARM\nBEGIN:VEVENT"
    safe = calendar_of(
        [{"task_id": "1:0:2", "name": "Canon", "lane": "builder", "start": NOW, "end": NOW + 3600}],
        NOW,
    )
    text = calendar_of(
        [{"task_id": "1:0:2", "name": hostile, "lane": "builder", "start": NOW, "end": NOW + 3600}],
        NOW,
    )
    lines = text.split("\r\n")
    for marker in ("BEGIN:VALARM", "BEGIN:VEVENT", "TRIGGER:-PT1M"):
        assert sum(1 for line in lines if line.startswith(marker)) == sum(
            1 for line in safe.split("\r\n") if line.startswith(marker)
        ), f"S12 injected line {marker}"
