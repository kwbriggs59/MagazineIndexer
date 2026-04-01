"""
SQLAlchemy ORM models for the Magazine Library database.

Tables:
  - magazines: One record per PDF file imported
  - articles:  One record per article extracted from a magazine TOC
  - settings:  Key/value store for all user-configurable settings
"""

from sqlalchemy import Column, Integer, String, Text, TIMESTAMP, LargeBinary, ForeignKey
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func

Base = declarative_base()


class Magazine(Base):
    __tablename__ = "magazines"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    title         = Column(String, nullable=False)
    volume        = Column(Integer)
    issue_number  = Column(Integer)
    season        = Column(String)           # e.g. "Spring", "Fall"
    year          = Column(Integer)
    publication   = Column(String)           # magazine series name, e.g. "Wildfowl Carving"
    pdf_path      = Column(String, unique=True, nullable=True)   # absolute path; NULL for index-only entries
    page_count    = Column(Integer)
    cover_image   = Column(LargeBinary)      # PNG bytes of cover thumbnail
    page_offset   = Column(Integer, default=0)  # unnumbered pages before page 1 (cover, ads, etc.)
    date_imported = Column(TIMESTAMP, server_default=func.now())
    notes         = Column(Text)

    articles = relationship("Article", back_populates="magazine",
                            cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Magazine id={self.id} title={self.title!r} vol={self.volume} issue={self.issue_number}>"


class Article(Base):
    __tablename__ = "articles"

    id                = Column(Integer, primary_key=True, autoincrement=True)
    magazine_id       = Column(Integer, ForeignKey("magazines.id", ondelete="CASCADE"), nullable=False)
    title             = Column(String, nullable=False)
    author            = Column(String)
    page_start        = Column(Integer)
    page_end          = Column(Integer)      # NULL if unknown
    keywords          = Column(Text)         # comma-separated tags
    is_read           = Column(Integer, default=0)   # 0 = unread, 1 = read
    rating            = Column(Integer, default=0)   # 0 = unrated, 1–5 stars
    notes             = Column(Text)
    extraction_method = Column(String)       # "pdf_text" | "ocr" | "ai" | "manual"
    date_added        = Column(TIMESTAMP, server_default=func.now())

    magazine = relationship("Magazine", back_populates="articles")

    def __repr__(self):
        return f"<Article id={self.id} title={self.title!r} page={self.page_start}>"


class Setting(Base):
    __tablename__ = "settings"

    key   = Column(String, primary_key=True)
    value = Column(Text)

    # Known keys:
    #   watched_folder            – absolute path to magazine folder
    #   anthropic_api_key         – stored in plaintext (local DB only)
    #   ocr_confidence_threshold  – int 50–90, default 70
    #   ocr_dpi                   – int, default 300
    #   ocr_language              – string, default "eng"
    #   ask_before_ai             – "true" | "false", default "true"
    #   theme                     – "light" | "dark"

    def __repr__(self):
        return f"<Setting {self.key}={self.value!r}>"
