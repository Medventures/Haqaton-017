import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from app import providers
from app.config import Settings


def test_gpu_runtime_profile_is_configurable_from_environment(monkeypatch):
    monkeypatch.setenv('DIARIZATION_BATCH_SIZE', '4')
    monkeypatch.setenv('ASR_RELEASE_CUDA_CACHE', 'true')

    profile = Settings(_env_file=None)

    assert profile.diarization_batch_size == 4
    assert profile.asr_release_cuda_cache is True


@pytest.mark.parametrize('batch_size', [0, 129])
def test_diarization_batch_size_rejects_invalid_bounds(batch_size):
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, diarization_batch_size=batch_size)

    assert any(item['loc'] == ('diarization_batch_size',) for item in error.value.errors())


def test_diarizer_applies_memory_profile_before_moving_to_gpu(monkeypatch):
    profile = Settings(
        _env_file=None,
        diarization_model='local-community-model',
        asr_device='cuda',
        diarization_batch_size=4,
    )
    pipeline = SimpleNamespace(segmentation_batch_size=32, embedding_batch_size=32)

    def move_to_device(device):
        assert device == 'cuda'
        assert pipeline.segmentation_batch_size == 4
        assert pipeline.embedding_batch_size == 4

    pipeline.to = Mock(side_effect=move_to_device)
    load_pipeline = Mock(return_value=pipeline)
    audio_module = ModuleType('pyannote.audio')
    audio_module.Pipeline = SimpleNamespace(from_pretrained=load_pipeline)
    monkeypatch.setitem(sys.modules, 'pyannote', ModuleType('pyannote'))
    monkeypatch.setitem(sys.modules, 'pyannote.audio', audio_module)
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(device=lambda value: value))
    monkeypatch.setattr(providers, 'settings', lambda: profile)

    # Bypass the singleton cache without disturbing a pipeline owned by another test.
    result = providers.diarizer.__wrapped__()

    assert result is pipeline
    load_pipeline.assert_called_once_with('local-community-model')
    pipeline.to.assert_called_once_with('cuda')
