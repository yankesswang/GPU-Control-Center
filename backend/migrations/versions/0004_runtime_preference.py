"""Persist each host's preferred model runtime."""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('nodes', sa.Column('default_runtime', sa.String(20), nullable=False, server_default='lmstudio'))


def downgrade():
    op.drop_column('nodes', 'default_runtime')
