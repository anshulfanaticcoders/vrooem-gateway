"""add location sync tracking

Revision ID: c3f4e5a6b7c8
Revises: 6a2b8e0db995
Create Date: 2026-03-11 18:20:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3f4e5a6b7c8'
down_revision: Union[str, None] = '6a2b8e0db995'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('provider_locations', sa.Column('last_seen_at', sa.DateTime(), nullable=True))
    op.add_column('provider_locations', sa.Column('sync_status', sa.String(length=20), nullable=True))
    op.add_column('provider_locations', sa.Column('provider_payload_hash', sa.String(length=64), nullable=True))

    op.create_table(
        'location_sync_runs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('provider', sa.String(length=50), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=True),
        sa.Column('locations_received', sa.Integer(), nullable=True),
        sa.Column('locations_upserted', sa.Integer(), nullable=True),
        sa.Column('locations_deactivated', sa.Integer(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_location_sync_provider_started', 'location_sync_runs', ['provider', 'started_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_location_sync_provider_started', table_name='location_sync_runs')
    op.drop_table('location_sync_runs')
    op.drop_column('provider_locations', 'provider_payload_hash')
    op.drop_column('provider_locations', 'sync_status')
    op.drop_column('provider_locations', 'last_seen_at')
