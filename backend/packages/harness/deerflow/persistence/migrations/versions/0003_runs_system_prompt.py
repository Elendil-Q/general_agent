"""Add system-prompt inspection columns to ``runs``.

Revision ID: 0003_runs_system_prompt
Revises: 0002_runs_token_usage
Create Date: 2026-07-17

Adds four nullable columns that store the most recently captured lead-agent
system prompt for a run (the full, untruncated prompt plus caller, call index,
and capture timestamp). These back the frontend "System Prompt" debug button.

Schema parity with ``Base.metadata``
------------------------------------

The ORM model declares all four columns as ``Mapped[... | None]`` (Optional),
so SQLAlchemy infers ``nullable=True`` and ``Base.metadata.create_all`` (the
empty-DB bootstrap path) produces them as nullable with no server default. This
migration mirrors that shape exactly so legacy-upgraded databases are
schema-identical to fresh ones.

All columns are nullable, so ``ALTER TABLE runs ADD COLUMN ...`` succeeds on a
populated table without a ``server_default`` -- existing rows pick up ``NULL``,
which is the correct "no prompt captured yet" state.

Idempotency
-----------

Uses ``safe_add_column`` so re-running this revision against a DB where a
column already exists is a no-op (manual ALTER, concurrent bootstrap, etc.).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from deerflow.persistence.migrations._helpers import safe_add_column, safe_drop_column

# revision identifiers, used by Alembic.
revision: str = "0003_runs_system_prompt"
down_revision: str | Sequence[str] | None = "0002_runs_token_usage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    safe_add_column(
        "runs",
        sa.Column("last_system_prompt", sa.Text(), nullable=True),
    )
    safe_add_column(
        "runs",
        sa.Column("last_system_prompt_caller", sa.String(length=64), nullable=True),
    )
    safe_add_column(
        "runs",
        sa.Column("last_system_prompt_call_index", sa.Integer(), nullable=True),
    )
    safe_add_column(
        "runs",
        sa.Column(
            "last_system_prompt_captured_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    safe_drop_column("runs", "last_system_prompt_captured_at")
    safe_drop_column("runs", "last_system_prompt_call_index")
    safe_drop_column("runs", "last_system_prompt_caller")
    safe_drop_column("runs", "last_system_prompt")
