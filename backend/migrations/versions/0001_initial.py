"""Initial control-plane schema."""
from alembic import op
import sqlalchemy as sa

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('nodes',
        sa.Column('id', sa.String(100), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('mode', sa.String(20), nullable=False),
        sa.Column('maintenance', sa.Boolean(), nullable=False),
        sa.Column('capabilities', sa.JSON(), nullable=False),
        sa.Column('metrics', sa.JSON(), nullable=False),
        sa.Column('last_seen', sa.DateTime(timezone=True), nullable=False))
    op.create_table('jobs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('source', sa.String(80), nullable=False),
        sa.Column('model', sa.String(100), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False),
        sa.Column('steps', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('progress', sa.Float(), nullable=False),
        sa.Column('node_id', sa.String(100), sa.ForeignKey('nodes.id')),
        sa.Column('attempt_id', sa.String(36)),
        sa.Column('lease_until', sa.DateTime(timezone=True)),
        sa.Column('result', sa.JSON()), sa.Column('error', sa.Text()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True)),
        sa.Column('finished_at', sa.DateTime(timezone=True)))
    op.create_index('ix_jobs_status', 'jobs', ['status'])
    op.create_table('events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('job_id', sa.String(36), sa.ForeignKey('jobs.id'), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_events_job_id', 'events', ['job_id'])


def downgrade():
    op.drop_table('events')
    op.drop_table('jobs')
    op.drop_table('nodes')
