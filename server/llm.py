"""Клиент к OpenAI-совместимому API (DeepSeek через сторонний провайдер)."""

import json
import threading
import urllib.request

import config


class LLM:
    def __init__(self) -> None:
        self._history: list[dict] = []
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._history.clear()

    def chat(self, text: str) -> str:
        with self._lock:
            messages = [{"role": "system", "content": config.SYSTEM_PROMPT}]
            messages += self._history
            messages.append({"role": "user", "content": text})

            payload = {
                "model": config.LLM_MODEL,
                "messages": messages,
                "temperature": config.LLM_TEMPERATURE,
                "max_tokens": config.LLM_MAX_TOKENS,
                "stream": False,
            }
            req = urllib.request.Request(
                f"{config.LLM_BASE_URL}/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {config.LLM_API_KEY}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=config.LLM_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            answer = data["choices"][0]["message"]["content"].strip()

            self._history.append({"role": "user", "content": text})
            self._history.append({"role": "assistant", "content": answer})
            limit = max(0, config.LLM_HISTORY_TURNS) * 2
            if limit and len(self._history) > limit:
                del self._history[: len(self._history) - limit]

            return answer
