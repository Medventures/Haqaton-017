"""Общий реестр ИИН и жизненный цикл приёма; существующие карточки сохранены."""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('patient_identities',
        sa.Column('iin_hash', sa.String(64), primary_key=True),
        sa.Column('patient_id', sa.String(36), sa.ForeignKey('patients.id'), nullable=False))
    op.create_index('ix_patient_identities_patient_id', 'patient_identities', ['patient_id'])
    op.create_index('ix_patients_iin_hash', 'patients', ['iin_hash'])
    # Старые дубли не удаляем и не объединяем: регистр лишь блокирует новые.
    op.execute('INSERT INTO patient_identities (iin_hash, patient_id) SELECT iin_hash, MIN(id) FROM patients GROUP BY iin_hash')
    with op.batch_alter_table('encounters') as batch:
        batch.add_column(sa.Column('started_at', sa.Integer(), nullable=False, server_default='0'))
        batch.add_column(sa.Column('ended_at', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('paused_at', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('paused_seconds', sa.Integer(), nullable=False, server_default='0'))
        batch.add_column(sa.Column('recording_deadline', sa.Integer(), nullable=False, server_default='0'))
        batch.add_column(sa.Column('sent_at', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('previous_encounter_id', sa.String(36), nullable=True))
        batch.create_foreign_key('fk_encounters_previous', 'encounters', ['previous_encounter_id'], ['id'])
        batch.create_index('ix_encounters_previous_encounter_id', ['previous_encounter_id'])
    op.execute('UPDATE encounters SET started_at = created_at, recording_deadline = created_at + 900')
    op.execute("UPDATE encounters SET ended_at = reviewed_at WHERE status IN ('approved', 'exported')")
    op.create_index('ix_audit_object_action', 'audit', ['object_id', 'action'])
    if op.get_bind().dialect.name == 'postgresql':
        # БД используется через FastAPI, публичный Supabase Data API не нужен.
        op.execute('ALTER TABLE patient_identities ENABLE ROW LEVEL SECURITY')
        op.execute('REVOKE ALL ON patient_identities FROM PUBLIC')


def downgrade():
    op.drop_index('ix_audit_object_action', table_name='audit')
    with op.batch_alter_table('encounters') as batch:
        batch.drop_index('ix_encounters_previous_encounter_id')
        batch.drop_constraint('fk_encounters_previous', type_='foreignkey')
        for name in ('previous_encounter_id', 'sent_at', 'recording_deadline', 'paused_seconds', 'paused_at', 'ended_at', 'started_at'):
            batch.drop_column(name)
    op.drop_table('patient_identities')
    op.drop_index('ix_patients_iin_hash', table_name='patients')
