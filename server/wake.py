"""Детектор вейкворда на сервере: openWakeWord через onnxruntime.

Считается по уже присланному PCM, до Yandex STT, — фоновая болтовня
отсекается, не доходя до распознавания.
"""

from __future__ import annotations

import threading

import config

np = None

# Внимание: onnxruntime импортируется только внутри Detector и только если
# задана модель. На CPU без SSE4.1 (Athlon II) он падает с SIGILL, поэтому
# импорт на уровне модуля недопустим.

SR = 16000
CHUNK = 1280            # 80 мс — шаг классификатора
CONTEXT = 1760          # 80 мс + 30 мс контекста для мел-спектра
MEL_FRAMES = 76         # окно эмбеддинга (0.76 с)
EMB_FRAMES = 16         # окно классификатора (1.3 с)


class Detector:
    def __init__(self, model_path: str, mel_path: str, emb_path: str,
                 threshold: float = 0.5):
        global np
        import numpy
        import onnxruntime as ort
        np = numpy
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self.mel = ort.InferenceSession(mel_path, sess_options=opts,
                                        providers=["CPUExecutionProvider"])
        self.emb = ort.InferenceSession(emb_path, sess_options=opts,
                                        providers=["CPUExecutionProvider"])
        self.cls = ort.InferenceSession(model_path, sess_options=opts,
                                        providers=["CPUExecutionProvider"])
        self.cls_input = self.cls.get_inputs()[0].name
        self.threshold = threshold

    def _mel(self, samples: np.ndarray) -> np.ndarray:
        out = self.mel.run(None, {"input": samples.astype(np.float32)[None, :]})[0]
        return np.squeeze(out) / 10.0 + 2.0

    def _embedding(self, frames: np.ndarray) -> np.ndarray:
        x = frames.astype(np.float32)[None, :, :, None]
        return self.emb.run(None, {"input_1": x})[0].reshape(-1)

    def _score(self, feats: np.ndarray) -> float:
        x = feats[None, :, :].astype(np.float32)
        return float(np.asarray(self.cls.run(None, {self.cls_input: x})[0]).reshape(-1)[0])

    def score(self, pcm: bytes, rate: int = SR) -> float:
        """Максимальный скор по всей записи."""
        x = np.frombuffer(pcm, dtype=np.int16)
        if rate != SR:
            n = int(len(x) * SR / rate)
            x = np.interp(np.linspace(0, len(x) - 1, n),
                          np.arange(len(x)), x).astype(np.int16)
        raw = np.zeros(0, dtype=np.int16)
        mels = np.ones((MEL_FRAMES, 32), dtype=np.float32)
        feats = np.ones((EMB_FRAMES, 96), dtype=np.float32)
        mel_count = emb_count = 0
        best = 0.0
        for i in range(0, len(x) - CHUNK + 1, CHUNK):
            raw = np.concatenate((raw, x[i:i + CHUNK]))[-CONTEXT:]
            frames = self._mel(raw)
            if frames.ndim == 1:
                frames = frames[None, :]
            mels = np.vstack((mels, frames))[-MEL_FRAMES:, :]
            mel_count += len(frames)
            if mel_count < MEL_FRAMES:
                continue
            feats = np.vstack((feats, self._embedding(mels)))[-EMB_FRAMES:, :]
            emb_count += 1
            if emb_count < EMB_FRAMES:
                continue
            best = max(best, self._score(feats))
        return best


_detector: Detector | None = None
_lock = threading.Lock()
_failed = False


def get() -> Detector | None:
    """Детектор или None, если модель не настроена/не загрузилась."""
    global _detector, _failed
    if not config.WAKE_ONNX_MODEL or _failed:
        return None
    with _lock:
        if _detector is None:
            try:
                _detector = Detector(config.WAKE_ONNX_MODEL,
                                     config.WAKE_ONNX_MEL,
                                     config.WAKE_ONNX_EMB,
                                     config.WAKE_ONNX_THRESHOLD)
                print(f"вейкворд: {config.WAKE_ONNX_MODEL} "
                      f"(порог {config.WAKE_ONNX_THRESHOLD})", flush=True)
            except Exception as exc:
                _failed = True
                print(f"вейкворд: модель не загрузилась: {exc}", flush=True)
                return None
    return _detector
