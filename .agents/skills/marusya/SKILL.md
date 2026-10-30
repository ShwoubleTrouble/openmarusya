---
name: marusya
description: Работа над openmarusya — голосовым помощником для VK Капсула Мини. Используй при любых задачах по этому репозиторию: клиент на капсуле, сервер (STT/LLM/TTS), деплой, отладка вейкворда, экран/часы, кнопки, громкость, LED-кольцо.
---

# openmarusya

Голосовой ассистент «Маруся»: VK Капсула Мини (Alpine, ARM, 210 МБ RAM) — тонкий клиент,
x86_64-сервер — вся обработка.

## Топология

| что | где |
|---|---|
| репозиторий (эта машина) | `/home/shwouble/openmarusya`, ветка `main`, remote `git@github.com:ShwoubleTrouble/openmarusya.git` |
| сервер | `ssh -p 8581 192.168.1.172`, код в `~/openmarusya`, Docker-контейнер `marusya`, порт **8181** |
| капсула | SSH с этой машины нет; команды выполняет пользователь и присылает вывод |

Сервер отдаёт файлы клиента: `GET /client.py`, `/clock.py`, `/volume.py`, `/wake.py`
(из каталога `CLIENT_PATH`), плюс `/health`, `POST /v1/voice`, `/v1/text`, `/v1/reset`.
Авторизация — заголовок `X-Token` (`SERVER_TOKEN` в `server/.env`).

## Деплой (после правок)

```sh
scp -q -P 8581 server/*.py 192.168.1.172:~/openmarusya/server/
scp -q -P 8581 capsule/*.py 192.168.1.172:~/openmarusya/capsule/
ssh -p 8581 192.168.1.172 "cd ~/openmarusya/server && docker compose up -d --build"
```

Проверка без колонки (тестовые PCM лежат в `server/models/*.pcm`):

```sh
T=$(ssh -p 8581 192.168.1.172 "grep ^SERVER_TOKEN= ~/openmarusya/server/.env|cut -d= -f2")
wget -qO /tmp/o.wav --header='X-Rate: 16000' --header="X-Token: $T" \
     --post-file=server/models/z.pcm http://192.168.1.172:8181/v1/voice   # z.pcm → wake
```

**Каждую правку — коммит и push в `main`.** Серверный каталог git не содержит
(пустой `.git`, без remote) — источник истины только репозиторий.

## Файлы

- `capsule/client.py` — захват `arecord` (hw:0,1, S32/48k/4ch), VAD по RMS, даунмикс +
  антиалиасинг 48k→16k, LED-кольцо, кнопка 582 (`KEY_VOICECOMMAND`) останавливает речь.
- `capsule/clock.py` — часы: раз в минуту `clockdisp HH:MM`.
- `capsule/volume.py` — громкость кнопками 114/115, Master+Playback вместе, щелчок 700 Гц.
- `capsule/wake.py` — локальный вейкворд (не используется: на капсуле нет места).
- `server/server.py` — HTTP, `_wake_split` (поиск «маруся» по всей фразе, склейка «мару ся»),
  ранние заголовки `X-Wake`, 204 для чужих фраз, follow-up 5 с.
- `server/stt.py` — yandex / vosk / whisper_cpp, `wake_text()` (только для vosk).
- `server/tts.py` — yandex / gemini / piper / espeak.
- `server/wake.py` — openWakeWord на сервере (выключено, см. ограничения).

## Факты и грабли

- Сервер: Athlon II X2 250, **без SSE4.1/AVX**. `onnxruntime` (а значит piper, openWakeWord,
  tflite) падает с SIGILL (`exit 132`). Vosk работает.
- Капсула: места ~75 МБ, после `apk add py3-onnxruntime py3-numpy` осталось 12 КБ —
  новые пакеты на капсулу не ставить.
- STT сейчас — Yandex, поэтому `stt.wake_text()` (грамматика Vosk) мёртв, не тратить время.
- Yandex на фоне разговоров слышит «Маруся» как «Бабася»/«Ксюша».
- Микрофон капсулы: `Mic Boost` = 108 (сбрасывается при ребуте), пики речи ~300–400 RMS.
- Громкость: `Master` 0–100 (сырые), `Playback` 0–251. 75% на ползунках UI = **89/236**;
  `amixer ... 75%` ставит 75/188 и звучит тише.
- `aplay` запускать с `-B 150000` (короткий буфер) — иначе речь не обрывается сразу.
- `ringled`: флаги до команды, `ringled off` / `--breathe 4000 --duration 86400000 -b 40 all FFFFFF`.
- Автозапуск на капсуле: `/etc/local.d/marusya.start` (amixer + clock.py + volume.py + client.py).
- Ключи/токены — только в `server/.env` (в git не попадает). Yandex TTS: speakit … голос
  `alena`, `good`, 48 кГц; DeepSeek через `https://freecodex.fun/v1`.

## Проверки перед ответом

1. `python3 -c "import ast; ast.parse(open('файл').read())"` для всех правленых файлов.
2. Модульные проверки `_wake_split` на строках «Маруся», «Мару ся, включи свет», «мусор».
3. После деплоя — запрос `models/z.pcm` (должен быть wake) и `models/y.pcm` (204).
