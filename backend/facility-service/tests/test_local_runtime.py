"""Ensure local setup is idempotent and refuses to adopt unrelated directories."""

import json

import pytest

from scripts import local


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    """Redirect native runtime operations to an owned temporary test directory."""
    monkeypatch.setattr(local, "RUNTIME", tmp_path / "runtime")
    monkeypatch.setattr(local, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(local.subprocess, "check_output", lambda *args, **kwargs: "test-user")
    return local.RUNTIME


def test_local_credentials_create_once_without_reset(runtime):
    """Repeated setup must preserve database credentials and the signing secret."""
    first = local.credentials(create=True)
    second = local.credentials(create=True)
    assert first == second
    assert len(first["password"]) >= 32
    assert len(first["jwt_secret"]) >= 48
    assert first["password"] != first["jwt_secret"]


def test_local_runtime_refuses_unowned_nonempty_directory(runtime):
    """An existing directory without an owner marker is never taken over."""
    runtime.mkdir()
    original = runtime / "unrelated.txt"
    original.write_text("Preserve this file", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Refusing to adopt"):
        local.credentials(create=True)
    assert original.read_text(encoding="utf-8") == "Preserve this file"


def test_local_runtime_refuses_wrong_owner_and_nonempty_data(runtime):
    """Initialization does not overwrite unexpected marker or cluster contents."""
    local.credentials(create=True)
    marker = runtime / "local.json"
    marker.write_text(json.dumps({"owner": "some-other-project"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="owner marker"):
        local.credentials(create=True)
    marker.unlink()
    runtime.rmdir()
    local.credentials(create=True)
    data = runtime / "postgres-data"
    data.mkdir()
    original = data / "important.txt"
    original.write_text("Unrelated database content", encoding="utf-8")
    with pytest.raises(RuntimeError, match="nonempty PostgreSQL"):
        local.initialize()
    assert original.read_text(encoding="utf-8") == "Unrelated database content"


def test_external_database_environment_requires_explicit_secret(runtime, monkeypatch):
    """An external URL never silently reuses local credentials or a default secret."""
    monkeypatch.setenv(
        "FACILITY_DATABASE_URL", "postgresql+asyncpg://configured-db/acme_facility_dev"
    )
    monkeypatch.delenv("FACILITY_JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="FACILITY_JWT_SECRET"):
        local.environment()
    assert not runtime.exists()
    monkeypatch.setenv("FACILITY_JWT_SECRET", "z" * 48)
    environment = local.environment()
    assert environment["FACILITY_DATABASE_URL"].startswith("postgresql+asyncpg://configured-db/")
    assert not runtime.exists()
