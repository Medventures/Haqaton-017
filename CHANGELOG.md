# Журнал изменений

Каждая запись соответствует одному коммиту. Время — Asia/Qyzylorda (UTC+05:00).

## 2026-09-30 12:59:42 +05:00 — Приложение переименовано в Anamio; добавлен журнал изменений

Изменены: `AGENTS.md`, `README.md`, `backend/app/main.py`, `deploy/gpu/README.md`, `deploy/placeholder.html`, `frontend/index.html`, `frontend/src/App.vue`, `frontend/src/Auth.vue`, `frontend/src/UmcLogo.vue`, `scripts/commit.py`.

## 2026-09-30 13:07:19 +05:00 — Добавлен справочник МКБ-10 с поиском по кодам, сокращениям и опечаткам

Изменены: `backend/app/catalogs/README.md`, `backend/app/catalogs/icd10.ru.json`, `backend/app/diagnoses.py`, `backend/tests/test_diagnoses.py`.

## 2026-09-30 13:21:10 +05:00 — Добавлены автоматический анализ OpenAI, архив аудио и версий приёма, API МИС

Изменены: `.env.example`, `backend/app/config.py`, `backend/app/history.py`, `backend/app/main.py`, `backend/app/openai_asr.py`, `backend/app/openai_llm.py`, `backend/app/providers.py`, `backend/app/schemas.py`, `backend/app/worker.py`, `backend/tests/test_consultation_pipeline.py`, `backend/tests/test_openai_asr.py`, `backend/tests/test_openai_llm.py`, `backend/tests/test_workflow.py`.

## 2026-09-30 13:23:18 +05:00 — Исправлены остановка записи, пауза и ожидание разрешения микрофона

Изменены: `frontend/package.json`, `frontend/src/recorder.js`, `frontend/tests/recorder.test.js`.

## 2026-09-30 13:25:09 +05:00 — Обновлён лист консультации: загрузка аудио, источники полей, диагнозы и утверждение

Изменены: `frontend/src/App.vue`, `frontend/src/ClinicalField.vue`, `frontend/src/Consultation.vue`, `frontend/src/DiagnosisPicker.vue`, `frontend/src/Settings.vue`, `frontend/src/recorder.js`, `frontend/src/style.css`.

## 2026-09-30 13:27:04 +05:00 — Документированы новый конвейер, хранение записей и результаты сквозной проверки

Изменены: `README.md`, `docs/verification-2026-09-30.md`.

## 2026-09-30 13:31:52 +05:00 — Зафиксированы успешные проверки релиза на сервере и OpenAI API

Изменены: `docs/verification-2026-09-30.md`.

## 2026-09-30 13:35:16 +05:00 — Убраны лишние пояснения из README, упрощено описание приложения

Изменены: `README.md`.

## 2026-09-30 13:58:42 +05:00 — Добавлен PDF листа первичного и повторного приёма с логотипом UMC

Изменены: `backend/app/consultation_pdf.py`, `backend/assets/README.md`, `backend/assets/fonts/Inter-Bold.ttf`, `backend/assets/fonts/Inter-Regular.ttf`, `backend/assets/fonts/OFL.txt`, `backend/assets/umc-horizontal.png`, `backend/requirements.lock`, `backend/requirements.txt`, `backend/tests/test_consultation_pdf.py`.

## 2026-09-30 14:00:33 +05:00 — Добавлены общий поиск пациентов, этапы приёма и серверное окно записи 15 минут

Изменены: `backend/app/db.py`, `backend/app/history.py`, `backend/app/lifecycle.py`, `backend/app/main.py`, `backend/app/schemas.py`, `backend/app/worker.py`, `backend/migrations/versions/0002_shared_patients_visit_lifecycle.py`, `backend/tests/audio_fixture.py`, `backend/tests/test_consultation_pipeline.py`, `backend/tests/test_migration.py`, `backend/tests/test_openai_asr.py`, `backend/tests/test_visit_lifecycle.py`, `backend/tests/test_workflow.py`.

## 2026-09-30 14:01:35 +05:00 — Оформлен Smart Consult в палитре UMC, добавлены общий поиск и мои приёмы

Изменены: `frontend/index.html`, `frontend/src/App.vue`, `frontend/src/Auth.vue`, `frontend/src/Settings.vue`, `frontend/src/UmcLogo.vue`, `frontend/src/assets/fonts/Inter-Variable.ttf`, `frontend/src/assets/fonts/OFL.txt`, `frontend/src/style.css`.

## 2026-09-30 14:03:50 +05:00 — Добавлены таймер, пауза, запись в разговоре и проверка листа консультации

Изменены: `frontend/src/App.vue`, `frontend/src/ClinicalField.vue`, `frontend/src/Consultation.vue`, `frontend/src/consultation.css`, `frontend/src/visit.js`, `frontend/tests/visit.test.js`.

## 2026-09-30 14:06:50 +05:00 — Стабилизирован документ МИС для безопасной повторной отправки

Изменены: `backend/app/main.py`, `backend/tests/test_mis_client.py`, `backend/tests/test_visit_lifecycle.py`, `backend/tests/test_workflow.py`, `examples/mis_client.py`.

## 2026-09-30 14:06:59 +05:00 — Добавлена резервная копия medhub перед серверными миграциями

Изменены: `deploy/backup-database.py`, `deploy/update-service-release.sh`.

## 2026-09-30 14:10:01 +05:00 — Описаны Smart Consult и результаты проверки всех 18 требований

Изменены: `README.md`, `docs/acceptance-2026-09-30-smart-consult.md`.

## 2026-09-30 14:14:15 +05:00 — Зафиксирована публикация Smart Consult и исправлен список файлов changelog

Изменены: `docs/acceptance-2026-09-30-smart-consult.md`, `scripts/commit.py`.

## 2026-09-30 14:33:01 +05:00 — Добавлены зашифрованный архив согласий и бланк пациента

Изменены: `backend/app/consent_document.py`, `backend/app/db.py`, `backend/migrations/versions/0003_patient_consent_signatures.py`.

## 2026-09-30 14:33:28 +05:00 — Реализованы проверка ЭЦП пациента через SIGEX и отзыв согласия

Изменены: `.env.example`, `backend/app/config.py`, `backend/app/consent.py`, `backend/app/consent_sigex.py`, `backend/app/main.py`, `backend/app/worker.py`, `backend/tests/conftest.py`, `backend/tests/test_consent_policy.py`, `backend/tests/test_patient_consents.py`.

## 2026-09-30 14:33:33 +05:00 — Добавлены подписание согласия по QR и NCALayer и статус ЭЦП в карте

Изменены: `frontend/src/App.vue`, `frontend/src/ConsentSigning.vue`, `frontend/src/eds.js`, `frontend/tests/eds.test.js`.

## 2026-09-30 14:35:49 +05:00 — Описаны процедура согласия пациента и результаты проверок ЭЦП

Изменены: `README.md`, `docs/sigex-patient-consent.md`, `docs/verification-2026-09-30-consent.md`.

## 2026-09-30 14:38:39 +05:00 — Зафиксирована проверка серверного релиза согласий пациента

Изменены: `docs/verification-2026-09-30-consent.md`.

## 2026-09-30 14:40:40 +05:00 — Закреплены обязательные коммит, push и деплой после каждой задачи

Изменены: `AGENTS.md`.

## 2026-09-30 14:55:18 +05:00 — Разделены ИИ-подсказки и результат врача, сохранены заполненные поля при анализе

Изменены: `backend/app/clinical.py`, `backend/app/consultation_pdf.py`, `backend/app/main.py`, `backend/app/providers.py`, `backend/app/schemas.py`, `backend/app/worker.py`, `backend/tests/test_clinical_merge.py`, `backend/tests/test_consultation_pdf.py`, `backend/tests/test_consultation_pipeline.py`, `backend/tests/test_openai_llm.py`, `backend/tests/test_providers.py`, `backend/tests/test_workflow.py`.

## 2026-09-30 14:55:29 +05:00 — Развёрнута форма приёма на всю ширину, итог перенесён вниз, расшифровка свёрнута

Изменены: `README.md`, `docs/verification-2026-09-30-ai-advisory.md`, `frontend/src/Consultation.vue`, `frontend/src/consultation.css`.

## 2026-09-30 15:12:31 +05:00 — Добавлены нормализация реквизитов и повторный анализ с учётом правок врача

Изменены: `backend/app/clinical.py`, `backend/app/main.py`, `backend/app/patient_input.py`, `backend/app/privacy.py`, `backend/app/providers.py`, `backend/app/schemas.py`, `backend/app/worker.py`, `backend/tests/test_clinical_merge.py`, `backend/tests/test_consultation_pipeline.py`, `backend/tests/test_patient_input.py`, `backend/tests/test_regeneration.py`, `backend/tests/test_workflow.py`.

## 2026-09-30 15:12:45 +05:00 — Добавлены маски реквизитов, дата из ИИН и кнопки перегенерации ответов

Изменены: `README.md`, `docs/regeneration-and-patient-input.md`, `frontend/src/App.vue`, `frontend/src/Auth.vue`, `frontend/src/ClinicalField.vue`, `frontend/src/Consultation.vue`, `frontend/src/MaskedInput.vue`, `frontend/src/patient-input.js`, `frontend/tests/patient-input.test.js`.

## 2026-09-30 15:36:43 +05:00 — Добавлена порционная обработка речи с архивом и восстановлением очереди

Изменены: `backend/app/live.py`, `backend/app/main.py`, `backend/app/schemas.py`, `backend/app/worker.py`, `backend/tests/test_live.py`.

## 2026-09-30 15:36:54 +05:00 — Добавлены непрерывные аудиофрагменты и звуковые сигналы записи

Изменены: `frontend/src/audio-chunks.js`, `frontend/src/live-upload.js`, `frontend/src/pcm-worklet.js`, `frontend/src/recorder.js`, `frontend/src/recording-sounds.js`, `frontend/tests/live.test.js`, `frontend/tests/recorder.test.js`.

## 2026-09-30 15:36:59 +05:00 — Подключена расшифровка во время приёма и итоговая сборка диалога

Изменены: `README.md`, `docs/live-recording.md`, `frontend/src/Consultation.vue`.

## 2026-09-30 15:44:12 +05:00 — Выбрана облегчённая LLM gpt-5.6-terra после сравнения качества

Изменены: `.env.example`, `README.md`, `docs/llm-selection.md`.

## 2026-09-30 16:02:55 +05:00 — Подписание XML при входе и восстановление проверки согласия SIGEX

Изменены: `backend/app/consent.py`, `backend/app/consent_sigex.py`, `backend/app/identity.py`, `backend/app/identity_xml.py`, `backend/app/signing_tasks.py`, `backend/requirements.lock`, `backend/requirements.txt`, `backend/tests/test_identity.py`, `backend/tests/test_identity_xml.py`, `backend/tests/test_patient_consents.py`, `docs/sigex-patient-consent.md`.

## 2026-09-30 16:03:22 +05:00 — Единые способы подписи QR, NCALayer и eGov Mobile с обновлением статуса

Изменены: `frontend/src/Auth.vue`, `frontend/src/ConsentSigning.vue`, `frontend/src/IdentitySigning.vue`, `frontend/src/Settings.vue`, `frontend/src/SigningMethods.vue`, `frontend/src/eds.js`, `frontend/tests/eds.test.js`.

## 2026-09-30 16:10:41 +05:00 — README по шаблону и напоминание о конфиденциальности перед записью

Изменены: `README.md`, `frontend/src/Consultation.vue`, `frontend/src/consultation.css`.

## 2026-09-30 16:21:50 +05:00 — Подготовлен Windows GPU-узел ASR и экспериментальный профиль LM Studio с проверкой качества

Изменены: `.env.example`, `.gitignore`, `backend/app/config.py`, `backend/app/providers.py`, `backend/tests/test_asr_runtime_profile.py`, `backend/tests/test_providers.py`, `deploy/gpu/README.md`, `deploy/gpu/windows/WINDOWS.md`, `deploy/gpu/windows/check-runtime.py`, `deploy/gpu/windows/requirements.lock`, `deploy/gpu/windows/start-asr.ps1`, `deploy/gpu/windows/start-llm.ps1`, `scripts/download_models.py`, `scripts/evaluate_local_models.py`.
## 2026-09-30 16:23:36 +05:00 — Обязательная ЭЦП согласия пациента и компактные кнопки записи

Изменены: `.env.example`, `README.md`, `backend/app/config.py`, `backend/tests/test_patient_consents.py`, `docs/sigex-patient-consent.md`, `frontend/src/App.vue`, `frontend/src/Consultation.vue`, `frontend/src/consultation.css`, `frontend/src/visit.js`, `frontend/tests/visit.test.js`.

## 2026-09-30 16:29:45 +05:00 — Необязательный телефон пациента и врача с проверкой неверного номера

Изменены: `backend/app/main.py`, `backend/app/patient_input.py`, `backend/app/schemas.py`, `backend/tests/test_patient_input.py`, `frontend/src/App.vue`, `frontend/src/Auth.vue`, `frontend/src/MaskedInput.vue`, `frontend/src/patient-input.js`, `frontend/tests/patient-input.test.js`.

## 2026-09-30 16:32:46 +05:00 — Добавлены участники команды и их профессиональный опыт в README

Изменены: `README.md`.

## 2026-09-30 16:46:00 +05:00 — Исправлена повторная проверка подписи SIGEX и ошибки входа

Изменены: `backend/app/consent_sigex.py`, `backend/app/identity.py`, `backend/app/identity_xml.py`, `backend/tests/test_identity.py`, `backend/tests/test_patient_consents.py`.

## 2026-09-30 16:46:06 +05:00 — Исправлены подключение NCALayer и отображение проверки ЭЦП

Изменены: `frontend/src/IdentitySigning.vue`, `frontend/src/eds.js`, `frontend/tests/eds.test.js`.

## 2026-09-30 16:50:54 +05:00 — Разделены способы подписания для ПК и мобильных устройств

Изменены: `frontend/src/ConsentSigning.vue`, `frontend/src/IdentitySigning.vue`, `frontend/src/SigningMethods.vue`, `frontend/src/signingDevice.js`.

## 2026-09-30 17:02:13 +05:00 — Перенос ветки dev MedHub в main Haqaton-017 с сохранением истории

Изменены: `.env.example`, `.gitignore`, `AGENTS.md`, `Logo UMC/foxy-CqOSG2V6.svg`, `Logo UMC/logo.png`, `Logo UMC/logo_head.png`, `Logo UMC/original_umc_logos/Logo 1.png`, `Logo UMC/original_umc_logos/Logo 2.png`, `Logo UMC/original_umc_logos/favicon.ico`, `README.md`, `backend/.dockerignore`, `backend/Dockerfile`, `backend/alembic.ini`, `backend/app/__init__.py`, `backend/app/asr_service.py`, `backend/app/audio_privacy.py`, `backend/app/catalogs/README.md`, `backend/app/catalogs/icd10.ru.json`, `backend/app/clinical.py`, `backend/app/config.py`, `backend/app/consent.py`, `backend/app/consent_document.py`, `backend/app/consent_sigex.py`, `backend/app/consultation_pdf.py`, `backend/app/db.py`, `backend/app/diagnoses.py`, `backend/app/history.py`, `backend/app/identity.py`, `backend/app/identity_xml.py`, `backend/app/lifecycle.py`, `backend/app/live.py`, `backend/app/main.py`, `backend/app/openai_asr.py`, `backend/app/openai_llm.py`, `backend/app/patient_input.py`, `backend/app/privacy.py`, `backend/app/providers.py`, `backend/app/schemas.py`, `backend/app/security.py`, `backend/app/seed_demo.py`, `backend/app/signing_tasks.py`, `backend/app/worker.py`, `backend/assets/README.md`, `backend/assets/fonts/Inter-Bold.ttf`, `backend/assets/fonts/Inter-Regular.ttf`, `backend/assets/fonts/OFL.txt`, `backend/assets/umc-horizontal.png`, `backend/migrations/env.py`, `backend/migrations/versions/0001_initial.py`, `backend/migrations/versions/0002_shared_patients_visit_lifecycle.py`, `backend/migrations/versions/0003_patient_consent_signatures.py`, `backend/pytest.ini`, `backend/requirements.lock`, `backend/requirements.txt`, `backend/tests/audio_fixture.py`, `backend/tests/conftest.py`, `backend/tests/test_asr_runtime_profile.py`, `backend/tests/test_audio_privacy.py`, `backend/tests/test_clinical_merge.py`, `backend/tests/test_consent_policy.py`, `backend/tests/test_consultation_pdf.py`, `backend/tests/test_consultation_pipeline.py`, `backend/tests/test_diagnoses.py`, `backend/tests/test_identity.py`, `backend/tests/test_identity_xml.py`, `backend/tests/test_live.py`, `backend/tests/test_migration.py`, `backend/tests/test_mis_client.py`, `backend/tests/test_openai_asr.py`, `backend/tests/test_openai_llm.py`, `backend/tests/test_patient_consents.py`, `backend/tests/test_patient_input.py`, `backend/tests/test_providers.py`, `backend/tests/test_regeneration.py`, `backend/tests/test_visit_lifecycle.py`, `backend/tests/test_workflow.py`, `compose.yaml`, `deploy/activate-service-site.sh`, `deploy/backup-database.py`, `deploy/bootstrap.sh`, `deploy/compose.production.yaml`, `deploy/gpu/.env.example`, `deploy/gpu/Dockerfile`, `deploy/gpu/README.md`, `deploy/gpu/compose.yaml`, `deploy/gpu/windows/WINDOWS.md`, `deploy/gpu/windows/check-runtime.py`, `deploy/gpu/windows/requirements.lock`, `deploy/gpu/windows/start-asr.ps1`, `deploy/gpu/windows/start-llm.ps1`, `deploy/install-docker.sh`, `deploy/install-service-release.sh`, `deploy/nginx/medhub.http.conf`, `deploy/nginx/medhub.https.conf`, `deploy/placeholder.html`, `deploy/systemd/medhub-api.service`, `deploy/systemd/medhub-worker.service`, `deploy/update-service-release.sh`, `docs/acceptance-2026-09-30-smart-consult.md`, `docs/live-recording.md`, `docs/llm-selection.md`, `docs/regeneration-and-patient-input.md`, `docs/sigex-patient-consent.md`, `docs/verification-2026-09-30-ai-advisory.md`, `docs/verification-2026-09-30-consent.md`, `docs/verification-2026-09-30.md`, `examples/mis_client.py`, `examples/mock_mis.py`, `frontend/.dockerignore`, `frontend/Dockerfile`, `frontend/index.html`, `frontend/nginx.conf`, `frontend/package.json`, `frontend/pnpm-lock.yaml`, `frontend/pnpm-workspace.yaml`, `frontend/public/favicon.ico`, `frontend/src/App.vue`, `frontend/src/Auth.vue`, `frontend/src/ClinicalField.vue`, `frontend/src/ConsentSigning.vue`, `frontend/src/Consultation.vue`, `frontend/src/DiagnosisPicker.vue`, `frontend/src/IdentitySigning.vue`, `frontend/src/MaskedInput.vue`, `frontend/src/Settings.vue`, `frontend/src/SigningMethods.vue`, `frontend/src/UmcLogo.vue`, `frontend/src/api.js`, `frontend/src/assets/fonts/Inter-Variable.ttf`, `frontend/src/assets/fonts/OFL.txt`, `frontend/src/assets/umc-horizontal.png`, `frontend/src/assets/umc-stacked.png`, `frontend/src/audio-chunks.js`, `frontend/src/consultation.css`, `frontend/src/eds.js`, `frontend/src/live-upload.js`, `frontend/src/main.js`, `frontend/src/patient-input.js`, `frontend/src/pcm-worklet.js`, `frontend/src/recorder.js`, `frontend/src/recording-sounds.js`, `frontend/src/signingDevice.js`, `frontend/src/style.css`, `frontend/src/visit.js`, `frontend/tests/eds.test.js`, `frontend/tests/live.test.js`, `frontend/tests/patient-input.test.js`, `frontend/tests/recorder.test.js`, `frontend/tests/visit.test.js`, `frontend/vite.config.js`, `scripts/commit.py`, `scripts/dev.ps1`, `scripts/download_models.py`, `scripts/evaluate_local_models.py`, `scripts/setup.ps1`.

## 2026-09-30 17:05:03 +05:00 — Актуализирован README хакатона: запуск, архитектура, ЭЦП и проверка локальных моделей

Изменены: `README.md`.

## 2026-09-30 17:06:30 +05:00 — Перенесена проверка ЭЦП на локальный модуль НУЦ с OCSP

Изменены: `.env.example`, `.gitignore`, `README.md`, `backend/Dockerfile`, `backend/app/config.py`, `backend/app/consent_sigex.py`, `backend/app/eds_local.py`, `backend/app/identity_xml.py`, `backend/tests/test_eds_local.py`, `backend/tests/test_identity_xml.py`, `backend/tests/test_patient_consents.py`, `backend/verifier/README.md`, `backend/verifier/Verifier.java`, `backend/verifier/VerifierChecks.java`, `backend/verifier/dependencies.sha256`, `backend/verifier/trust/nca_gost_2022.cer`, `backend/verifier/trust/nca_rsa_2022.cer`, `backend/verifier/trust/root_gost_2022.cer`, `backend/verifier/trust/root_rsa_2020.cer`, `deploy/update-service-release.sh`.

## 2026-09-30 17:13:54 +05:00 — Синхронизированы main и dev хакатона с актуальной dev MedHub

Изменены: `.env.example`, `.gitignore`, `README.md`, `backend/Dockerfile`, `backend/app/config.py`, `backend/app/consent_sigex.py`, `backend/app/eds_local.py`, `backend/app/identity_xml.py`, `backend/tests/test_eds_local.py`, `backend/tests/test_identity_xml.py`, `backend/tests/test_patient_consents.py`, `backend/verifier/README.md`, `backend/verifier/Verifier.java`, `backend/verifier/VerifierChecks.java`, `backend/verifier/dependencies.sha256`, `backend/verifier/trust/nca_gost_2022.cer`, `backend/verifier/trust/nca_rsa_2022.cer`, `backend/verifier/trust/root_gost_2022.cer`, `backend/verifier/trust/root_rsa_2020.cer`, `deploy/update-service-release.sh`.

## 2026-09-30 17:16:24 +05:00 — Добавлен код приглашения и порядок регистрации в публичном демо

Изменены: `README.md`.
