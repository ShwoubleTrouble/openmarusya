ARG BASE_IMAGE=python:3.11-slim
FROM ${BASE_IMAGE}

# espeak-ng — fallback TTS, если piper-голос не подключён
RUN apt-get update \
 && apt-get install -y --no-install-recommends espeak-ng unzip \
 && rm -rf /var/lib/apt/lists/*

# vosk — Kaldi: работает на старых CPU без AVX/SSE4
RUN pip install --no-cache-dir vosk piper-tts

COPY . /app

ENV STT_ENGINE=vosk \
    VOSK_MODEL=/models/vosk-model-small-ru-0.22 \
    TTS_ENGINE=piper \
    PIPER_BIN=piper \
    PIPER_MODEL=/models/ru_RU-irina-medium.onnx \
    SERVER_HOST=0.0.0.0 \
    SERVER_PORT=8080

WORKDIR /app
EXPOSE 8080
CMD ["python", "-u", "server.py"]
