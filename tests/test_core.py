from __future__ import annotations

import importlib

from app.core.config import Settings, settings
from app.infra.db import Base, async_session_factory, engine


def test_settings_build_database_urls() -> None:
    app_settings = Settings(
        DB_HOST="db",
        DB_PORT=5433,
        DB_USER="user",
        DB_PASSWORD="password",
        DB_NAME="unilake_test",
        JWT_SECRET_KEY="test-secret",
    )

    assert app_settings.database_url == "postgresql+asyncpg://user:password@db:5433/unilake_test"
    assert app_settings.sync_database_url == "postgresql://user:password@db:5433/unilake_test"


def test_database_engine_uses_settings_url() -> None:
    assert engine.url.render_as_string(hide_password=False) == settings.database_url
    assert async_session_factory.kw["expire_on_commit"] is False
    assert Base.metadata is not None


def test_module_model_imports_register_with_base() -> None:
    module_paths = (
        "app.modules.auth.models",
        "app.modules.users.models",
        "app.modules.datasources.models",
        "app.modules.ingestion.models",
        "app.modules.pipeline.models",
        "app.modules.governance.models",
        "app.modules.nlq.models",
        "app.modules.dashboard.models",
        "app.modules.query_history.models",
    )

    for module_path in module_paths:
        module = importlib.import_module(module_path)
        assert module.Base is Base
