"""Add MCP client API tokens table for external agent authentication and RBAC."""
from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'mcp_tokens',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('source_tag', sa.String(80), nullable=False),
        sa.Column('role', sa.String(20), nullable=False, server_default='infer'),
        sa.Column('token_prefix', sa.String(24), nullable=False),
        sa.Column('token_hash', sa.String(64), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('request_count', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_mcp_tokens_token_hash', 'mcp_tokens', ['token_hash'], unique=True)


def downgrade():
    op.drop_index('ix_mcp_tokens_token_hash', table_name='mcp_tokens')
    op.drop_table('mcp_tokens')
