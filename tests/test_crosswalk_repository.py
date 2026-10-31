from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.infra.db.base import Base
from app.modules.pipeline.identity_contracts import IdentityConfigurationError
from app.modules.pipeline.identity_repository import (
    IDENTITIES,
    IDENTITY_TABLES,
    RELEASES,
    CrosswalkRepository,
)

AT = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def database(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'crosswalk.db'}",
        execution_options={"schema_translate_map": {"app": None}},
    )

    @event.listens_for(engine, "connect")
    def connect(connection, _):
        connection.isolation_level = None
        connection.execute("PRAGMA foreign_keys=ON")

    @event.listens_for(engine, "begin")
    def begin(connection):
        connection.exec_driver_sql("BEGIN")

    Base.metadata.create_all(engine, tables=IDENTITY_TABLES)
    yield engine
    engine.dispose()


def register(repo, namespace="A", entity="position", source="007", canonical_id=None):
    return repo.register(
        namespace=namespace,
        entity=entity,
        source=(source,),
        canonical_id=canonical_id,
        evidence="synthetic-approved-match",
        valid_from=AT,
    )


def test_durable_retry_namespaces_and_zeroes(database):
    with database.begin() as connection:
        repo = CrosswalkRepository(connection)
        identity = register(repo)
        assert register(repo) == identity
        assert register(repo, namespace="B") != identity
        assert register(repo, source="7") != identity
        repo.freeze("v1")
    with database.begin() as connection:
        repo = CrosswalkRepository(connection)
        assert register(repo) == identity
        assert len(repo.load("v1").entries) == 3
        assert len(connection.execute(select(IDENTITIES)).all()) == 3


def test_explicit_match_and_shared_employee_lecturer(database):
    with database.begin() as connection:
        repo = CrosswalkRepository(connection)
        employee = register(repo, entity="employee")
        assert register(repo, namespace="B", entity="employee", canonical_id=employee) == employee
        assert register(repo, entity="lecturer", canonical_id=employee) == employee
        with pytest.raises(IdentityConfigurationError, match="SHARED_IDENTITY_REQUIRED"):
            register(repo, entity="lecturer", source="other")
        with pytest.raises(IdentityConfigurationError, match="CANONICAL_ENTITY_MISMATCH"):
            register(repo, entity="position", canonical_id=employee)
        with pytest.raises(
            IdentityConfigurationError, match="COMPOSITE_IDENTITY_REQUIRES_COMPONENTS"
        ):
            register(repo, entity="employee_qualification")


def test_cannot_remap_or_overwrite_release(database):
    with database.begin() as connection:
        repo = CrosswalkRepository(connection)
        first = register(repo)
        original = repo.freeze("v1")
        assert repo.freeze("v1") == original
        second = register(repo, source="other")
        with pytest.raises(IdentityConfigurationError, match="CROSSWALK_IMMUTABLE_CONFLICT"):
            register(repo, canonical_id=second)
        with pytest.raises(IdentityConfigurationError, match="CROSSWALK_VERSION_CONFLICT"):
            repo.freeze("v1")
        assert repo.load("v1") == original
        assert repo.load("v1").entries[0].canonical_id == first
        assert len(repo.freeze("v2").entries) == 2


def test_outer_transaction_rollback_includes_allocation(database):
    def failed_transaction():
        with database.begin() as connection:
            register(CrosswalkRepository(connection))
            raise RuntimeError("rollback")

    with pytest.raises(RuntimeError):
        failed_transaction()
    with database.begin() as connection:
        assert not connection.execute(select(IDENTITIES)).all()


def test_transaction_and_evidence_required(database):
    with (
        database.connect() as connection,
        pytest.raises(IdentityConfigurationError, match="CROSSWALK_TRANSACTION_REQUIRED"),
    ):
        CrosswalkRepository(connection)
    with (
        database.begin() as connection,
        pytest.raises(IdentityConfigurationError, match="CROSSWALK_EVIDENCE_REQUIRED"),
    ):
        CrosswalkRepository(connection).register(
            namespace="A", entity="position", source=("1",), evidence="", valid_from=AT
        )


def test_release_integrity_checked_when_loading(database):
    with database.begin() as connection:
        repo = CrosswalkRepository(connection)
        register(repo)
        repo.freeze("v1")
        connection.execute(update(RELEASES).values(fingerprint="0" * 64))
        with pytest.raises(IdentityConfigurationError, match="CROSSWALK_FINGERPRINT_MISMATCH"):
            repo.load("v1")


def test_postgresql_ddl_compiles_without_live_database():
    sql = "\n".join(
        str(CreateTable(table).compile(dialect=postgresql.dialect())) for table in IDENTITY_TABLES
    )
    assert "GENERATED BY DEFAULT AS IDENTITY" in sql
    assert "PRIMARY KEY (namespace, entity, source_key)" in sql
    assert "REFERENCES app.canonical_identity" in sql
