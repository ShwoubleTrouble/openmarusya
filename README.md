# openmarusya

Голосовой ИИ-помощник для **VK Капсула Мини** (Alpine 3.24, kernel 6.18, 210 МБ RAM).

Колонка — тонкий клиент: пишет звук с микрофона, шлёт на сервер, играет ответ.
Вся тяжёлая работа (STT, LLM, TTS) — на сервере по Wi-Fi.

```
[капсула] arecord → детект речи по RMS → POST /v1/voice (raw PCM 16k s16le)
                                                     ↓
[сервер]  whisper.cpp → DeepSeek (OpenAI-совместимый API) → piper/espeak
                                                     ↓
[капсула] aplay ←────────────── WAV (audio/wav) ──────┘
```

Почему не на колонке: 210 МБ RAM и 75 МБ свободного диска. `piper`+`onnxruntime`
требуют ~250 МБ RAM, `whisper base` — 142 МБ на диске. Не влезает.

## Стек

| Часть | Чем |
|---|---|
| STT | whisper.cpp (CLI), модель `ggml-small.bin`, `-l ru` |
| LLM | любой OpenAI-совместимый `/chat/completions` (DeepSeek через сторонний провайдер) |
| TTS | piper (голоса ru_RU) или espeak-ng как fallback |
| HTTP | stdlib `http.server`, без фреймворков и зависимостей |

Сервер вообще не требует pip-пакетов — только бинарники `whisper-cli`, `piper`, `espeak-ng`.

## Сервер (Docker, x86_64)

```sh
cd server
cp .env.example .env      # вписать LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
mkdir -p models
# голос piper (~60 МБ), кладём в models/
curl -LO --output-dir models https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx
curl -LO --output-dir models https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx.json
docker compose up -d --build
```

При первом запросе контейнер сам скачает модель whisper `small` (~460 МБ) в `models/hf`.

Проверка без колонки:

```sh
curl -s localhost:8080/health
curl -s --data '{"text":"привет"}' -H 'Content-Type: application/json' \
     localhost:8080/v1/text -o /tmp/a.wav
```

### Без Docker (whisper.cpp + espeak-ng)

```sh
apk add whisper-cpp espeak-ng python3
# в .env: STT_ENGINE=whisper_cpp, WHISPER_MODEL=/opt/models/ggml-small.bin, TTS_ENGINE=espeak
python3 server.py
```

Модель whisper.cpp: `ggml-small.bin` с https://huggingface.co/ggerganov/whisper.cpp

## Капсула

Нужен только `alsa-utils` и python3 (stdlib):

```sh
apk add alsa-utils python3
python3 client.py --server http://IP_СЕРВЕРА:8080 --token change-me
```

Часы на экране — отдельный процесс, раз в минуту вызывает `clockdisp HH:MM`:

```sh
python3 clock.py &
```

Полезные флаги: `--once` (одна фраза и выход), `--threshold` (порог RMS вручную),
`--silence-ms` (пауза, завершающая фразу), `--in-device/--out-device`.

При старте клиент калибрует шум: 30 кадров по 30 мс, порог = max(400, медиана×4).
Если ловит лишнее — подними `--threshold`; если не слышит — опусти.

## Протокол

| Метод | Путь | Тело | Ответ |
|---|---|---|---|
| GET | `/health` | — | JSON |
| POST | `/v1/voice` | raw PCM s16le mono, заголовок `X-Rate` | WAV + `X-Text-B64` (распознанный текст) |
| POST | `/v1/text` | `{"text": "..."}` | WAV |
| POST | `/v1/reset` | — | очистить историю диалога |

Авторизация: заголовок `X-Token`, если задан `SERVER_TOKEN`.

## Известные ограничения

- Нет wake-word: триггер по громкости, поэтому хлопок/шум может запустить запись.
  Следующий шаг — порог по громкости заменить на openWakeWord или кнопку (`touch-keys`).
- История диалога одна на всех (без разделения по устройству).
- `piper` под musl на сервере-альпине обычно не заводится колёсами pip —
  нужен либо Docker с glibc, либо сборка из исходников; до этого `TTS_ENGINE=espeak`.
