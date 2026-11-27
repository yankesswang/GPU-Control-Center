"""Add hardware telemetry history samples and backend anomaly/ops event audit table."""
from alembic import op
import sqlalchemy as sa

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'hardware_samples',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('node_id', sa.String(100), nullable=False),
        sa.Column('node_name', sa.String(100), nullable=False),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('interval_s', sa.Float(), nullable=False, server_default='4.0'),
        sa.Column('cpu_percent', sa.Float(), nullable=True),
        sa.Column('cpu_temp_c', sa.Float(), nullable=True),
        sa.Column('load_avg_1m', sa.Float(), nullable=True),
        sa.Column('ram_percent', sa.Float(), nullable=True),
        sa.Column('ram_used_gb', sa.Float(), nullable=True),
        sa.Column('ram_total_gb', sa.Float(), nullable=True),
        sa.Column('disk_percent', sa.Float(), nullable=True),
        sa.Column('gpu_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('avg_gpu_util', sa.Float(), nullable=True),
        sa.Column('max_gpu_util', sa.Float(), nullable=True),
        sa.Column('total_vram_used_mb', sa.Float(), nullable=True),
        sa.Column('total_vram_total_mb', sa.Float(), nullable=True),
        sa.Column('avg_gpu_temp_c', sa.Float(), nullable=True),
        sa.Column('max_gpu_temp_c', sa.Float(), nullable=True),
        sa.Column('total_power_w', sa.Float(), nullable=True),
        sa.Column('per_gpu', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('runtimes_online', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('active_job_id', sa.String(36), nullable=True),
        sa.Column('active_model', sa.String(100), nullable=True),
        sa.Column('simulated', sa.Boolean(), nullable=False, server_default='false'),
    )
    op.create_index('ix_hardware_samples_node_id', 'hardware_samples', ['node_id'])
    op.create_index('ix_hardware_samples_recorded_at', 'hardware_samples', ['recorded_at'])
    op.create_index('ix_hardware_samples_node_recorded', 'hardware_samples', ['node_id', 'recorded_at'])

    op.create_table(
        'hardware_events',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('node_id', sa.String(100), nullable=False),
        sa.Column('node_name', sa.String(100), nullable=False),
        sa.Column('gpu_index', sa.Integer(), nullable=True),
        sa.Column('severity', sa.String(16), nullable=False, server_default='info'),
        sa.Column('event_type', sa.String(40), nullable=False),
        sa.Column('metric_name', sa.String(40), nullable=True),
        sa.Column('metric_value', sa.Float(), nullable=True),
        sa.Column('threshold', sa.Float(), nullable=True),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('acknowledged', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('simulated', sa.Boolean(), nullable=False, server_default='false'),
    )
    op.create_index('ix_hardware_events_recorded_at', 'hardware_events', ['recorded_at'])
    op.create_index('ix_hardware_events_node_id', 'hardware_events', ['node_id'])
    op.create_index('ix_hardware_events_event_type', 'hardware_events', ['event_type'])
    op.create_index('ix_hardware_events_node_recorded', 'hardware_events', ['node_id', 'recorded_at'])


def downgrade():
    op.drop_index('ix_hardware_events_node_recorded', table_name='hardware_events')
    op.drop_index('ix_hardware_events_event_type', table_name='hardware_events')
    op.drop_index('ix_hardware_events_node_id', table_name='hardware_events')
    op.drop_index('ix_hardware_events_recorded_at', table_name='hardware_events')
    op.drop_table('hardware_events')
    op.drop_index('ix_hardware_samples_node_recorded', table_name='hardware_samples')
    op.drop_index('ix_hardware_samples_recorded_at', table_name='hardware_samples')
    op.drop_index('ix_hardware_samples_node_id', table_name='hardware_samples')
    op.drop_table('hardware_samples')
