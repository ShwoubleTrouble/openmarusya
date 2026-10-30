"""HTTP-сервер: принимает аудио с колонки, отдаёт озвученный ответ.

Эндпоинты:
  GET  /health          -> {"ok": true}
  POST /v1/voice        -> тело: raw PCM s16le mono (16000 Гц), ответ: WAV
  POST /v1/text         -> JSON {"text": "..."}, ответ: WAV
  POST /v1/reset        -> очистить историю диалога

Заголовки ответа:
  X-Text-B64  — ответ ассистента в base64 (utf-8), для логов/отладки
"""

import base64
import json
import sys
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
import llm
import stt
import tts

_pipeline_lock = threading.Lock()
_llm = llm.LLM()

NOT_HEARD = "Не расслышала, повтори."
ERROR_PHRASE = "Что-то пошло не так, попробуй ещё раз."


def _pipeline(text: str) -> bytes:
    if not text:
        return tts.synthesize(NOT_HEARD)
    try:
        answer = _llm.chat(text)
    except Exception:
        traceback.print_exc(file=sys.stderr)
        answer = ERROR_PHRASE
    print(f"[user] {text}\n[bot]  {answer}", flush=True)
    return tts.synthesize(answer)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # тише в логах
        pass

    def _auth_ok(self) -> bool:
        if not config.TOKEN:
            return True
        return self.headers.get("X-Token", "") == config.TOKEN

    def _send(self, code: int, body: bytes, ctype: str = "application/octet-stream", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict):
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length) if length else b""

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/health":
            self._json(200, {"ok": True, "model": config.LLM_MODEL, "tts": config.TTS_ENGINE})
        elif path == "/client.py":
            # раздача клиента для обновления на колонке
            try:
                with open(config.CLIENT_PATH, "rb") as fh:
                    self._send(200, fh.read(), "text/x-python; charset=utf-8")
            except OSError:
                self._json(404, {"error": "client.py не найден", "path": config.CLIENT_PATH})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if not self._auth_ok():
            self._json(403, {"error": "bad token"})
            return

        try:
            if path == "/v1/reset":
                _llm.reset()
                self._json(200, {"ok": True})
                return

            if path == "/v1/voice":
                rate = int(self.headers.get("X-Rate", "16000"))
                pcm = self._read_body()
                if len(pcm) < rate * 2 * 0.2:  # < 200 мс
                    self._send(200, tts.synthesize(NOT_HEARD), "audio/wav")
                    return
                with _pipeline_lock:
                    text = stt.transcribe(pcm, rate)
                    wav = _pipeline(text)
                self._send(200, wav, "audio/wav",
                           {"X-Text-B64": base64.b64encode(text.encode()).decode()})

            elif path == "/v1/text":
                req = json.loads(self._read_body().decode("utf-8") or "{}")
                text = str(req.get("text", "")).strip()
                with _pipeline_lock:
                    wav = _pipeline(text)
                self._send(200, wav, "audio/wav")

            else:
                self._json(404, {"error": "not found"})

        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            try:
                self._json(500, {"error": str(exc)[:300]})
            except Exception:
                pass


def main() -> None:
    srv = ThreadingHTTPServer((config.HOST, config.PORT), Handler)
    srv.daemon_threads = True
    print(f"слушаю http://{config.HOST}:{config.PORT}  llm={config.LLM_MODEL} tts={config.TTS_ENGINE}",
          flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
