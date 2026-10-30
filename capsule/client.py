#!/usr/bin/env python3
"""Клиент для VK Капсула Мини (Alpine, musl, 210 МБ RAM).

Только stdlib + arecord/aplay (alsa-utils). Ничего не жрёт:
запись -> детект речи по громкости -> POST на сервер -> aplay ответа.

Запуск:
    python3 client.py --server http://192.168.1.10:8080
"""

import argparse
import array
import base64
import collections
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

FRAME_MS = 30
NATIVE_RATE = 48000
NATIVE_CH = 4


def rms(frame: bytes) -> int:
    """RMS после вычитания DC-смещения.

    Некоторые ADC (в т.ч. на капсуле) отдают тишину как постоянную -32768,
    из-за чего обычный RMS равен 32767 и триггер никогда не срабатывает.
    """
    a = array.array("h")
    a.frombytes(frame)
    if sys.byteorder == "big":
        a.byteswap()
    if not a:
        return 0
    mean = sum(a) / len(a)
    return math.isqrt(int(sum((v - mean) ** 2 for v in a)) // len(a))


def start_recorder(device: str, rate: int) -> subprocess.Popen:
    """Нативный поток: на капсуле ALSA отдаёт S32_LE/48k/4ch, только так
    сигнал не теряется в 10 раз, как через plughw."""
    return subprocess.Popen(
        ["arecord", "-q", "-D", device, "-f", "S32_LE",
         "-r", str(NATIVE_RATE), "-c", str(NATIVE_CH), "-t", "raw"],
        stdout=subprocess.PIPE,
        bufsize=0,
    )


def frames(stream, rate: int, gain: int):
    """Отдаёт кадры s16le mono по FRAME_MS мс: даунмикс 4 каналов,
    децимация 48k -> 16k, усиление."""
    out_samples = rate * FRAME_MS // 1000
    step = NATIVE_RATE // rate            # 3
    need = out_samples * step * NATIVE_CH * 4
    while True:
        data = stream.read(need)
        if not data or len(data) < need:
            return
        src = array.array("i")
        src.frombytes(data)
        if sys.byteorder == "big":
            src.byteswap()
        dst = array.array("h")
        for i in range(0, len(src), step * NATIVE_CH):
            v = (src[i] + src[i + NATIVE_CH] + src[i + 2 * NATIVE_CH]) // 3
            v = (v >> 16) * gain
            dst.append(-32768 if v < -32768 else 32767 if v > 32767 else v)
        yield dst.tobytes()


def stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


def record_utterance(it, rate: int, threshold: int, trailing_ms: int,
                     max_s: float, wait_s: float, min_s: float = 0.4) -> tuple[bytes | None, int]:
    """Ждёт речь, пишет до паузы. Возвращает (pcm|None, пиковый уровень)."""
    frame_bytes = rate * 2 * FRAME_MS // 1000
    trailing_frames = max(3, trailing_ms // FRAME_MS)
    trigger_frames = 5                     # 150 мс речи подряд
    preroll = collections.deque(maxlen=10)  # 300 мс до начала фразы
    max_frames = int(max_s * 1000 / FRAME_MS)
    wait_frames = int(wait_s * 1000 / FRAME_MS)
    peak = 0

    # сбрасываем переходный процесс в начале захвата
    for _ in range(10):
        try:
            next(it)
        except StopIteration:
            return None, peak

    buf = bytearray()
    run = 0
    seen = 0

    # ждём устойчивой речи
    while seen < wait_frames:
        try:
            chunk = next(it)
        except StopIteration:
            return None, peak
        seen += 1
        level = rms(chunk)
        peak = max(peak, level)
        preroll.append(chunk)
        if level >= threshold:
            run += 1
            if run >= trigger_frames:
                buf += b"".join(preroll)
                break
        else:
            run = 0
    else:
        return None, peak

    silence = 0
    while len(buf) < max_frames * frame_bytes:
        try:
            chunk = next(it)
        except StopIteration:
            break
        buf += chunk
        level = rms(chunk)
        peak = max(peak, level)
        if level < threshold:
            silence += 1
            if silence >= trailing_frames:
                break
        else:
            silence = 0

    # срезаем хвостовую тишину, оставляя 150 мс
    drop = max(0, silence - 5) * frame_bytes
    if drop:
        del buf[len(buf) - min(drop, len(buf)):]

    if len(buf) < rate * 2 * min_s:
        return None, peak
    return bytes(buf), peak


def ask_server(server: str, pcm: bytes, rate: int, token: str, timeout: int) -> bytes:
    req = urllib.request.Request(
        server.rstrip("/") + "/v1/voice",
        data=pcm,
        headers={"Content-Type": "application/octet-stream",
                 "X-Rate": str(rate),
                 "X-Token": token},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def play(wav: bytes, device: str) -> None:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(wav)
        path = f.name
    try:
        subprocess.run(["aplay", "-q", "-D", device, path], check=False)
    finally:
        os.unlink(path)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--server", default=os.environ.get("MARYA_SERVER", "http://127.0.0.1:8080"))
    p.add_argument("--token", default=os.environ.get("MARYA_TOKEN", ""))
    p.add_argument("--rate", type=int, default=16000)
    p.add_argument("--in-device", default="hw:0,1",
                   help="у капсулы микрофон это hw:0,1 (нативный S32_LE/48k/4ch)")
    p.add_argument("--out-device", default="default")
    p.add_argument("--gain", type=int, default=8,
                   help="усиление после даунмикса (микрофон капсулы тихий)")
    p.add_argument("--threshold", type=int, default=0,
                   help="порог RMS; 0 = калибровка по шуму на старте")
    p.add_argument("--silence-ms", type=int, default=800)
    p.add_argument("--max-s", type=float, default=12.0)
    p.add_argument("--wait-s", type=float, default=6.0)
    p.add_argument("--timeout", type=int, default=120)
    p.add_argument("--once", action="store_true")
    args = p.parse_args()

    threshold = args.threshold
    if threshold <= 0:
        probe = start_recorder(args.in_device, args.rate)
        levels = []
        raw_min, raw_max = 32767, -32768
        for chunk in frames(probe.stdout, args.rate, args.gain):
            levels.append(rms(chunk))
            a = array.array("h")
            a.frombytes(chunk)
            if a:
                raw_min = min(raw_min, min(a))
                raw_max = max(raw_max, max(a))
            if len(levels) >= 30:
                break
        stop(probe)
        levels.sort()
        floor = levels[len(levels) // 2] if levels else 0
        threshold = max(150, int(floor * 4))
        print(f"сигнал: {raw_min}..{raw_max}, шум(AC) {floor}, порог {threshold}", flush=True)
        if raw_max - raw_min < 200:
            print("! захват отдаёт постоянный уровень — микрофон не пишет или не тот "
                  "--in-device. Смотри: arecord -l", flush=True)

    print(f"сервер: {args.server}", flush=True)

    try:
        while True:
            proc = start_recorder(args.in_device, args.rate)
            try:
                time.sleep(0.2)
                if proc.poll() is not None:
                    print(f"arecord упал с кодом {proc.returncode}; "
                          f"проверь устройство {args.in_device}", flush=True)
                    return 1
                pcm, peak = record_utterance(frames(proc.stdout, args.rate, args.gain),
                                             args.rate, threshold,
                                             args.silence_ms, args.max_s, args.wait_s)
            finally:
                stop(proc)

            if not pcm:
                if args.once:
                    return 0
                print(f"нет речи (пик {peak} при пороге {threshold})", flush=True)
                continue

            print(f"записано {len(pcm) / (args.rate * 2):.1f} с, шлю...", flush=True)
            t0 = time.time()
            try:
                wav = ask_server(args.server, pcm, args.rate, args.token, args.timeout)
            except Exception as exc:
                print(f"ошибка сервера: {exc}", flush=True)
                if args.once:
                    return 1
                continue

            print(f"ответ за {time.time() - t0:.1f} с", flush=True)
            play(wav, args.out_device)
            if args.once:
                return 0

    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
