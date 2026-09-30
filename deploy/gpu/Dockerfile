FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv ffmpeg libgomp1 && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /venv
ENV PATH="/venv/bin:$PATH"
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir 'faster-whisper>=1.1,<2' 'pyannote.audio>=4,<5'
COPY backend/app ./app
RUN useradd --create-home --uid 10001 medhub
USER medhub
CMD ["uvicorn", "app.asr_service:app", "--host", "0.0.0.0", "--port", "8090", "--no-access-log"]
