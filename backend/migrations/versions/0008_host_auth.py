"""Add per-host operation password hash and auth version for model control protection."""
from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('nodes', sa.Column('auth_password_hash', sa.String(256), nullable=True))
    op.add_column('nodes', sa.Column('auth_version', sa.Integer(), nullable=False, server_default='0'))


def downgrade():
    op.drop_column('nodes', 'auth_version')
    op.drop_column('nodes', 'auth_password_hash')
