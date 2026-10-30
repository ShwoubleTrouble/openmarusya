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
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

FRAME_MS = 30


def rms(frame: bytes) -> int:
    a = array.array("h")
    a.frombytes(frame)
    if sys.byteorder == "big":
        a.byteswap()
    if not a:
        return 0
    return math.isqrt(sum(v * v for v in a) // len(a))


def start_recorder(device: str, rate: int) -> subprocess.Popen:
    return subprocess.Popen(
        ["arecord", "-q", "-D", device, "-f", "S16_LE", "-r", str(rate),
         "-c", "1", "-t", "raw"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
    )


def stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


def record_utterance(proc, rate: int, threshold: int, trailing_ms: int,
                     max_s: float, wait_s: float) -> bytes | None:
    """Ждёт речь, пишет до паузы. None — если речь не началась."""
    frame_bytes = rate * 2 * FRAME_MS // 1000
    trailing_frames = max(3, trailing_ms // FRAME_MS)
    max_frames = int(max_s * 1000 / FRAME_MS)
    wait_frames = int(wait_s * 1000 / FRAME_MS)

    buf = bytearray()
    speech_started = False
    silence = 0

    for _ in range(wait_frames):
        chunk = proc.stdout.read(frame_bytes)
        if not chunk or len(chunk) < frame_bytes:
            return None
        if rms(chunk) >= threshold:
            speech_started = True
            buf += chunk
            break

    if not speech_started:
        return None

    while len(buf) < max_frames * frame_bytes:
        chunk = proc.stdout.read(frame_bytes)
        if not chunk or len(chunk) < frame_bytes:
            break
        buf += chunk
        if rms(chunk) < threshold:
            silence += 1
            if silence >= trailing_frames:
                break
        else:
            silence = 0

    # срезаем хвостовую тишину, оставляя 150 мс
    drop = max(0, silence - 5) * frame_bytes
    if drop:
        del buf[len(buf) - min(drop, len(buf)):]
    return bytes(buf)


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
    p.add_argument("--in-device", default="default")
    p.add_argument("--out-device", default="default")
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
        frame_bytes = args.rate * 2 * FRAME_MS // 1000
        levels = []
        for _ in range(30):
            chunk = probe.stdout.read(frame_bytes)
            if chunk and len(chunk) == frame_bytes:
                levels.append(rms(chunk))
        stop(probe)
        levels.sort()
        floor = levels[len(levels) // 2] if levels else 0
        threshold = max(400, int(floor * 4))
        print(f"шум {floor}, порог {threshold}", flush=True)

    print(f"сервер: {args.server}", flush=True)

    try:
        while True:
            proc = start_recorder(args.in_device, args.rate)
            try:
                pcm = record_utterance(proc, args.rate, threshold,
                                       args.silence_ms, args.max_s, args.wait_s)
            finally:
                stop(proc)

            if not pcm:
                if args.once:
                    return 0
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
