"""Unit tests: the injectable transport of the official API client and the pure diff."""

from __future__ import annotations

import httpx
import pytest

from clashpilot.ingest.diff import diff_snapshots
from clashpilot.ingest.official_api import CocApiError, fetch_player
from clashpilot.ingest.state import RunningJob, SnapshotItem, VillageSnapshot

BASE = "https://coc.invalid/v1"


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
