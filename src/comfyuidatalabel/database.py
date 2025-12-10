from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Generator

from sqlmodel import Session, SQLModel, create_engine


_engine = None
_engine_url: str | None = None


def _build_engine(url: str):
    return create_engine(
        url,
        connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
    )


def get_engine():
    global _engine, _engine_url
    database_url = os.getenv("DATABASE_URL", "sqlite:///./comfyui_data_label.db")
    if _engine is None or _engine_url != database_url:
        _engine = _build_engine(database_url)
        _engine_url = database_url
    return _engine


def init_db() -> None:
    SQLModel.metadata.create_all(get_engine())


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    with Session(get_engine()) as session:
        yield session
