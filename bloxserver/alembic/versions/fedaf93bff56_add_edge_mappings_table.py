"""add edge_mappings table

Revision ID: fedaf93bff56
Revises: 7136cc209524
Create Date: 2026-01-26 07:22:32.557309

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fedaf93bff56'
down_revision: Union[str, Sequence[str], None] = '7136cc209524'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('edge_mappings',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('flow_id', sa.UUID(), nullable=False),
        sa.Column('from_node', sa.String(length=100), nullable=False),
        sa.Column('to_node', sa.String(length=100), nullable=False),
        sa.Column('confidence', sa.Numeric(precision=3, scale=2), nullable=True),
        sa.Column('level', sa.Enum('HIGH', 'MEDIUM', 'LOW', name='confidencelevel'), nullable=True),
        sa.Column('analysis_method', sa.String(length=20), nullable=True),
        sa.Column('proposed_mapping', sa.JSON(), nullable=True),
        sa.Column('user_mapping', sa.JSON(), nullable=True),
        sa.Column('user_confirmed', sa.Boolean(), nullable=False),
        sa.Column('analyzed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['flow_id'], ['flows.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_edge_mappings_edge', 'edge_mappings', ['flow_id', 'from_node', 'to_node'], unique=True)
    op.create_index('idx_edge_mappings_flow', 'edge_mappings', ['flow_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_edge_mappings_flow', table_name='edge_mappings')
    op.drop_index('idx_edge_mappings_edge', table_name='edge_mappings')
    op.drop_table('edge_mappings')
