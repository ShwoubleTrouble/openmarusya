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
import glob
import json
import math
import os
import select
import signal
import struct
import subprocess
import sys
import tempfile
import threading
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


def read_exact(stream, n: int) -> bytes | None:
    """Читает ровно n байт: pipe при bufsize=0 отдаёт короткие порции."""
    chunks = []
    got = 0
    while got < n:
        part = stream.read(n - got)
        if not part:
            return None
        chunks.append(part)
        got += len(part)
    return b"".join(chunks)


def frames(stream, rate: int, gain: int):
    """Отдаёт кадры s16le mono по FRAME_MS мс: даунмикс 4 каналов,
    децимация 48k -> 16k, усиление."""
    out_samples = rate * FRAME_MS // 1000
    step = NATIVE_RATE // rate            # 3
    need = out_samples * step * NATIVE_CH * 4
    while True:
        data = read_exact(stream, need)
        if data is None:
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
        proc.send_signal(signal.SIGCONT)   # снятый процесс не примет SIGTERM
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


def ask_server(server: str, pcm: bytes, rate: int, token: str, timeout: int,
               followup: bool = False, on_wake=None) -> bytes:
    req = urllib.request.Request(
        server.rstrip("/") + "/v1/voice",
        data=pcm,
        headers={"Content-Type": "application/octet-stream",
                 "X-Rate": str(rate),
                 "X-Token": token,
                 "X-Followup": "1" if followup else "0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        # заголовки приходят сразу после распознавания, тело — после синтеза
        if on_wake is not None and resp.headers.get("X-Wake") == "1":
            on_wake()
        return resp.read()


LED_MS = 600
LED_IDLE_MS = 4000
_led_idle = None


def _ringled(*args: str):
    return subprocess.Popen(
        ["ringled", *args],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def led_idle_start() -> None:
    """Медленное белое дыхание, пока ждём обращения."""
    global _led_idle
    if _led_idle is not None and _led_idle.poll() is None:
        return
    led_idle_stop()
    try:
        _led_idle = _ringled("--breathe", str(LED_IDLE_MS), "--duration", "86400000",
                             "-b", "40", "all", "FFFFFF")
    except OSError:
        _led_idle = None


def led_idle_stop() -> None:
    global _led_idle
    if _led_idle is not None and _led_idle.poll() is None:
        _led_idle.terminate()
        try:
            _led_idle.wait(timeout=1)
        except subprocess.TimeoutExpired:
            _led_idle.kill()
    _led_idle = None


def led_off() -> None:
    try:
        subprocess.run(["ringled", "off"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        pass


def pulse_ring() -> None:
    """Проснулись: гасим дежурное дыхание и делаем один зелёный вдох."""
    led_idle_stop()
    led_off()
    try:
        _ringled("--breathe", str(LED_MS), "--duration", str(LED_MS),
                 "-b", "100", "all", "00FF00")
    except OSError:
        pass


def aplay_args(device: str, path: str) -> list[str]:
    """Короткий буфер: при перебивании звук обрывается сразу,
    а не доигрывает уже отправленное в ALSA."""
    return ["aplay", "-q", "-B", "150000", "-D", device, path]


_player = None
_player_lock = threading.Lock()


def stop_playback() -> None:
    """Кнопка на капсуле: обрывает текущую речь."""
    with _player_lock:
        player = _player
    if player is not None and player.poll() is None:
        stop(player)
        print("кнопка: речь остановлена", flush=True)


def play(wav: bytes, device: str) -> None:
    global _player
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(wav)
        path = f.name
    player = subprocess.Popen(aplay_args(device, path),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with _player_lock:
        _player = player
    try:
        player.wait()
    finally:
        with _player_lock:
            _player = None
        os.unlink(path)


KEY_VOICECOMMAND = 582


def listen_button(code: int) -> None:
    """Ждёт нажатия кнопки (KEY_VOICECOMMAND) на любом /dev/input/event*."""
    fmt = struct.Struct("llHHi")           # 16 байт на 32-бит, 24 на 64-бит
    fds = {}
    for path in sorted(glob.glob("/dev/input/event*")):
        try:
            fds[os.open(path, os.O_RDONLY | os.O_NONBLOCK)] = path
        except OSError:
            pass
    if not fds:
        print("кнопка: нет доступа к /dev/input/event*", flush=True)
        return
    print(f"кнопка: KEY_VOICECOMMAND ({code}) на {', '.join(fds.values())}", flush=True)
    buffers = {fd: b"" for fd in fds}
    while True:
        try:
            ready, _, _ = select.select(list(fds), [], [], 1.0)
        except OSError:
            return
        for fd in ready:
            try:
                data = buffers[fd] + os.read(fd, fmt.size * 64)
            except OSError:
                continue
            cut = len(data) - len(data) % fmt.size
            buffers[fd] = data[cut:]
            for off in range(0, cut, fmt.size):
                _, _, typ, ev_code, value = fmt.unpack_from(data, off)
                if typ == 1 and ev_code == code and value == 1:
                    stop_playback()


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
    p.add_argument("--followup-s", type=float, default=5.0,
                   help="сколько секунд после ответа ждать продолжения без «Маруся»")
    p.add_argument("--timeout", type=int, default=120)
    p.add_argument("--button-code", type=int, default=KEY_VOICECOMMAND,
                   help="код кнопки остановки речи (KEY_VOICECOMMAND=582); 0 = выключить")
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
    if args.button_code:
        threading.Thread(target=listen_button, args=(args.button_code,), daemon=True).start()
    followup = False

    try:
        while True:
            led_idle_start()
            wait = args.followup_s if followup else args.wait_s
            proc = start_recorder(args.in_device, args.rate)
            try:
                time.sleep(0.2)
                if proc.poll() is not None:
                    print(f"arecord упал с кодом {proc.returncode}; "
                          f"проверь устройство {args.in_device}", flush=True)
                    return 1
                pcm, peak = record_utterance(frames(proc.stdout, args.rate, args.gain),
                                             args.rate, threshold,
                                             args.silence_ms, args.max_s, wait)
            finally:
                stop(proc)

            if not pcm:
                followup = False          # не дождались продолжения — снова ждём «Маруся»
                if args.once:
                    return 0
                print(f"нет речи (пик {peak} при пороге {threshold})", flush=True)
                continue

            print(f"записано {len(pcm) / (args.rate * 2):.1f} с, шлю..."
                  f"{' (продолжение)' if followup else ''}", flush=True)
            t0 = time.time()
            try:
                wav = ask_server(args.server, pcm, args.rate, args.token, args.timeout, followup,
                                 pulse_ring)
            except Exception as exc:
                print(f"ошибка сервера: {exc}", flush=True)
                followup = False
                if args.once:
                    return 1
                continue

            print(f"ответ за {time.time() - t0:.1f} с", flush=True)
            if not wav:
                followup = False
                print("не ко мне — пропускаю", flush=True)
                if args.once:
                    return 0
                continue
            play(wav, args.out_device)
            followup = True               # 5 секунд ждём продолжения без обращения
            if args.once:
                return 0

    except KeyboardInterrupt:
        led_idle_stop()
        led_off()
        return 0


if __name__ == "__main__":
    sys.exit(main())
