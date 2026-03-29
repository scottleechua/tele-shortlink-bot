import os
import sqlite3
from contextlib import contextmanager

from cryptography.fernet import Fernet


def _fernet() -> Fernet:
    key = os.environ.get("ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("ENCRYPTION_KEY environment variable is not set")
    return Fernet(key.encode() if isinstance(key, str) else key)


def _encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def _decrypt(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()

DB_PATH = os.environ.get("DB_PATH", "./bot.db")


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL UNIQUE,
                nickname    TEXT,
                is_admin    INTEGER DEFAULT 0,
                added_at    DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS domains (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                nickname            TEXT NOT NULL,
                hostname            TEXT NOT NULL UNIQUE,
                shortio_domain_id   INTEGER,
                api_key             TEXT NOT NULL,
                added_at            DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS podcasts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                apple_id    TEXT NOT NULL,
                rss_url     TEXT NOT NULL,
                domain_id   INTEGER NOT NULL REFERENCES domains(id),
                added_at    DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS links (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                domain_id    INTEGER NOT NULL REFERENCES domains(id),
                original_url TEXT NOT NULL,
                short_url    TEXT NOT NULL,
                slug         TEXT NOT NULL,
                title        TEXT,
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)

    # Bootstrap admin from env if not already present
    admin_id = os.environ.get("ADMIN_USER_ID")
    if admin_id:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO users (telegram_id, nickname, is_admin)
                VALUES (?, 'Admin', 1)
                ON CONFLICT(telegram_id) DO UPDATE SET is_admin=1
                """,
                (int(admin_id),),
            )


# ── Users ──────────────────────────────────────────────────────────────────

def is_allowed(telegram_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
        return row is not None


def is_admin(telegram_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT is_admin FROM users WHERE telegram_id = ? AND is_admin = 1",
            (telegram_id,),
        ).fetchone()
        return row is not None


def list_users():
    with get_conn() as conn:
        return conn.execute(
            "SELECT telegram_id, nickname, is_admin, added_at FROM users ORDER BY added_at"
        ).fetchall()


def add_user(telegram_id: int, nickname: str = None):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (telegram_id, nickname) VALUES (?, ?)",
            (telegram_id, nickname),
        )


def remove_user(telegram_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM users WHERE telegram_id = ?", (telegram_id,))


# ── Domains ────────────────────────────────────────────────────────────────

def list_domains():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, nickname, hostname, shortio_domain_id, api_key FROM domains ORDER BY nickname"
        ).fetchall()
    return [_decrypt_domain(row) for row in rows]


def get_domain(domain_id: int):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, nickname, hostname, shortio_domain_id, api_key FROM domains WHERE id = ?",
            (domain_id,),
        ).fetchone()
    return _decrypt_domain(row) if row else None


def _decrypt_domain(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["api_key"] = _decrypt(d["api_key"])
    return d


def add_domain(nickname: str, hostname: str, shortio_domain_id: int, api_key: str):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO domains (nickname, hostname, shortio_domain_id, api_key)
            VALUES (?, ?, ?, ?)
            """,
            (nickname, hostname, shortio_domain_id, _encrypt(api_key)),
        )


def update_domain_nickname(domain_id: int, nickname: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE domains SET nickname = ? WHERE id = ?",
            (nickname, domain_id),
        )


def remove_domain(domain_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM domains WHERE id = ?", (domain_id,))


# ── Podcasts ───────────────────────────────────────────────────────────────

def list_podcasts():
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT p.id, p.name, p.apple_id, p.rss_url, p.domain_id, d.hostname
            FROM podcasts p
            JOIN domains d ON d.id = p.domain_id
            ORDER BY p.name
            """
        ).fetchall()


def get_podcast(podcast_id: int):
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT p.id, p.name, p.apple_id, p.rss_url, p.domain_id, d.hostname, d.api_key, d.shortio_domain_id
            FROM podcasts p
            JOIN domains d ON d.id = p.domain_id
            WHERE p.id = ?
            """,
            (podcast_id,),
        ).fetchone()


def add_podcast(name: str, apple_id: str, rss_url: str, domain_id: int):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO podcasts (name, apple_id, rss_url, domain_id) VALUES (?, ?, ?, ?)",
            (name, apple_id, rss_url, domain_id),
        )


def update_podcast_name(podcast_id: int, name: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE podcasts SET name = ? WHERE id = ?",
            (name, podcast_id),
        )


def update_podcast_domain(podcast_id: int, domain_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE podcasts SET domain_id = ? WHERE id = ?",
            (domain_id, podcast_id),
        )


def remove_podcast(podcast_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM podcasts WHERE id = ?", (podcast_id,))


# ── Links ──────────────────────────────────────────────────────────────────

def sync_links(domain_id: int, links: list[dict]) -> None:
    """Replace local links for a domain with the current state from Short.io."""
    with get_conn() as conn:
        conn.execute("DELETE FROM links WHERE domain_id = ?", (domain_id,))
        conn.executemany(
            """
            INSERT INTO links (domain_id, original_url, short_url, slug, title)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    domain_id,
                    link.get("originalURL", ""),
                    link.get("secureShortURL") or link.get("shortURL", ""),
                    link.get("path", ""),
                    link.get("title"),
                )
                for link in links
                if link.get("path")
            ],
        )


def list_links_for_domain(domain_id: int):
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT slug, short_url, original_url, title
            FROM links WHERE domain_id = ?
            ORDER BY created_at DESC
            """,
            (domain_id,),
        ).fetchall()


def slug_exists_on_domain(domain_id: int, slug: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM links WHERE domain_id = ? AND slug = ?",
            (domain_id, slug),
        ).fetchone()
        return row is not None


def save_link(domain_id: int, original_url: str, short_url: str, slug: str, title: str = None):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO links (domain_id, original_url, short_url, slug, title)
            VALUES (?, ?, ?, ?, ?)
            """,
            (domain_id, original_url, short_url, slug, title),
        )


def find_link_by_slug(domain_id: int, slug: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT short_url, original_url, title FROM links WHERE domain_id = ? AND slug = ?",
            (domain_id, slug),
        ).fetchone()
