"""Конфиг сервера. Всё через переменные окружения / .env."""

import os


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


_load_dotenv()

# --- HTTP ---
HOST = _env("SERVER_HOST", "0.0.0.0")
PORT = _env_int("SERVER_PORT", 8080)
# Общий секрет: клиент шлёт его в X-Token. Пусто = без проверки.
TOKEN = _env("SERVER_TOKEN", "")

# --- LLM (OpenAI-совместимый, DeepSeek через неофф. провайдера) ---
LLM_BASE_URL = _env("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
LLM_API_KEY = _env("LLM_API_KEY", "")
LLM_MODEL = _env("LLM_MODEL", "deepseek-chat")
LLM_TIMEOUT = _env_int("LLM_TIMEOUT", 60)
LLM_MAX_TOKENS = _env_int("LLM_MAX_TOKENS", 200)
LLM_TEMPERATURE = float(_env("LLM_TEMPERATURE", "0.7"))
LLM_HISTORY_TURNS = _env_int("LLM_HISTORY_TURNS", 8)

SYSTEM_PROMPT = _env(
    "SYSTEM_PROMPT",
    "Ты — Маруся, голосовой помощник в умной колонке. "
    "Отвечай по-русски, коротко и по делу: 1-3 предложения, без списков, "
    "без markdown, без эмодзи — ответ читается вслух. "
    "Если не знаешь — скажи честно.",
)

# --- STT ---
# "faster_whisper" (в докере, pip) | "whisper_cpp" (CLI)
STT_ENGINE = _env("STT_ENGINE", "faster_whisper")
FASTER_WHISPER_MODEL = _env("FASTER_WHISPER_MODEL", "small")
FASTER_WHISPER_COMPUTE = _env("FASTER_WHISPER_COMPUTE", "int8")
FASTER_WHISPER_DIR = _env("FASTER_WHISPER_DIR", "/models/hf")

# whisper.cpp: путь к CLI и модели
WHISPER_BIN = _env("WHISPER_BIN", "whisper-cli")
WHISPER_MODEL = _env("WHISPER_MODEL", "/opt/models/ggml-small.bin")
WHISPER_LANG = _env("WHISPER_LANG", "ru")
WHISPER_THREADS = _env_int("WHISPER_THREADS", 0)  # 0 = все ядра

# --- TTS ---
# "piper" | "espeak"
TTS_ENGINE = _env("TTS_ENGINE", "piper")
PIPER_BIN = _env("PIPER_BIN", "piper")
PIPER_MODEL = _env("PIPER_MODEL", "/opt/models/ru_RU-irina-medium.onnx")
ESPEAK_BIN = _env("ESPEAK_BIN", "espeak-ng")
ESPEAK_VOICE = _env("ESPEAK_VOICE", "ru")
ESPEAK_SPEED = _env_int("ESPEAK_SPEED", 160)
