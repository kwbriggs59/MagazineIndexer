"""
Database connection, session management, and schema initialization.

Usage:
    from database.db import init_db, get_session

    init_db()               # Call once at startup — creates tables + FTS5 if needed
    session = get_session() # Returns a new SQLAlchemy session
"""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from database.models import Base
import config

_engine = None
_Session = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(f"sqlite:///{config.DB_PATH}", echo=False)
    return _engine


def get_session():
    """Return a new SQLAlchemy session. Caller is responsible for closing it."""
    global _Session
    if _Session is None:
        _Session = sessionmaker(bind=_get_engine())
    return _Session()


def init_db():
    """
    Create all ORM tables and the FTS5 virtual table + sync triggers.
    Safe to call multiple times — uses CREATE TABLE IF NOT EXISTS semantics.
    """
    engine = _get_engine()
    Base.metadata.create_all(engine)
    _create_fts5(engine)


def _create_fts5(engine):
    """Create the FTS5 virtual table and its three sync triggers if they don't exist."""
    statements = [
        # FTS5 virtual table
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
            title,
            author,
            keywords,
            notes,
            content='articles',
            content_rowid='id'
        )
        """,
        # INSERT trigger
        """
        CREATE TRIGGER IF NOT EXISTS articles_ai
        AFTER INSERT ON articles BEGIN
            INSERT INTO articles_fts(rowid, title, author, keywords, notes)
            VALUES (new.id, new.title, new.author, new.keywords, new.notes);
        END
        """,
        # UPDATE trigger
        """
        CREATE TRIGGER IF NOT EXISTS articles_au
        AFTER UPDATE ON articles BEGIN
            INSERT INTO articles_fts(articles_fts, rowid, title, author, keywords, notes)
            VALUES ('delete', old.id, old.title, old.author, old.keywords, old.notes);
            INSERT INTO articles_fts(rowid, title, author, keywords, notes)
            VALUES (new.id, new.title, new.author, new.keywords, new.notes);
        END
        """,
        # DELETE trigger
        """
        CREATE TRIGGER IF NOT EXISTS articles_ad
        AFTER DELETE ON articles BEGIN
            INSERT INTO articles_fts(articles_fts, rowid, title, author, keywords, notes)
            VALUES ('delete', old.id, old.title, old.author, old.keywords, old.notes);
        END
        """,
    ]

    with engine.connect() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
        conn.commit()


def get_setting(key: str, default=None) -> str | None:
    """Read a single setting value from the database."""
    session = get_session()
    try:
        from database.models import Setting
        row = session.get(Setting, key)
        return row.value if row else default
    finally:
        session.close()


def set_setting(key: str, value: str) -> None:
    """Write a single setting value to the database (upsert)."""
    session = get_session()
    try:
        from database.models import Setting
        row = session.get(Setting, key)
        if row is None:
            row = Setting(key=key, value=value)
            session.add(row)
        else:
            row.value = value
        session.commit()
    finally:
        session.close()
