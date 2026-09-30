from datetime import date
from typing import Literal
from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict
from .patient_input import normalize_iin, normalize_phone, birth_date_from_iin


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

    @field_validator('phone', mode='before', check_fields=False)
    @classmethod
    def canonical_phone(cls, value):
        return normalize_phone(value)

    @field_validator('iin', mode='before', check_fields=False)
    @classmethod
    def canonical_iin(cls, value):
        return normalize_iin(value)


class Register(Strict):
    phone: str = Field(default='', max_length=30)
    name: str = Field(min_length=2, max_length=150)
    iin: str = Field(pattern=r'^[0-9]{12}$')
    email: str = Field(min_length=5, max_length=150, pattern=r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
    password: str = Field(min_length=12, max_length=128)
    code: str = Field(default='', max_length=150)


class Login(Strict):
    email: str = Field(max_length=150)
    password: str = Field(max_length=128)


class PatientInput(Strict):
    name: str = Field(min_length=2, max_length=150)
    iin: str = Field(pattern=r'^[0-9]{12}$')
    birth_date: date
    phone: str = Field(default='', max_length=30)
    sex: Literal['female', 'male', 'unknown'] = 'unknown'
    external_id: str | None = Field(default=None, max_length=100)
    recording_consent: bool = False
    cloud_consent: bool = False
    cloud_audio_consent: bool = False
    openai_audio_consent: bool = False
    processing_consent: bool | None = None

    @model_validator(mode='before')
    @classmethod
    def infer_birth_date(cls, values):
        if isinstance(values, dict) and not values.get('birth_date'):
            inferred = birth_date_from_iin(values.get('iin'))
            if inferred:
                return {**values, 'birth_date': inferred}
        return values

    @model_validator(mode='after')
    def unified_consent(self):
        if self.processing_consent is not None:
            self.recording_consent = self.cloud_consent = self.cloud_audio_consent = self.openai_audio_consent = self.processing_consent
        return self

    @field_validator('birth_date')
    @classmethod
    def valid_date(cls, value):
        if value > date.today() or value.year < 1900:
            raise ValueError('Некорректная дата рождения')
        return value


class Consent(Strict):
    recording_consent: bool = False
    cloud_consent: bool = False
    cloud_audio_consent: bool = False
    openai_audio_consent: bool = False
    processing_consent: bool | None = None

    @model_validator(mode='after')
    def unified_consent(self):
        if self.processing_consent is None and not {'recording_consent', 'cloud_consent'} <= self.model_fields_set:
            raise ValueError('Укажите processing_consent или оба прежних согласия')
        if self.processing_consent is not None:
            self.recording_consent = self.cloud_consent = self.cloud_audio_consent = self.openai_audio_consent = self.processing_consent
        return self


class Segment(Strict):
    speaker: str = Field(pattern=r'^SPEAKER_\d{2,4}$')
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str = Field(max_length=10000)

    @model_validator(mode='after')
    def valid_time(self):
        import math
        if not math.isfinite(self.start) or not math.isfinite(self.end) or self.end < self.start:
            raise ValueError('Некорректные таймкоды')
        return self


class FieldSource(Strict):
    field: str = Field(max_length=50)
    segments: list[int] = Field(default_factory=list, max_length=100)


class DiagnosisSuggestion(Strict):
    code: str = Field(max_length=10)
    name: str = Field(default='', max_length=500)
    reason: str = Field(default='', max_length=2000)


class Consultation(Strict):
    visit_type: Literal['primary', 'repeat'] = 'primary'
    visit_format: Literal['in_person', 'remote'] = 'in_person'
    complaints: str = Field(default='', max_length=15000)
    anamnesis: str = Field(default='', max_length=15000)
    life_history: str = Field(default='', max_length=15000)
    allergies: str = Field(default='', max_length=5000)
    medications: str = Field(default='', max_length=5000)
    chronic_conditions: str = Field(default='', max_length=5000)
    family_history: str = Field(default='', max_length=5000)
    operations: str = Field(default='', max_length=5000)
    habits: str = Field(default='', max_length=5000)
    examination: str = Field(default='', max_length=15000)
    temperature: str = Field(default='', max_length=50)
    height: str = Field(default='', max_length=50)
    weight: str = Field(default='', max_length=50)
    pulse: str = Field(default='', max_length=50)
    respiratory_rate: str = Field(default='', max_length=50)
    blood_pressure: str = Field(default='', max_length=100)
    spo2: str = Field(default='', max_length=50)
    investigations: str = Field(default='', max_length=15000)
    diagnosis: str = Field(default='', max_length=10000)
    diagnosis_code: str = Field(default='', max_length=10)
    recommendations: str = Field(default='', max_length=15000)
    follow_up: str = Field(default='', max_length=5000)
    ai_conclusion: str = Field(default='', max_length=15000)
    ai_test_recommendations: str = Field(default='', max_length=15000)
    ai_diagnosis_variants: str = Field(default='', max_length=15000)
    sources: list[FieldSource] = Field(default_factory=list, max_length=50)
    diagnosis_suggestions: list[DiagnosisSuggestion] = Field(default_factory=list, max_length=5)
    warnings: list[str] = Field(default_factory=list, max_length=20)
    reviewed_fields: list[str] = Field(default_factory=list, max_length=50)


class EncounterPatch(Strict):
    version: int = Field(ge=1)
    fields: Consultation
    speaker_roles: dict[str, Literal['doctor', 'patient', 'nurse', 'unknown']] = Field(default_factory=dict, max_length=1000)
    transcript: list[Segment] | None = Field(default=None, max_length=2000)
    previous_encounter_id: str | None = Field(default=None, max_length=36)


class EncounterStart(Strict):
    visit_type: Literal['primary', 'repeat'] = 'primary'
    previous_encounter_id: str | None = Field(default=None, max_length=36)


class EncounterCreate(Strict):
    draft_token: str = Field(min_length=20, max_length=5000)
    fields: Consultation
    speaker_roles: dict[str, Literal['doctor', 'patient', 'nurse', 'unknown']] = Field(default_factory=dict, max_length=1000)
    transcript: list[Segment] = Field(default_factory=list, max_length=2000)
    previous_encounter_id: str | None = Field(default=None, max_length=36)


class LifecycleAction(Strict):
    version: int | None = Field(default=None, ge=1)
    draft_token: str | None = Field(default=None, max_length=5000)


class PrivacyReview(Strict):
    version: int = Field(ge=1)
    segments: list[Segment] = Field(max_length=2000)


class Version(Strict):
    version: int = Field(ge=1)


class Regenerate(Version):
    target: str = 'all'

    @field_validator('target')
    @classmethod
    def known_target(cls, value):
        from .clinical import GENERATION_TARGETS
        if value not in GENERATION_TARGETS:
            raise ValueError('Неизвестное поле для перегенерации')
        return value


class CloudAudio(Version):
    audio_reviewed: bool


class RecordingTranscribe(Version):
    analyze: bool = True


class MuteAudio(Version):
    segment_indices: list[int] = Field(min_length=1, max_length=2000)


class IdentityStart(Strict):
    purpose: Literal['login', 'register', 'link'] = 'login'
    code: str = Field(default='', max_length=150)
    iin: str = Field(default='', pattern=r'^(?:[0-9]{12})?$')

    @model_validator(mode='after')
    def registration_iin_required(self):
        if self.purpose == 'register' and not self.iin:
            raise ValueError('Для регистрации укажите ИИН: 12 цифр')
        return self


class Signature(Strict):
    signature: str = Field(min_length=20, max_length=200000)
