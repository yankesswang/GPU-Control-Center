"""Persist remote runtime connections without changing existing hosts."""
from alembic import op
import sqlalchemy as sa

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('nodes', sa.Column('connection_host', sa.String(500), nullable=True))
    op.add_column('nodes', sa.Column('connection_services', sa.JSON(), nullable=False, server_default='{}'))
    op.create_unique_constraint('uq_nodes_connection_host', 'nodes', ['connection_host'])


def downgrade():
    op.drop_constraint('uq_nodes_connection_host', 'nodes', type_='unique')
    op.drop_column('nodes', 'connection_services')
    op.drop_column('nodes', 'connection_host')
