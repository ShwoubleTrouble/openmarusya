"""TTS: piper или espeak-ng. Возвращает WAV (bytes)."""

import array
import base64
import io
import json
import math
import os
import shutil
import subprocess
import tempfile
import urllib.request
import wave

import config


class TTSError(RuntimeError):
    pass


def _fix_tail(pcm: bytes, rate: int) -> bytes:
    """Gemini дописывает в конец всплеск шума после паузы.
    Если после тишины идёт звук — это артефакт, отрезаем его и гасим хвост."""
    a = array.array("h")
    a.frombytes(pcm)
    win = max(1, rate // 100)          # 10 мс
    if len(a) < win * 20:
        return pcm

    def rms(seg):
        return math.isqrt(sum(v * v for v in seg) // len(seg)) if seg else 0

    quiet = 100
    wins = [rms(a[i:i + win]) for i in range(0, len(a) - win + 1, win)]

    i = len(wins) - 1
    while i >= 0 and wins[i] < quiet:   # пропускаем тишину в самом конце
        i -= 1
    j = i
    while j >= 0 and wins[j] >= quiet:  # назад по последнему звучащему куску
        j -= 1

    if i < 0:
        return b"\x00" * (len(a) * 2)   # одна тишина
    cut = len(a)
    if i - j >= 8:                       # >=80 мс паузы перед этим куском → артефакт
        cut = (j + 1) * win
    del a[cut:]

    fade = min(len(a), rate // 50)       # 20 мс затухания
    for k in range(fade):
        a[len(a) - fade + k] = int(a[len(a) - fade + k] * (1 - k / fade))
    return a.tobytes()


def _gemini(text: str) -> bytes:
    """Нейронный голос Gemini TTS. Возвращает готовый WAV."""
    if not config.GEMINI_API_KEY:
        raise TTSError("нет GEMINI_API_KEY")
    payload = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": config.GEMINI_TTS_VOICE}}
            },
        },
    }
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{config.GEMINI_TTS_MODEL}:generateContent?key={config.GEMINI_API_KEY}")
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"https": config.GEMINI_PROXY, "http": config.GEMINI_PROXY})
        if config.GEMINI_PROXY else urllib.request.ProxyHandler({})
    )
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with opener.open(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise TTSError(f"gemini tts: {exc}") from exc

    try:
        part = data["candidates"][0]["content"]["parts"][0]["inlineData"]
        pcm = base64.b64decode(part["data"])
        mime = part.get("mimeType", "audio/L16;rate=24000")
        rate = 24000
        for chunk in mime.split(";"):
            chunk = chunk.strip()
            if chunk.startswith("rate="):
                rate = int(chunk[5:])
    except (KeyError, IndexError, TypeError) as exc:
        raise TTSError(f"gemini tts: нет аудио в ответе ({str(data)[:150]})") from exc

    # Gemini отдаёт сырой s16le mono — оборачиваем в WAV
    pcm = _fix_tail(pcm, rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _which(name: str) -> str:
    path = name if os.path.isabs(name) else shutil.which(name)
    if not path or not os.path.exists(path):
        raise TTSError(f"не найден бинарь {name}")
    return path


def _piper(text: str) -> bytes:
    if not os.path.exists(config.PIPER_MODEL):
        raise TTSError(f"нет модели {config.PIPER_MODEL}")
    cmd = [_which(config.PIPER_BIN), "-m", config.PIPER_MODEL, "--output_file", "-"]
    proc = subprocess.run(cmd, input=text.encode("utf-8"), capture_output=True, timeout=120)
    if proc.returncode != 0 or not proc.stdout:
        raise TTSError(proc.stderr.decode("utf-8", "replace")[:300] or "piper упал")
    return proc.stdout


def _espeak(text: str) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        out = f.name
    try:
        cmd = [
            _which(config.ESPEAK_BIN),
            "-v", config.ESPEAK_VOICE,
            "-s", str(config.ESPEAK_SPEED),
            "-w", out,
            text,
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=60)
        if proc.returncode != 0:
            raise TTSError(proc.stderr.decode("utf-8", "replace")[:300] or "espeak упал")
        with open(out, "rb") as fh:
            return fh.read()
    finally:
        os.unlink(out)


def synthesize(text: str) -> bytes:
    text = text.strip()
    if not text:
        raise TTSError("пустой текст")
    if config.TTS_ENGINE == "gemini":
        try:
            return _gemini(text)
        except TTSError:
            # мягкая деградация: без сети всё равно ответим голосом
            return _espeak(text)
    if config.TTS_ENGINE == "espeak":
        return _espeak(text)
    try:
        return _piper(text)
    except TTSError:
        # мягкая деградация: если piper сломан, отвечаем espeak-ом
        return _espeak(text)
