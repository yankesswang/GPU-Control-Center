"""Persist user-defined custom display names for models per host."""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('nodes', sa.Column('model_aliases', sa.JSON(), nullable=False, server_default='{}'))


def downgrade():
    op.drop_column('nodes', 'model_aliases')
