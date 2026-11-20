"""SQLite index stored inside the vault: FTS5 for keywords, sqlite-vec for embeddings.

The index is a cache. Everything in it can be rebuilt from the markdown files, so schema
changes and corruption are handled by dropping and rebuilding rather than migrating.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
import threading
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notes (
    rowid INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    note_id TEXT NOT NULL,
    title TEXT NOT NULL,
    title_key TEXT NOT NULL,
    stem_key TEXT NOT NULL,
    status TEXT NOT NULL,
    source_type TEXT NOT NULL,
    tags TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    mtime REAL NOT NULL,
    content_hash TEXT NOT NULL,
    embedded_hash TEXT
);
CREATE INDEX IF NOT EXISTS notes_note_id ON notes(note_id);
CREATE INDEX IF NOT EXISTS notes_title_key ON notes(title_key);
CREATE INDEX IF NOT EXISTS notes_stem_key ON notes(stem_key);
CREATE INDEX IF NOT EXISTS notes_status ON notes(status, captured_at);
CREATE TABLE IF NOT EXISTS links (
    src INTEGER NOT NULL REFERENCES notes(rowid) ON DELETE CASCADE,
    target_key TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS links_target ON links(target_key);
CREATE INDEX IF NOT EXISTS links_src ON links(src);
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    title, body, tags, tokenize = 'unicode61 remove_diacritics 2'
);
"""
_TABLES = ("links", "notes", "notes_fts", "notes_vec", "meta")


@dataclass(frozen=True)
class NoteRecord:
    rowid: int
    path: str
    note_id: str
    title: str
    status: str
    source_type: str
    tags: list[str]
    captured_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> NoteRecord:
        return cls(
            rowid=row["rowid"],
            path=row["path"],
            note_id=row["note_id"],
            title=row["title"],
            status=row["status"],
            source_type=row["source_type"],
            tags=json.loads(row["tags"]),
            captured_at=row["captured_at"],
        )


_RECORD_COLUMNS = (
    "n.rowid, n.path, n.note_id, n.title, n.status, n.source_type, n.tags, n.captured_at"
)


class IndexStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.needs_rebuild = False
        self.conn = self._connect()
        self.vec_available = self._load_vec()
        try:
            self._ensure_schema()
        except sqlite3.DatabaseError as exc:
            log.error("Index database unusable (%s); recreating %s", exc, self.db_path)
            self.conn.close()
            for suffix in ("", "-wal", "-shm"):
                Path(f"{self.db_path}{suffix}").unlink(missing_ok=True)
            self.conn = self._connect()
            self.vec_available = self._load_vec()
            self._ensure_schema()
            self.needs_rebuild = True

    # -------------------------------------------------------------- plumbing
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        with contextlib.suppress(sqlite3.DatabaseError):
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _load_vec(self) -> bool:
        try:
            import sqlite_vec

            self.conn.enable_load_extension(True)
            sqlite_vec.load(self.conn)
            self.conn.enable_load_extension(False)
            return True
        except (ImportError, AttributeError, sqlite3.Error) as exc:
            log.warning("sqlite-vec unavailable, semantic search disabled: %s", exc)
            return False

    def _ensure_schema(self) -> None:
        with self.transaction() as conn:
            version = None
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='meta'").fetchone():
                row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
                version = int(row[0]) if row else None
            if version is not None and version != SCHEMA_VERSION:
                log.info("Index schema %s != %s; dropping index", version, SCHEMA_VERSION)
                self._drop_all(conn)
                version = None
            for statement in _split_sql(_SCHEMA):
                conn.execute(statement)
            if version is None:
                conn.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
                self.needs_rebuild = True

    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            else:
                self.conn.execute("COMMIT")

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def _drop_all(self, conn: sqlite3.Connection) -> None:
        for table in _TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table}")

    def reset(self) -> None:
        """Drop everything and recreate an empty schema (used by full rebuilds)."""
        with self.transaction() as conn:
            self._drop_all(conn)
            for statement in _split_sql(_SCHEMA):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
            )
        self.needs_rebuild = False

    def get_meta(self, key: str) -> str | None:
        rows = self._query("SELECT value FROM meta WHERE key=?", (key,))
        return rows[0][0] if rows else None

    # ----------------------------------------------------------------- writes
    def upsert_note(
        self,
        *,
        path: str,
        note_id: str,
        title: str,
        body: str,
        status: str,
        source_type: str,
        tags: list[str],
        captured_at: str,
        mtime: float,
        content_hash: str,
        links: list[str],
    ) -> int:
        stem_key = Path(path).stem.lower()
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT rowid, content_hash FROM notes WHERE path=?", (path,)
            ).fetchone()
            values = (
                note_id,
                title,
                title.lower(),
                stem_key,
                status,
                source_type,
                json.dumps(tags),
                captured_at,
                mtime,
                content_hash,
            )
            if row is None:
                cur = conn.execute(
                    "INSERT INTO notes(note_id, title, title_key, stem_key, status, source_type,"
                    " tags, captured_at, mtime, content_hash, path)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (*values, path),
                )
                rowid = int(cur.lastrowid)
            else:
                rowid = int(row["rowid"])
                conn.execute(
                    "UPDATE notes SET note_id=?, title=?, title_key=?, stem_key=?, status=?,"
                    " source_type=?, tags=?, captured_at=?, mtime=?, content_hash=? WHERE rowid=?",
                    (*values, rowid),
                )
                conn.execute("DELETE FROM notes_fts WHERE rowid=?", (rowid,))
                conn.execute("DELETE FROM links WHERE src=?", (rowid,))
            conn.execute(
                "INSERT INTO notes_fts(rowid, title, body, tags) VALUES (?,?,?,?)",
                (rowid, title, body, " ".join(tags)),
            )
            conn.executemany(
                "INSERT INTO links(src, target_key) VALUES (?, ?)",
                [(rowid, target.lower()) for target in links],
            )
        return rowid

    def delete_path(self, path: str) -> bool:
        with self.transaction() as conn:
            row = conn.execute("SELECT rowid FROM notes WHERE path=?", (path,)).fetchone()
            if row is None:
                return False
            rowid = row["rowid"]
            conn.execute("DELETE FROM notes_fts WHERE rowid=?", (rowid,))
            if self._has_vec_table(conn):
                conn.execute("DELETE FROM notes_vec WHERE rowid=?", (rowid,))
            conn.execute("DELETE FROM links WHERE src=?", (rowid,))
            conn.execute("DELETE FROM notes WHERE rowid=?", (rowid,))
        return True

    # ------------------------------------------------------------------ reads
    def file_state(self) -> dict[str, tuple[float, str]]:
        return {
            r["path"]: (r["mtime"], r["content_hash"])
            for r in self._query("SELECT path, mtime, content_hash FROM notes")
        }

    def get_hash(self, path: str) -> str | None:
        rows = self._query("SELECT content_hash FROM notes WHERE path=?", (path,))
        return rows[0][0] if rows else None

    def count(self) -> int:
        return int(self._query("SELECT count(*) FROM notes")[0][0])

    def list_notes(self, status: str | None = None, limit: int = 1000) -> list[NoteRecord]:
        sql = f"SELECT {_RECORD_COLUMNS} FROM notes n"
        params: tuple = ()
        if status:
            sql += " WHERE n.status=?"
            params = (status,)
        sql += " ORDER BY n.captured_at DESC, n.title LIMIT ?"
        return [NoteRecord.from_row(r) for r in self._query(sql, (*params, limit))]

    def get_record(self, path: str) -> NoteRecord | None:
        rows = self._query(f"SELECT {_RECORD_COLUMNS} FROM notes n WHERE n.path=?", (path,))
        return NoteRecord.from_row(rows[0]) if rows else None

    def get_by_note_id(self, note_id: str) -> NoteRecord | None:
        rows = self._query(f"SELECT {_RECORD_COLUMNS} FROM notes n WHERE n.note_id=?", (note_id,))
        return NoteRecord.from_row(rows[0]) if rows else None

    def find_by_title(self, title: str) -> NoteRecord | None:
        """Resolve a wiki-link target: exact title first, then file name (case-insensitive)."""
        key = title.strip().lower()
        rows = self._query(
            f"SELECT {_RECORD_COLUMNS} FROM notes n WHERE n.title_key=? OR n.stem_key=?"
            " ORDER BY n.title_key=? DESC, n.path LIMIT 1",
            (key, key, key),
        )
        return NoteRecord.from_row(rows[0]) if rows else None

    def titles(self) -> list[str]:
        return [r[0] for r in self._query("SELECT title FROM notes ORDER BY title_key")]

    def backlinks(self, path: str) -> list[NoteRecord]:
        record = self._query("SELECT title_key, stem_key FROM notes WHERE path=?", (path,))
        if not record:
            return []
        title_key, stem_key = record[0]
        rows = self._query(
            f"SELECT DISTINCT {_RECORD_COLUMNS} FROM links l JOIN notes n ON n.rowid = l.src"
            " WHERE l.target_key IN (?, ?) AND n.path != ? ORDER BY n.title_key",
            (title_key, stem_key, path),
        )
        return [NoteRecord.from_row(r) for r in rows]

    def tag_counts(self) -> Counter[str]:
        counts: Counter[str] = Counter()
        for (tags,) in self._query("SELECT tags FROM notes"):
            counts.update(json.loads(tags))
        return counts

    def body(self, rowid: int) -> str:
        rows = self._query("SELECT body FROM notes_fts WHERE rowid=?", (rowid,))
        return rows[0][0] if rows else ""

    def keyword_search(
        self, fts_query: str, limit: int = 50
    ) -> list[tuple[NoteRecord, str, float]]:
        rows = self._query(
            f"SELECT {_RECORD_COLUMNS},"
            " snippet(notes_fts, 1, '[', ']', '…', 16) AS snip,"
            " bm25(notes_fts, 10.0, 1.0, 5.0) AS rank"
            " FROM notes_fts JOIN notes n ON n.rowid = notes_fts.rowid"
            " WHERE notes_fts MATCH ? ORDER BY rank LIMIT ?",
            (fts_query, limit),
        )
        return [(NoteRecord.from_row(r), r["snip"], -float(r["rank"])) for r in rows]

    # ---------------------------------------------------------------- vectors
    def _has_vec_table(self, conn: sqlite3.Connection) -> bool:
        return (
            conn.execute("SELECT 1 FROM sqlite_master WHERE name='notes_vec'").fetchone()
            is not None
        )

    def ensure_vec_table(self, model: str, dim: int) -> None:
        if not self.vec_available:
            raise RuntimeError("sqlite-vec is not available")
        with self.transaction() as conn:
            current = dict(
                conn.execute(
                    "SELECT key, value FROM meta WHERE key IN ('embed_model', 'embed_dim')"
                ).fetchall()
            )
            if (
                current.get("embed_model") == model
                and current.get("embed_dim") == str(dim)
                and self._has_vec_table(conn)
            ):
                return
            log.info("Creating vector table for %s (dim %d)", model, dim)
            conn.execute("DROP TABLE IF EXISTS notes_vec")
            conn.execute(
                f"CREATE VIRTUAL TABLE notes_vec USING vec0("
                f"embedding float[{int(dim)}] distance_metric=cosine)"
            )
            conn.execute("UPDATE notes SET embedded_hash = NULL")
            conn.executemany(
                "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                [("embed_model", model), ("embed_dim", str(dim))],
            )

    def embedding_model(self) -> str | None:
        return self.get_meta("embed_model")

    def needing_embedding(self, limit: int = 10_000) -> list[tuple[int, str, str]]:
        """``(rowid, content_hash, title)`` for notes whose embedding is missing or stale."""
        rows = self._query(
            "SELECT rowid, content_hash, title FROM notes"
            " WHERE embedded_hash IS NULL OR embedded_hash != content_hash LIMIT ?",
            (limit,),
        )
        return [(r[0], r[1], r[2]) for r in rows]

    def set_embedding(self, rowid: int, vector: list[float], content_hash: str) -> None:
        from sqlite_vec import serialize_float32

        with self.transaction() as conn:
            current = conn.execute(
                "SELECT content_hash FROM notes WHERE rowid=?", (rowid,)
            ).fetchone()
            if current is None or current[0] != content_hash:
                return  # note changed or vanished while embedding; it will be picked up again
            conn.execute("DELETE FROM notes_vec WHERE rowid=?", (rowid,))
            conn.execute(
                "INSERT INTO notes_vec(rowid, embedding) VALUES (?, ?)",
                (rowid, serialize_float32(vector)),
            )
            conn.execute("UPDATE notes SET embedded_hash=? WHERE rowid=?", (content_hash, rowid))

    def vector_search(self, vector: list[float], limit: int = 20) -> list[tuple[NoteRecord, float]]:
        from sqlite_vec import serialize_float32

        with self._lock:
            if not self.vec_available or not self._has_vec_table(self.conn):
                return []
            rows = self.conn.execute(
                f"SELECT {_RECORD_COLUMNS}, v.distance FROM"
                " (SELECT rowid, distance FROM notes_vec WHERE embedding MATCH ? AND k = ?) v"
                " JOIN notes n ON n.rowid = v.rowid ORDER BY v.distance",
                (serialize_float32(vector), limit),
            ).fetchall()
        return [(NoteRecord.from_row(r), float(r["distance"])) for r in rows]

    def embedded_count(self) -> int:
        return int(
            self._query("SELECT count(*) FROM notes WHERE embedded_hash = content_hash")[0][0]
        )


def _split_sql(script: str) -> list[str]:
    return [s.strip() for s in script.split(";") if s.strip()]
