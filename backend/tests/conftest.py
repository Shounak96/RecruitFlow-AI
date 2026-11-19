import os
import re
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.database import Base, get_db
from app.main import app
from app import models  # noqa: F401


configured_url = make_url(get_settings().database_url)
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    configured_url.set(database=f"{configured_url.database}_test").render_as_string(
        hide_password=False
    ),
)


def _ensure_test_database() -> None:
    url = make_url(TEST_DATABASE_URL)
    database_name = url.database or ""
    if not database_name.endswith("_test") or not re.fullmatch(r"[A-Za-z0-9_]+", database_name):
        raise RuntimeError("TEST_DATABASE_URL must target a database ending in '_test'.")
    admin_engine = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            exists = connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": database_name},
            )
            if not exists:
                connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    finally:
        admin_engine.dispose()


@pytest.fixture(scope="session")
def test_engine():
    _ensure_test_database()
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    if engine.dialect.name != "postgresql":
        raise RuntimeError("Milestone 2 integration tests require PostgreSQL.")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(autouse=True)
def isolated_app(test_engine, tmp_path) -> Generator[None, None, None]:
    testing_session = sessionmaker(
        bind=test_engine, class_=Session, autoflush=False, expire_on_commit=False
    )
    with test_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE automatic_send_requests, automatic_send_claims, automatic_send_authorizations, sending_preferences, "
                "received_gmail_messages, email_sends, gmail_oauth_states, gmail_connections, "
                "email_drafts, email_purposes, resumes, sender_profiles "
                "RESTART IDENTITY CASCADE"
            )
        )

    def override_get_db() -> Generator[Session, None, None]:
        with testing_session() as session:
            try:
                yield session
            except Exception:
                session.rollback()
                raise

    get_settings().resume_storage_path = tmp_path / "private-resumes"
    app.dependency_overrides[get_db] = override_get_db
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def db_session(test_engine) -> Generator[Session, None, None]:
    with Session(test_engine, expire_on_commit=False) as session:
        yield session

