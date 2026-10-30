ARG PYTHON_IMAGE=python:3.11-slim
FROM ${PYTHON_IMAGE}

# espeak-ng — fallback TTS, если piper-голос не подключён
RUN apt-get update \
 && apt-get install -y --no-install-recommends espeak-ng \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir faster-whisper piper-tts

WORKDIR /app
COPY . .

ENV STT_ENGINE=faster_whisper \
    FASTER_WHISPER_MODEL=small \
    FASTER_WHISPER_COMPUTE=int8 \
    FASTER_WHISPER_DIR=/models/hf \
    TTS_ENGINE=piper \
    PIPER_BIN=piper \
    PIPER_MODEL=/models/ru_RU-irina-medium.onnx \
    SERVER_HOST=0.0.0.0 \
    SERVER_PORT=8080

EXPOSE 8080
CMD ["python", "-u", "server.py"]
