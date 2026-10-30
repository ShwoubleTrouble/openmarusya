"""TTS: piper или espeak-ng. Возвращает WAV (bytes)."""

import os
import shutil
import subprocess
import tempfile

import config


class TTSError(RuntimeError):
    pass


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
    if config.TTS_ENGINE == "espeak":
        return _espeak(text)
    try:
        return _piper(text)
    except TTSError:
        # мягкая деградация: если piper сломан, отвечаем espeak-ом
        return _espeak(text)
