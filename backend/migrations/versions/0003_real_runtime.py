"""Runtime discovery, inputs and live generation state."""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('nodes', sa.Column('model_catalog', sa.JSON(), nullable=False, server_default='[]'))
    op.add_column('jobs', sa.Column('parameters', sa.JSON(), nullable=False, server_default='{}'))
    op.add_column('jobs', sa.Column('runtime_state', sa.JSON(), nullable=False, server_default='{}'))
    for table, column in [('nodes', 'model_catalog'), ('jobs', 'parameters'), ('jobs', 'runtime_state')]:
        op.alter_column(table, column, server_default=None)


def downgrade():
    op.drop_column('jobs', 'runtime_state')
    op.drop_column('jobs', 'parameters')
    op.drop_column('nodes', 'model_catalog')
