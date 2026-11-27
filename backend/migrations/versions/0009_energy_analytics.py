"""Add power telemetry samples, electricity tariff settings, and manual bill records."""
from alembic import op
import sqlalchemy as sa

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'power_samples',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('node_id', sa.String(100), nullable=False),
        sa.Column('node_name', sa.String(100), nullable=False),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('interval_s', sa.Float(), nullable=False, server_default='4.0'),
        sa.Column('gpu_power_w', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('gpu_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('avg_utilization', sa.Float(), nullable=True),
        sa.Column('avg_temperature_c', sa.Float(), nullable=True),
        sa.Column('per_gpu', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('active_job_id', sa.String(36), nullable=True),
        sa.Column('active_model', sa.String(100), nullable=True),
        sa.Column('simulated', sa.Boolean(), nullable=False, server_default='false'),
    )
    op.create_index('ix_power_samples_node_id', 'power_samples', ['node_id'])
    op.create_index('ix_power_samples_recorded_at', 'power_samples', ['recorded_at'])
    op.create_index('ix_power_samples_node_recorded', 'power_samples', ['node_id', 'recorded_at'])

    op.create_table(
        'tariff_settings',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('currency', sa.String(8), nullable=False, server_default='TWD'),
        sa.Column('tariff_mode', sa.String(20), nullable=False, server_default='tou_tw'),
        sa.Column('flat_rate', sa.Float(), nullable=False, server_default='3.85'),
        sa.Column('peak_rate', sa.Float(), nullable=False, server_default='5.06'),
        sa.Column('off_peak_rate', sa.Float(), nullable=False, server_default='2.06'),
        sa.Column('non_summer_peak_rate', sa.Float(), nullable=False, server_default='4.86'),
        sa.Column('non_summer_off_peak_rate', sa.Float(), nullable=False, server_default='1.99'),
        sa.Column('peak_start_hour', sa.Integer(), nullable=False, server_default='9'),
        sa.Column('peak_end_hour', sa.Integer(), nullable=False, server_default='24'),
        sa.Column('weekend_off_peak', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('summer_months', sa.JSON(), nullable=False, server_default='[6, 7, 8, 9]'),
        sa.Column('timezone_offset_hours', sa.Float(), nullable=False, server_default='8.0'),
        sa.Column('pue_factor', sa.Float(), nullable=False, server_default='1.12'),
        sa.Column('base_host_watts', sa.Float(), nullable=False, server_default='85.0'),
        sa.Column('co2_kg_per_kwh', sa.Float(), nullable=False, server_default='0.494'),
        sa.Column('monthly_budget', sa.Float(), nullable=True, server_default='3000.0'),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        'electricity_bills',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('billing_date', sa.String(10), nullable=False),
        sa.Column('period_start', sa.String(10), nullable=False),
        sa.Column('period_end', sa.String(10), nullable=False),
        sa.Column('label', sa.String(120), nullable=False),
        sa.Column('category', sa.String(32), nullable=False, server_default='utility_bill'),
        sa.Column('node_id', sa.String(100), nullable=True),
        sa.Column('kwh', sa.Float(), nullable=False),
        sa.Column('cost', sa.Float(), nullable=False),
        sa.Column('currency', sa.String(8), nullable=False, server_default='TWD'),
        sa.Column('peak_kwh', sa.Float(), nullable=True),
        sa.Column('off_peak_kwh', sa.Float(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_electricity_bills_billing_date', 'electricity_bills', ['billing_date'])


def downgrade():
    op.drop_index('ix_electricity_bills_billing_date', table_name='electricity_bills')
    op.drop_table('electricity_bills')
    op.drop_table('tariff_settings')
    op.drop_index('ix_power_samples_node_recorded', table_name='power_samples')
    op.drop_index('ix_power_samples_recorded_at', table_name='power_samples')
    op.drop_index('ix_power_samples_node_id', table_name='power_samples')
    op.drop_table('power_samples')
