"""STT: faster-whisper (pip) или whisper.cpp CLI."""

import json
import os
import shutil
import subprocess
import tempfile
import threading
import urllib.parse
import urllib.request
import wave

import config


class STTError(RuntimeError):
    pass


# ---------- yandex ----------

def _transcribe_yandex(pcm: bytes, sample_rate: int) -> str:
    if not config.YANDEX_API_KEY:
        raise STTError("нет YANDEX_API_KEY")
    params = urllib.parse.urlencode({
        "lang": config.WHISPER_LANG + "-RU" if config.WHISPER_LANG == "ru" else config.WHISPER_LANG,
        "format": "lpcm",
        "sampleRateHertz": str(sample_rate),
        "topic": config.YANDEX_STT_TOPIC,
    })
    req = urllib.request.Request(
        f"{config.YANDEX_STT_URL}?{params}", data=pcm,
        headers={"Authorization": f"Api-Key {config.YANDEX_API_KEY}"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise STTError(f"yandex stt: {exc}") from exc
    return (data.get("result") or "").strip()


# ---------- vosk ----------

_vosk_model = None
_vosk_lock = threading.Lock()


def _vosk():
    global _vosk_model
    with _vosk_lock:
        if _vosk_model is None:
            try:
                from vosk import Model, SetLogLevel
            except ImportError as exc:
                raise STTError("vosk не установлен") from exc
            SetLogLevel(-1)
            if not os.path.isdir(config.VOSK_MODEL):
                raise STTError(f"нет модели vosk {config.VOSK_MODEL}")
            _vosk_model = Model(config.VOSK_MODEL)
        return _vosk_model


def _transcribe_vosk(pcm: bytes, sample_rate: int) -> str:
    import json as _json
    from vosk import KaldiRecognizer

    if sample_rate != 16000:
        raise STTError(f"vosk ждёт 16000 Гц, пришло {sample_rate}")
    rec = KaldiRecognizer(_vosk(), sample_rate)
    rec.AcceptWaveform(pcm)
    return _json.loads(rec.FinalResult()).get("text", "").strip()


# ---------- faster-whisper ----------

_fw_model = None
_fw_lock = threading.Lock()


def _fw():
    global _fw_model
    with _fw_lock:
        if _fw_model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise STTError("faster-whisper не установлен") from exc
            _fw_model = WhisperModel(
                config.FASTER_WHISPER_MODEL,
                device="cpu",
                compute_type=config.FASTER_WHISPER_COMPUTE,
                download_root=config.FASTER_WHISPER_DIR,
            )
        return _fw_model


def _transcribe_fw(pcm: bytes, sample_rate: int) -> str:
    import numpy as np

    audio = np.frombuffer(pcm, dtype=np.int16).astype("float32") / 32768.0
    segments, _ = _fw().transcribe(
        audio,
        language=config.WHISPER_LANG,
        beam_size=1,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    return " ".join(s.text.strip() for s in segments).strip()


# ---------- whisper.cpp ----------

def _find_bin() -> str:
    for name in (config.WHISPER_BIN, "whisper-cli", "main"):
        path = shutil.which(name) if not os.path.isabs(name) else name
        if path and os.path.exists(path):
            return path
    raise STTError(f"whisper.cpp не найден ({config.WHISPER_BIN}). Укажи WHISPER_BIN.")


def _transcribe_cpp(pcm: bytes, sample_rate: int) -> str:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav_path = f.name
    try:
        with wave.open(wav_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(pcm)

        if not os.path.exists(config.WHISPER_MODEL):
            raise STTError(f"нет модели {config.WHISPER_MODEL}")

        threads = config.WHISPER_THREADS or (os.cpu_count() or 4)
        cmd = [
            _find_bin(),
            "-m", config.WHISPER_MODEL,
            "-f", wav_path,
            "-l", config.WHISPER_LANG,
            "-t", str(threads),
            "-nt",
            "-np",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise STTError(proc.stderr.strip()[:300] or "whisper.cpp упал")
        return proc.stdout.strip()
    finally:
        os.unlink(wav_path)


def transcribe(pcm: bytes, sample_rate: int = 16000) -> str:
    """pcm: сырой s16le mono. Возвращает текст."""
    if config.STT_ENGINE == "yandex":
        return _transcribe_yandex(pcm, sample_rate)
    if config.STT_ENGINE == "vosk":
        return _transcribe_vosk(pcm, sample_rate)
    if config.STT_ENGINE == "whisper_cpp":
        return _transcribe_cpp(pcm, sample_rate)
    return _transcribe_fw(pcm, sample_rate)
