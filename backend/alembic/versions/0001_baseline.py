"""Baseline schema — users, settings, refresh tokens, query logs, conversations.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-31

Equivalent to the legacy hand-written SQL files (``migrations/legacy/``
001_initial + 002_refresh_tokens + 002_drop_unused_query_log_columns +
003_conversations + 003_voice_gender) applied in order, with column types
taken from ``app/models/models.py`` so ``alembic check`` reports no drift:

  - FKs: user_settings/refresh_tokens/conversations → users ON DELETE CASCADE,
    query_logs → users ON DELETE SET NULL, conversation_messages →
    conversations ON DELETE CASCADE.
  - JSONB for query_logs JSON columns on Postgres (JSON elsewhere).
  - Server defaults and CHECK constraints from the SQL files (not compared by
    autogenerate, but kept so raw SQL inserts behave as before).

Existing databases created from the legacy SQL files or from the old
startup ``create_all`` must NOT run this; mark them instead with:

    uv run alembic stamp 0001_baseline
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
_UUID_DEFAULT = sa.text("gen_random_uuid()")
_NOW = sa.text("now()")


def upgrade() -> None:
    """Create the full v1 schema."""
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=True),
        sa.Column("hashed_password", sa.String(), nullable=False),
        sa.Column(
            "language", sa.String(length=20), server_default="en-pidgin", nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    op.create_table(
        "user_settings",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("overlay_opacity", sa.Float(), server_default="1.0", nullable=False),
        sa.Column(
            "overlay_size", sa.String(length=20), server_default="default", nullable=False
        ),
        sa.Column("mic_sensitivity", sa.Integer(), server_default="5", nullable=False),
        sa.Column("sound_effects", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("notifications", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "wake_word_enabled", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("launch_at_login", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("show_in_taskbar", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "voice_gender", sa.String(length=10), server_default="female", nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False
        ),
        sa.CheckConstraint(
            "voice_gender IN ('female', 'male')", name="user_settings_voice_gender_check"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token", sa.String(length=128), nullable=False),
        sa.Column("device_id", sa.String(length=120), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_refresh_tokens_token"), "refresh_tokens", ["token"], unique=True
    )
    op.create_index(
        op.f("ix_refresh_tokens_user_id"), "refresh_tokens", ["user_id"], unique=False
    )

    op.create_table(
        "query_logs",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False
        ),
        sa.Column("language_used", sa.String(length=20), nullable=False),
        sa.Column("transcript_whisper", sa.String(), nullable=True),
        sa.Column("whisper_latency_ms", sa.Integer(), nullable=True),
        sa.Column("vision_response", _JSON, nullable=True),
        sa.Column("beacon_steps", _JSON, nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False
        ),
        sa.Column("rolling_summary", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_conversations_user_id"), "conversations", ["user_id"], unique=True
    )

    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False
        ),
        sa.Column("summarised", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')", name="conversation_messages_role_check"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_conversation_messages_conversation_id"),
        "conversation_messages",
        ["conversation_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop every table created by this revision."""
    op.drop_index(
        op.f("ix_conversation_messages_conversation_id"), table_name="conversation_messages"
    )
    op.drop_table("conversation_messages")
    op.drop_index(op.f("ix_conversations_user_id"), table_name="conversations")
    op.drop_table("conversations")
    op.drop_table("query_logs")
    op.drop_index(op.f("ix_refresh_tokens_user_id"), table_name="refresh_tokens")
    op.drop_index(op.f("ix_refresh_tokens_token"), table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_table("user_settings")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")
