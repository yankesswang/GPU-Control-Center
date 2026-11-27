"""Preserve cancellation intent through lost leases."""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('jobs', sa.Column('cancel_requested', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute("UPDATE jobs SET cancel_requested = true WHERE status IN ('cancelling', 'cancelled')")
    op.alter_column('jobs', 'cancel_requested', server_default=None)


def downgrade():
    op.drop_column('jobs', 'cancel_requested')
