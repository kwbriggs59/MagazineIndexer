"""
Database connection, session management, and schema initialization.

Usage:
    from database.db import init_db, get_session

    init_db()               # Call once at startup — creates tables + FTS5 if needed
    session = get_session() # Returns a new SQLAlchemy session
"""

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from database.models import Base
import config

_engine = None
_Session = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(f"sqlite:///{config.DB_PATH}", echo=False)

        @event.listens_for(_engine, "connect")
        def _set_fk_pragma(dbapi_conn, _record):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return _engine


def get_session():
    """Return a new SQLAlchemy session. Caller is responsible for closing it."""
    global _Session
    if _Session is None:
        # expire_on_commit=False: column values remain readable after commit+close
        _Session = sessionmaker(bind=_get_engine(), expire_on_commit=False)
    return _Session()


def init_db():
    """
    Create all ORM tables and the FTS5 virtual table + sync triggers.
    Safe to call multiple times — uses CREATE TABLE IF NOT EXISTS semantics.
    """
    engine = _get_engine()
    Base.metadata.create_all(engine)
    _migrate(engine)
    _create_fts5(engine)


def _migrate(engine):
    """Apply additive schema migrations for columns added after initial release."""
    with engine.connect() as conn:
        # --- Migration: nullable pdf_path + publication column ---
        # Check if pdf_path still has NOT NULL constraint (column index 3 = notnull flag)
        result = conn.execute(text("PRAGMA table_info(magazines)"))
        columns = {row[1]: row for row in result.fetchall()}
        pdf_path_col = columns.get("pdf_path")
        pdf_path_notnull = pdf_path_col[3] if pdf_path_col else 0  # index 3 is notnull

        if pdf_path_notnull:
            # Recreate the table without NOT NULL on pdf_path and with publication column.
            # Must disable foreign keys to drop the old table while articles still exist.
            conn.execute(text("PRAGMA foreign_keys=OFF"))
            # Clean up any leftover temp table from a previously failed migration
            conn.execute(text("DROP TABLE IF EXISTS magazines_new"))
            conn.execute(text("""
                CREATE TABLE magazines_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    volume INTEGER,
                    issue_number INTEGER,
                    season TEXT,
                    year INTEGER,
                    publication TEXT,
                    pdf_path TEXT UNIQUE,
                    page_count INTEGER,
                    cover_image BLOB,
                    page_offset INTEGER DEFAULT 0,
                    date_imported TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    notes TEXT
                )
            """))
            conn.execute(text("""
                INSERT INTO magazines_new
                    SELECT id, title, volume, issue_number, season, year,
                           NULL, pdf_path, page_count, cover_image,
                           page_offset, date_imported, notes
                    FROM magazines
            """))
            conn.execute(text("DROP TABLE magazines"))
            conn.execute(text("ALTER TABLE magazines_new RENAME TO magazines"))
            conn.execute(text("PRAGMA foreign_keys=ON"))
            conn.commit()
        else:
            # pdf_path is already nullable — just ensure publication column exists
            try:
                conn.execute(text("ALTER TABLE magazines ADD COLUMN publication TEXT"))
                conn.commit()
            except Exception:
                pass  # column already exists

        # magazines.page_offset — added to support per-magazine page number alignment
        try:
            conn.execute(text("ALTER TABLE magazines ADD COLUMN page_offset INTEGER DEFAULT 0"))
            conn.commit()
        except Exception:
            pass  # column already exists

        # Clean up orphan articles left behind by bulk magazine deletes (bypassed ORM cascade)
        conn.execute(text(
            "DELETE FROM articles WHERE magazine_id NOT IN (SELECT id FROM magazines)"
        ))
        conn.commit()

        # Rebuild FTS index to match the articles table exactly
        conn.execute(text("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')"))
        conn.commit()


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
