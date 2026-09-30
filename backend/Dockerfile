FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgomp1 default-jdk-headless && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.txt -c requirements.lock
ARG INSTALL_LOCAL_AI=false
RUN if [ "$INSTALL_LOCAL_AI" = "true" ]; then pip install --no-cache-dir 'faster-whisper>=1.1,<2' 'pyannote.audio>=4,<5'; fi
RUN useradd --create-home --uid 10001 medhub && mkdir -p /data/audio && chown -R medhub:medhub /data
COPY --chown=medhub:medhub . .
RUN test -f verifier/lib/knca_provider_jce_kalkan-0.7.5.jar && mkdir -p verifier/classes && javac --release 17 -encoding UTF-8 -cp 'verifier/lib/*' -d verifier/classes verifier/Verifier.java
USER medhub
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
