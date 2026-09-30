"""Неизменяемые согласия пациентов и отдельные попытки электронной подписи."""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('patient_consents',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('patient_id', sa.String(36), sa.ForeignKey('patients.id'), nullable=False),
        sa.Column('doctor_id', sa.String(36), sa.ForeignKey('doctors.id'), nullable=False),
        sa.Column('state', sa.String(20), nullable=False),
        sa.Column('document_version', sa.String(30), nullable=False),
        sa.Column('document_sha256', sa.String(64), nullable=False),
        sa.Column('document', sa.Text(), nullable=False),
        sa.Column('verification', sa.Text(), nullable=False),
        sa.Column('created_at', sa.Integer(), nullable=False),
        sa.Column('signed_at', sa.Integer(), nullable=True),
        sa.Column('revoked_at', sa.Integer(), nullable=True))
    op.create_index('ix_patient_consents_patient_id', 'patient_consents', ['patient_id'])
    op.create_index('ix_patient_consents_doctor_id', 'patient_consents', ['doctor_id'])
    op.create_table('patient_consent_attempts',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('consent_id', sa.String(36), sa.ForeignKey('patient_consents.id'), nullable=False),
        sa.Column('doctor_id', sa.String(36), sa.ForeignKey('doctors.id'), nullable=False),
        sa.Column('session_hash', sa.String(64), nullable=False),
        sa.Column('method', sa.String(10), nullable=False),
        sa.Column('state', sa.String(20), nullable=False),
        sa.Column('data', sa.Text(), nullable=False),
        sa.Column('created_at', sa.Integer(), nullable=False),
        sa.Column('expires_at', sa.Integer(), nullable=False))
    op.create_index('ix_patient_consent_attempts_consent_id', 'patient_consent_attempts', ['consent_id'])
    op.create_index('ix_patient_consent_attempts_doctor_id', 'patient_consent_attempts', ['doctor_id'])
    if op.get_bind().dialect.name == 'postgresql':
        for table in ('patient_consents', 'patient_consent_attempts'):
            op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
            op.execute(f'REVOKE ALL ON {table} FROM PUBLIC')
            for role in ('anon', 'authenticated'):
                op.execute(f"""DO $$ BEGIN
                    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                        REVOKE ALL ON {table} FROM {role};
                    END IF;
                END $$""")


def downgrade():
    op.drop_table('patient_consent_attempts')
    op.drop_table('patient_consents')
