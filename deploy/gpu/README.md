# ПК с видеокартой: распознавание и генерация для Anamio

Для запуска непосредственно в Windows с LM Studio см. [инструкцию Windows](windows/WINDOWS.md). Ниже описан вариант с Docker и Ollama.

Браузер врача записывает звук и отправляет его backend Anamio. Backend обращается к вашему ASR по `/transcribe`; ASR возвращает реплики `SPEAKER_00…02` с таймкодами. Отдельным действием Ollama заполняет лист и определяет роли. После редактирования врач подтверждает лист для МИС.

Приведённый комплект рассчитан на **NVIDIA GPU**. Для AMD нужен другой образ и отдельная проверка. Конкретный объём видеопамяти и скорость нужно проверить на вашей карте: Whisper, pyannote и Qwen конкурируют за память. Начните с последовательных коротких записей; при нехватке памяти уменьшите LLM или переведите ASR в `int8_float16`.

## 1. Подготовить GPU-ПК

На Windows установите актуальный драйвер NVIDIA, обновите WSL командой `wsl --update`, установите Docker Desktop и включите WSL 2 backend. GPU в Docker Desktop для Windows поддерживается через WSL 2: [документация Docker](https://docs.docker.com/desktop/features/gpu/).

На Linux установите драйвер NVIDIA, Docker Engine / Compose и [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html), затем настройте runtime:

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Проверьте доступ из контейнера:

```powershell
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04 nvidia-smi
```

Используйте Docker Compose с поддержкой `gpus: all` (2.30+). На GPU-ПК скопируйте текущую рабочую папку medhub; исходники доступны в ветке `dev` репозитория `MG-Trener/medhub`.

## 2. Скачать модели один раз

Установите Python 3.12, создайте окружение для скачивания. Загрузка весов выполняется только на GPU-ПК:

```powershell
py -3.12 -m venv .download-venv
.\.download-venv\Scripts\python.exe -m pip install huggingface_hub
```

Войдите в Hugging Face, примите условия [Community-1](https://huggingface.co/pyannote/speaker-diarization-community-1) и создайте токен с доступом к этой модели. Введите его через переменную окружения `HF_TOKEN` в своей консоли; не сохраняйте токен в репозиторий. Затем:

```powershell
.\.download-venv\Scripts\python.exe scripts/download_models.py --directory D:/AI/models
Remove-Item Env:HF_TOKEN
```

Будут созданы каталоги:

```text
D:/AI/models/faster-whisper-large-v3/
D:/AI/models/pyannote-speaker-diarization-community-1/
```

`snapshot_download` сохраняет все файлы репозитория. Community-1 поддерживает загрузку pipeline из локального каталога. Перед реальным приёмом обязательно проверьте запуск с отключённым интернетом. Официальные описания: [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [Community-1 offline](https://huggingface.co/pyannote/speaker-diarization-community-1#offline-use).

## 3. Запустить два сервиса

В папке `deploy/gpu` скопируйте `.env.example` в `.env`. Укажите `MODELS_PATH=D:/AI/models`. Замените `ASR_SERVICE_TOKEN` случайным секретом длиной не менее 32 символов. Такой же секрет позднее понадобится backend Anamio.

`BIND_IP` сначала оставьте `127.0.0.1` для проверки на самом GPU-ПК.

```powershell
cd deploy/gpu
docker compose --env-file .env build asr
docker compose --env-file .env up -d
docker compose exec ollama ollama pull qwen3:8b
docker compose exec ollama ollama list
curl.exe http://127.0.0.1:8090/health
```

Первый build скачивает CUDA/PyTorch и может занять длительное время. Первый ASR-запрос загружает веса в память. `/health` проверяет только доступность процесса, а не качество распознавания.

Проверка с коротким **синтетическим** разговором (токен берётся из переменной окружения):

```powershell
curl.exe -H "Authorization: Bearer $env:ASR_SERVICE_TOKEN" -F "file=@test.wav" http://127.0.0.1:8090/transcribe
```

Ожидается JSON `{"segments":[{"speaker":"SPEAKER_00","start":0.0,"end":2.4,"text":"..."}]}`. Для LLM используется `/api/chat` Ollama. [Ollama в Docker](https://docs.ollama.com/docker).

## 4. Соединить компьютеры

Для доступа через интернет используйте закрытую VPN между backend Anamio и GPU-ПК, например существующую WireGuard-сеть. Не публикуйте Ollama напрямую в интернет: у используемого здесь стандартного API нет нашего Bearer-контроля.

В `deploy/gpu/.env` задайте `BIND_IP=<VPN-IP GPU-ПК>` и перезапустите `docker compose up -d`. Межсетевой экран GPU-ПК должен разрешать TCP 8090 и 11434 только с VPN-IP компьютера/backend Anamio. Это же относится к локальной сети. Проверьте, что оба адреса доступны **из контейнера worker**, а не только из браузера.

На компьютере, где запущен Anamio, укажите в корневом `.env`:

```dotenv
ASR_PROVIDER=self_hosted
ASR_URL=http://<VPN-IP-GPU-ПК>:8090
ASR_API_KEY=<тот же ASR_SERVICE_TOKEN>
INSTALL_LOCAL_AI=false
LLM_PROVIDER=ollama
LLM_URL=http://<VPN-IP-GPU-ПК>:11434
LLM_MODEL=qwen3:8b
LLM_IS_CLOUD=false
```

```powershell
docker compose up -d --force-recreate api worker
```

HTTP в примере проходит внутри шифрованного VPN. При доступе вне VPN нужен HTTPS reverse proxy и ограничение доступа. ASR получает исходное аудио только как ваш доверенный сервер обработки; согласие на запись проверяется Anamio до отправки.

## 5. Проверить полный сценарий

Создайте вымышленного пациента с согласием → начните приём → разрешите микрофон → запишите короткий диалог двух/трёх говорящих → остановите → нажмите «Распознать запись» → проверьте реплики → «Заполнить лист с AI» → исправьте роли/поля → «Проверено врачом».

На этом компьютере без GPU тяжёлые модели не нужны. На VPS 77.37.65.54 тоже не потребуется GPU: после отдельного запроса на деплой туда переносятся UI/API/БД, а адреса моделей остаются адресами GPU-ПК.

Запуск реального GPU-конвейера пока не проверен: GPU-ПК и веса ещё не подключены. Тесты проекта проверяют контракт и контроль доступа с подменёнными ответами моделей, не точность русской/казахской речи.
