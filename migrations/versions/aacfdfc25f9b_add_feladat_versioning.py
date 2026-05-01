"""add_feladat_versioning

Revision ID: aacfdfc25f9b
Revises: c491db828a78
Create Date: 2026-05-01 09:08:30.220865

"""
from __future__ import annotations
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'aacfdfc25f9b'
down_revision: Union[str, None] = 'c491db828a78'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('feladatok', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tts_kerdes_szoveg', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('verzio', sa.Integer(), server_default='1', nullable=False))
        batch_op.add_column(sa.Column('statusz', sa.String(length=16), server_default='aktiv', nullable=False))
        batch_op.add_column(sa.Column('elozmeny_feladat_id', sa.String(length=64), nullable=True))
        batch_op.create_index(batch_op.f('ix_feladatok_statusz'), ['statusz'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('feladatok', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_feladatok_statusz'))
        batch_op.drop_column('elozmeny_feladat_id')
        batch_op.drop_column('statusz')
        batch_op.drop_column('verzio')
        batch_op.drop_column('tts_kerdes_szoveg')
