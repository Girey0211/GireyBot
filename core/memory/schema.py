"""
메모리 DB 스키마 및 마이그레이션
"""

import logging

import aiosqlite

logger = logging.getLogger("girey-bot.memory")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS conversations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    channel_id      INTEGER NOT NULL,
    channel_name    TEXT    NOT NULL DEFAULT '',
    user_id         INTEGER NOT NULL,
    user_name       TEXT    NOT NULL,
    user_message    TEXT    NOT NULL,
    bot_response    TEXT    NOT NULL,
    reaction_count  INTEGER DEFAULT 0,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_conv_channel ON conversations(channel_id, created_at);
CREATE INDEX IF NOT EXISTS idx_conv_user    ON conversations(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_conv_guild   ON conversations(guild_id, created_at);
CREATE INDEX IF NOT EXISTS idx_conv_guild_channel ON conversations(guild_id, channel_id, created_at);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    channel_id      INTEGER NOT NULL,
    channel_name    TEXT    NOT NULL DEFAULT '',
    user_id         INTEGER NOT NULL,
    user_name       TEXT    NOT NULL,
    content         TEXT    NOT NULL,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_msg_channel ON messages(channel_id, created_at);
CREATE INDEX IF NOT EXISTS idx_msg_guild   ON messages(guild_id, created_at);
CREATE INDEX IF NOT EXISTS idx_msg_guild_channel ON messages(guild_id, channel_id, created_at);

CREATE TABLE IF NOT EXISTS summaries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    channel_id      INTEGER NOT NULL,
    summary         TEXT    NOT NULL,
    period_start    TEXT    NOT NULL,
    period_end      TEXT    NOT NULL,
    message_count   INTEGER DEFAULT 0,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sum_channel ON summaries(channel_id, created_at);
CREATE INDEX IF NOT EXISTS idx_sum_guild ON summaries(guild_id, created_at);

CREATE TABLE IF NOT EXISTS important_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    channel_id      INTEGER NOT NULL,
    event_title     TEXT    NOT NULL,
    description     TEXT    NOT NULL,
    participants    TEXT    DEFAULT '[]',
    importance      TEXT    DEFAULT 'high',
    occurred_at     TEXT    NOT NULL,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_events_guild ON important_events(guild_id, occurred_at);

CREATE TABLE IF NOT EXISTS user_facts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id          INTEGER NOT NULL,
    user_id           INTEGER NOT NULL,
    fact              TEXT    NOT NULL,
    category          TEXT    DEFAULT 'info',
    source_message_id INTEGER,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_facts_user ON user_facts(user_id);

CREATE TABLE IF NOT EXISTS user_feedback (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL UNIQUE,
    guild_id            INTEGER NOT NULL,
    score               INTEGER DEFAULT 0,
    violation_count     INTEGER DEFAULT 0,
    last_violation_type TEXT    DEFAULT NULL,
    last_violation_at   TEXT    DEFAULT NULL,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_feedback_user ON user_feedback(user_id);
CREATE INDEX IF NOT EXISTS idx_feedback_guild ON user_feedback(guild_id, score);
"""


async def migrate(db: aiosqlite.Connection) -> None:
    """기존 DB에 새 컬럼이 없으면 추가합니다."""
    async with db.execute("PRAGMA table_info(conversations)") as cursor:
        columns = {row[1] for row in await cursor.fetchall()}

    if "channel_name" not in columns:
        await db.execute(
            "ALTER TABLE conversations ADD COLUMN channel_name TEXT NOT NULL DEFAULT ''"
        )
        logger.info("마이그레이션: conversations.channel_name 컬럼 추가")
