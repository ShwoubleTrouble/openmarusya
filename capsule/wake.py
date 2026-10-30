#!/usr/bin/env python3
"""Локальный вейкворд на капсуле: openWakeWord-модель через onnxruntime.

Три ONNX-модели:
  melspectrogram.onnx   — 32 мел-полосы из 16 кГц PCM;
  embedding_model.onnx  — 96-мерный вектор из 76 кадров;
  <слово>.onnx          — классификатор по 16 векторам.

Клиент вызывает feed(pcm) и получает True, когда услышал слово.
"""

import time

import numpy as np
import onnxruntime as ort

SR = 16000
CHUNK = 1280            # 80 мс — шаг классификатора
CONTEXT = 1760          # 80 мс + 30 мс контекста для мел-спектра
MEL_FRAMES = 76         # окно эмбеддинга (0.76 с)
EMB_FRAMES = 16         # окно классификатора (1.3 с)


class Detector:
    def __init__(self, model_path: str, mel_path: str, emb_path: str,
                 threshold: float = 0.5, debounce_s: float = 1.0,
                 threads: int = 1):
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = threads
        opts.intra_op_num_threads = threads
        self.mel = ort.InferenceSession(mel_path, sess_options=opts,
                                        providers=["CPUExecutionProvider"])
        self.emb = ort.InferenceSession(emb_path, sess_options=opts,
                                        providers=["CPUExecutionProvider"])
        self.cls = ort.InferenceSession(model_path, sess_options=opts,
                                        providers=["CPUExecutionProvider"])
        self.cls_input = self.cls.get_inputs()[0].name
        self.threshold = threshold
        self.debounce_s = debounce_s
        self._last_hit = -1e9
        self.reset()

    def reset(self) -> None:
        self.raw = np.zeros(0, dtype=np.int16)
        self.pending = np.zeros(0, dtype=np.int16)
        self.mels = np.ones((MEL_FRAMES, 32), dtype=np.float32)
        self.feats = np.ones((EMB_FRAMES, 96), dtype=np.float32)
        self.mel_count = 0
        self.emb_count = 0

    def _mel(self, samples: np.ndarray) -> np.ndarray:
        x = samples.astype(np.float32)[None, :]
        out = self.mel.run(None, {"input": x})[0]
        return np.squeeze(out) / 10.0 + 2.0

    def _embedding(self, frames: np.ndarray) -> np.ndarray:
        x = frames.astype(np.float32)[None, :, :, None]
        return self.emb.run(None, {"input_1": x})[0].reshape(-1)

    def _score(self) -> float:
        x = self.feats[None, :, :].astype(np.float32)
        return float(np.asarray(self.cls.run(None, {self.cls_input: x})[0]).reshape(-1)[0])

    def _step(self, chunk: np.ndarray) -> float | None:
        self.raw = np.concatenate((self.raw, chunk))[-CONTEXT:]
        frames = self._mel(self.raw)
        if frames.ndim == 1:
            frames = frames[None, :]
        self.mels = np.vstack((self.mels, frames))[-MEL_FRAMES:, :]
        self.mel_count += len(frames)
        if self.mel_count < MEL_FRAMES:
            return None

        self.feats = np.vstack((self.feats, self._embedding(self.mels)))[-EMB_FRAMES:, :]
        self.emb_count += 1
        if self.emb_count < EMB_FRAMES:
            return None
        return self._score()

    def feed(self, pcm: bytes) -> bool:
        """pcm: s16le mono 16 кГц. True — услышали слово."""
        self.pending = np.concatenate((self.pending, np.frombuffer(pcm, dtype=np.int16)))
        hit = False
        while len(self.pending) >= CHUNK:
            score = self._step(self.pending[:CHUNK])
            self.pending = self.pending[CHUNK:]
            if score is None or score < self.threshold:
                continue
            now = time.monotonic()
            if now - self._last_hit >= self.debounce_s:
                self._last_hit = now
                hit = True
        return hit
