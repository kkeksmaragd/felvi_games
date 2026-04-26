"""Baseline – current schema before user-id migration.

This is an empty migration that represents the state of the database
BEFORE the add_user_id_pk migration.  Stamp existing databases against
this revision before running autogenerate.

  alembic stamp 0001

Revision ID: 0001
Revises:
Create Date: 2025-01-01 00:00:00.000000

"""
from __future__ import annotations
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass  # baseline – no schema changes


def downgrade() -> None:
    pass
