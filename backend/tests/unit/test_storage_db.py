from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clashpilot.api.errors import ApiError, register_error_handlers
from clashpilot.storage.db import connect, migrate, open_db


def write(directory: Path, name: str, sql: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(sql, encoding="utf-8")


def tables(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {row[0] for row in rows}


def test_connect_enables_wal_and_foreign_keys(tmp_path: Path) -> None:
    connection = connect(tmp_path / "nested" / "data")
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        connection.close()


def test_migrate_applies_each_file_once(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    write(migrations, "0001_a.sql", "CREATE TABLE a (id INTEGER);")
    connection = connect(tmp_path / "data")
    try:
        assert migrate(connection, migrations) == ["0001_a.sql"]
        assert migrate(connection, migrations) == []
        assert "a" in tables(connection)
    finally:
        connection.close()


def test_migrate_applies_a_lower_number_delivered_after_a_higher_one(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    write(migrations, "0005_late.sql", "CREATE TABLE late (id INTEGER);")
    connection = connect(tmp_path / "data")
    try:
        migrate(connection, migrations)
        write(migrations, "0003_middle.sql", "CREATE TABLE middle (id INTEGER);")
        assert migrate(connection, migrations) == ["0003_middle.sql"]
        assert {"late", "middle"} <= tables(connection)
    finally:
        connection.close()


def test_failed_migration_is_rolled_back_and_not_recorded(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    write(migrations, "0001_bad.sql", "CREATE TABLE half (id INTEGER);\nNOT VALID SQL;")
    connection = connect(tmp_path / "data")
    try:
        with pytest.raises(sqlite3.Error):
            migrate(connection, migrations)
        assert "half" not in tables(connection)
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 0
    finally:
        connection.close()


def test_open_db_rolls_back_on_error_and_commits_on_success(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    write(migrations, "0001_t.sql", "CREATE TABLE t (id INTEGER);")
    with open_db(tmp_path / "data", migrations) as connection:
        connection.execute("INSERT INTO t VALUES (1)")
    with pytest.raises(RuntimeError), open_db(tmp_path / "data", migrations) as connection:
        connection.execute("INSERT INTO t VALUES (2)")
        raise RuntimeError("boom")
    with open_db(tmp_path / "data", migrations) as connection:
        assert [row[0] for row in connection.execute("SELECT id FROM t")] == [1]


def test_api_error_is_answered_in_the_contract_format() -> None:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/boom")
    def boom() -> None:
        raise ApiError(418, "teapot", "Théière.", ["a", "b"])

    @app.get("/plain")
    def plain() -> None:
        raise ApiError(409, "plain", "Simple.")

    client = TestClient(app)
    response = client.get("/boom")
    assert response.status_code == 418
    assert response.json() == {
        "detail": {"code": "teapot", "message": "Théière.", "errors": ["a", "b"]}
    }
    assert client.get("/plain").json() == {"detail": {"code": "plain", "message": "Simple."}}


def test_malformed_request_body_follows_the_error_contract(tmp_path: Path) -> None:
    from clashpilot.api.app import create_app
    from clashpilot.settings import Settings

    client = TestClient(create_app(Settings(data_dir=tmp_path, frontend_dist=tmp_path / "none")))
    response = client.post("/api/gamedata/packs", json=[1, 2])
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_request"
    assert detail["errors"]
