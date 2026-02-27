"""Converts Telethon .session files to Kurigram (Pyrogram) format."""

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

PYROGRAM_SCHEMA = """
CREATE TABLE sessions
(
    dc_id          INTEGER PRIMARY KEY,
    server_address TEXT,
    port           INTEGER,
    api_id         INTEGER,
    test_mode      INTEGER,
    auth_key       BLOB,
    date           INTEGER NOT NULL,
    user_id        INTEGER,
    is_bot         INTEGER
);

CREATE TABLE peers
(
    id             INTEGER PRIMARY KEY,
    access_hash    INTEGER,
    type           INTEGER NOT NULL,
    phone_number   TEXT,
    last_update_on INTEGER NOT NULL DEFAULT (CAST(STRFTIME('%s', 'now') AS INTEGER))
);

CREATE TABLE usernames
(
    id       INTEGER,
    username TEXT,
    FOREIGN KEY (id) REFERENCES peers(id)
);

CREATE TABLE update_state
(
    id   INTEGER PRIMARY KEY,
    pts  INTEGER,
    qts  INTEGER,
    date INTEGER,
    seq  INTEGER
);

CREATE TABLE version
(
    number INTEGER PRIMARY KEY
);

CREATE INDEX idx_peers_id ON peers (id);
CREATE INDEX idx_peers_phone_number ON peers (phone_number);
CREATE INDEX idx_usernames_id ON usernames (id);
CREATE INDEX idx_usernames_username ON usernames (username);

CREATE TRIGGER trg_peers_last_update_on
    AFTER UPDATE
    ON peers
BEGIN
    UPDATE peers
    SET last_update_on = CAST(STRFTIME('%s', 'now') AS INTEGER)
    WHERE id = NEW.id;
END;
"""

PYROGRAM_VERSION = 7

DC_ADDRESSES = {
    1: "149.154.175.53",
    2: "149.154.167.51",
    3: "149.154.175.100",
    4: "149.154.167.91",
    5: "91.108.56.130",
}


def is_telethon_session(path: Path) -> bool:
    """Check if a .session file is in Telethon format."""
    try:
        conn = sqlite3.connect(str(path))
        cursor = conn.cursor()
        columns = [
            col[1] for col in cursor.execute("PRAGMA table_info(version)").fetchall()
        ]
        conn.close()
        return "version" in columns and "number" not in columns
    except Exception:
        return False


def convert_telethon_to_pyrogram(path: Path) -> None:
    """
    Convert a Telethon .session file to Pyrogram format in-place.

    Extracts dc_id and auth_key from the Telethon session,
    creates a new Pyrogram-schema database, and replaces the file.
    """
    src = sqlite3.connect(str(path))
    cursor = src.cursor()

    row = cursor.execute("SELECT dc_id, auth_key FROM sessions").fetchone()

    if not row:
        src.close()
        raise ValueError("Telethon session has no session data")

    dc_id, auth_key = row
    src.close()

    tmp_path = path.with_suffix(".session.tmp")
    dst = sqlite3.connect(str(tmp_path))
    dst.executescript(PYROGRAM_SCHEMA)

    server_address = DC_ADDRESSES.get(dc_id, "149.154.167.51")
    port = 443

    dst.execute(
        "INSERT INTO version VALUES (?)",
        (PYROGRAM_VERSION,),
    )
    dst.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (dc_id, server_address, port, 0, 0, auth_key, 0, 999999999, 0),
    )
    dst.commit()
    dst.close()

    tmp_path.replace(path)

    logger.info(f"Converted Telethon session to Kurigram format: {path.name}")


def ensure_pyrogram_session(path: Path) -> bool:
    """
    Check and convert the session file if needed.
    Returns True if conversion was performed, False if already Pyrogram.
    """
    if not path.exists():
        return False

    if is_telethon_session(path):
        convert_telethon_to_pyrogram(path)
        return True

    return False
