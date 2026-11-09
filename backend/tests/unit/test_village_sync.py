"""Unit tests: the injectable transport of the official API client and the pure diff."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from clashpilot.gamedata.pack import load_pack
from clashpilot.ingest.diff import diff_snapshots
from clashpilot.ingest.official_api import CocApiError, fetch_player, match_levels
from clashpilot.ingest.state import RunningJob, SnapshotItem, VillageSnapshot

BASE = "https://coc.invalid/v1"
PACK_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "packs" / "fictional_pack_v1.json"


def test_fetch_player_goes_through_the_given_transport() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"name": "x"})

    player = fetch_player(BASE, "tok", "ABC1", transport=httpx.MockTransport(handler))

    assert player == {"name": "x"}
    request = seen[0]
    assert request.url.raw_path == b"/v1/players/%23ABC1"
    assert request.headers["authorization"] == "Bearer tok"


@pytest.mark.parametrize(
    ("status", "code"),
    [(404, "coc_player_not_found"), (429, "coc_rate_limited"), (500, "coc_unavailable")],
)
def test_fetch_player_translates_upstream_failures(status: int, code: str) -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(status, json={}))

    with pytest.raises(CocApiError) as caught:
        fetch_player(BASE, "tok", "ABC1", transport=transport)

    assert caught.value.code == code


def test_network_failure_is_unavailable_and_hides_the_token() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom tok-secret")

    with pytest.raises(CocApiError) as caught:
        fetch_player(BASE, "tok-secret", "ABC1", transport=httpx.MockTransport(handler))

    assert caught.value.code == "coc_unavailable"
    assert "tok-secret" not in str(caught.value)


def snapshot(*items: SnapshotItem, running: tuple[RunningJob, ...] = ()) -> VillageSnapshot:
    return VillageSnapshot(tag="T", exported_at=1, imported_at=1, items=items, running=running)


def item(data_id: int, level: int | None) -> SnapshotItem:
    return SnapshotItem(data_id=data_id, level=level, count=1, section="buildings", village="home")


def job(data_id: int, to_level: int) -> RunningJob:
    return RunningJob(
        data_id=data_id, finish_at=10, to_level=to_level, section="buildings", village="home"
    )


def test_diff_ignores_items_without_level_for_upgrades() -> None:
    result = diff_snapshots(snapshot(item(1, None)), snapshot(item(1, None)))
    assert result.upgraded == ()


def test_diff_finished_needs_the_level_to_be_reached() -> None:
    older = snapshot(item(1, 1), item(2, 1), running=(job(1, 2), job(2, 2)))
    newer = snapshot(item(1, 2), item(2, 1))

    result = diff_snapshots(older, newer)

    assert [j.data_id for j in result.finished] == [1]
    assert result.started == ()


def many(data_id: int, level: int, count: int = 1) -> SnapshotItem:
    return SnapshotItem(
        data_id=data_id, level=level, count=count, section="buildings", village="home"
    )


def timed(data_id: int, to_level: int, finish_at: int) -> RunningJob:
    return RunningJob(
        data_id=data_id,
        finish_at=finish_at,
        to_level=to_level,
        section="buildings",
        village="home",
    )


def test_diff_sees_an_upgrade_among_several_instances() -> None:
    older = snapshot(many(1, 10, 3), many(1, 11, 1))
    newer = snapshot(many(1, 10, 2), many(1, 11, 2))

    result = diff_snapshots(older, newer)

    assert [(u.data_id, u.from_level, u.to_level, u.count) for u in result.upgraded] == [
        (1, 10, 11, 1)
    ]
    assert result.added == ()
    assert result.removed == ()


def test_diff_reports_an_extra_instance_of_an_owned_item_as_added() -> None:
    result = diff_snapshots(snapshot(many(1, 5, 2)), snapshot(many(1, 5, 2), many(1, 1, 1)))

    assert [(p.data_id, p.level, p.count) for p in result.added] == [(1, 1, 1)]
    assert result.upgraded == ()


def test_diff_reports_a_missing_instance_as_removed() -> None:
    result = diff_snapshots(snapshot(many(1, 5, 3)), snapshot(many(1, 5, 2)))

    assert [(p.data_id, p.level, p.count) for p in result.removed] == [(1, 5, 1)]


def test_a_cancelled_job_is_not_finished_because_another_instance_has_the_level() -> None:
    older = snapshot(many(1, 10, 4), many(1, 11, 1), running=(timed(1, 11, 500),))
    newer = snapshot(many(1, 10, 4), many(1, 11, 1))

    result = diff_snapshots(older, newer)

    assert result.finished == ()
    assert result.started == ()


def test_one_instance_finishing_while_another_starts_the_same_level() -> None:
    older = snapshot(many(1, 10, 2), running=(timed(1, 11, 500),))
    newer = snapshot(many(1, 10, 1), many(1, 11, 1), running=(timed(1, 11, 90000),))

    result = diff_snapshots(older, newer)

    assert [(j.data_id, j.to_level) for j in result.finished] == [(1, 11)]
    assert [(j.data_id, j.to_level) for j in result.started] == [(1, 11)]


def test_two_simultaneous_jobs_to_the_same_level_are_both_tracked() -> None:
    jobs = (timed(1, 11, 5000), timed(1, 11, 9000))
    older = snapshot(many(1, 10, 2), running=jobs)
    newer = snapshot(many(1, 11, 1), many(1, 10, 1), running=(timed(1, 11, 9000),))

    result = diff_snapshots(older, newer)

    assert [j.finish_at for j in result.finished] == [5000]
    assert result.started == ()


def test_the_same_job_seen_twice_is_neither_started_nor_finished() -> None:
    older = snapshot(many(1, 10, 1), running=(timed(1, 11, 5000),))
    newer = snapshot(many(1, 10, 1), running=(timed(1, 11, 5002),))

    result = diff_snapshots(older, newer)

    assert result.started == ()
    assert result.finished == ()


def test_api_names_are_matched_only_among_items_of_the_section_kind() -> None:
    raw = json.loads(PACK_FIXTURE.read_text(encoding="utf-8"))
    shared = {"village": "home", "tags": [], "max_count_by_th": {"1": 1}, "category": "x"}
    raw["items"] = [
        {**shared, "data_id": 91, "name": "Homonyme", "kind": "building", "lane": "builder"},
        {**shared, "data_id": 92, "name": "Homonyme", "kind": "troop", "lane": "lab"},
        {**shared, "data_id": 93, "name": "Homonyme", "kind": "troop", "lane": "lab"}
        | {"village": "builder"},
    ]
    raw["levels"], raw["storage"] = [], []
    pack = load_pack(raw)
    export = snapshot(many(91, 3), many(92, 1))
    player = {"troops": [{"name": "Homonyme", "level": 4, "village": "home"}]}

    updated, conflicts = match_levels(player, export, pack)

    assert [(u.data_id, u.from_level, u.level) for u in updated] == [(92, 1, 4)]
    assert conflicts == []
