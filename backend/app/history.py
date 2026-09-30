"""Зашифрованные версии документа в существующем хранилище заданий."""
from copy import deepcopy
from .db import Job


def snapshot(db, encounter, action):
    db.add(Job(encounter_id=encounter.id, kind='revision', state='done', payload={
        'action': action, 'version': encounter.version,
        'snapshot': deepcopy({key: getattr(encounter, key) for key in (
            'fields', 'transcript', 'redacted_transcript', 'speaker_roles', 'status', 'reviewed_at', 'privacy_reviewed',
            'started_at', 'ended_at', 'paused_at', 'paused_seconds', 'sent_at', 'previous_encounter_id')})}))
