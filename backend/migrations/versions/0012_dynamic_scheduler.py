"""Add dynamic scheduler policy and decision audit tables."""
from alembic import op
import sqlalchemy as sa

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'scheduler_policies',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('mode', sa.String(32), nullable=False, server_default='balanced'),
        sa.Column('weight_priority', sa.Float(), nullable=False, server_default='1.0'),
        sa.Column('weight_aging', sa.Float(), nullable=False, server_default='1.0'),
        sa.Column('weight_deadline', sa.Float(), nullable=False, server_default='2.5'),
        sa.Column('weight_fairness', sa.Float(), nullable=False, server_default='2.2'),
        sa.Column('weight_warm_affinity', sa.Float(), nullable=False, server_default='2.0'),
        sa.Column('weight_node_health', sa.Float(), nullable=False, server_default='2.5'),
        sa.Column('weight_sjf', sa.Float(), nullable=False, server_default='0.8'),
        sa.Column('aging_interval_s', sa.Float(), nullable=False, server_default='30.0'),
        sa.Column('fairness_window_s', sa.Integer(), nullable=False, server_default='300'),
        sa.Column('max_queued_per_source', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('source_weights', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('thermal_soft_c', sa.Float(), nullable=False, server_default='80.0'),
        sa.Column('thermal_hard_c', sa.Float(), nullable=False, server_default='88.0'),
        sa.Column('vram_guard_pct', sa.Float(), nullable=False, server_default='94.0'),
        sa.Column('comfyui_busy_penalty', sa.Float(), nullable=False, server_default='2.0'),
        sa.Column('defer_batch_on_peak_tariff', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('route_reservation_s', sa.Integer(), nullable=False, server_default='8'),
        sa.Column('routing_margin', sa.Float(), nullable=False, server_default='1.5'),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        'scheduler_decisions',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('action', sa.String(32), nullable=False),
        sa.Column('job_id', sa.String(36), nullable=True),
        sa.Column('job_name', sa.String(120), nullable=True),
        sa.Column('source', sa.String(80), nullable=True),
        sa.Column('model', sa.String(100), nullable=True),
        sa.Column('sla_class', sa.String(20), nullable=True),
        sa.Column('selected_node_id', sa.String(100), nullable=True),
        sa.Column('selected_node_name', sa.String(100), nullable=True),
        sa.Column('score', sa.Float(), nullable=True),
        sa.Column('policy_mode', sa.String(32), nullable=False, server_default='balanced'),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('breakdown', sa.JSON(), nullable=False, server_default='{}'),
    )
    op.create_index('ix_scheduler_decisions_recorded', 'scheduler_decisions', ['recorded_at'])
    op.create_index('ix_scheduler_decisions_action', 'scheduler_decisions', ['action'])
    op.create_index('ix_scheduler_decisions_job_id', 'scheduler_decisions', ['job_id'])
    op.create_index('ix_scheduler_decisions_source', 'scheduler_decisions', ['source'])
    op.create_index('ix_scheduler_decisions_selected_node_id', 'scheduler_decisions', ['selected_node_id'])
    op.create_index('ix_scheduler_decisions_source_recorded', 'scheduler_decisions', ['source', 'recorded_at'])


def downgrade():
    op.drop_index('ix_scheduler_decisions_source_recorded', table_name='scheduler_decisions')
    op.drop_index('ix_scheduler_decisions_selected_node_id', table_name='scheduler_decisions')
    op.drop_index('ix_scheduler_decisions_source', table_name='scheduler_decisions')
    op.drop_index('ix_scheduler_decisions_job_id', table_name='scheduler_decisions')
    op.drop_index('ix_scheduler_decisions_action', table_name='scheduler_decisions')
    op.drop_index('ix_scheduler_decisions_recorded', table_name='scheduler_decisions')
    op.drop_table('scheduler_decisions')
    op.drop_table('scheduler_policies')
