#!/usr/bin/env python3
"""Громкость кнопками капсулы: KEY_VOLUMEDOWN (114) и KEY_VOLUMEUP (115).

Меняет Master и Playback вместе, сохраняя их соотношение:
Playback = Master/100 * 251, как на ползунках интерфейса.
"""

import array
import glob
import io
import math
import os
import re
import select
import struct
import subprocess
import sys
import wave

CARD = "0"
OUT_DEVICE = "default"
BEEP_MS = 45
BEEP_AMP = 26000            # почти полная шкала, иначе на капсуле еле слышно
MASTER_MAX = 100
PLAYBACK_MAX = 251
STEP = 5                    # шаг Master, примерно 3 дБ

KEY_VOLUMEDOWN = 114
KEY_VOLUMEUP = 115
# выше при прибавлении, ниже при убавлении
BEEP_HZ = {KEY_VOLUMEUP: 1100, KEY_VOLUMEDOWN: 700}

level = MASTER_MAX


def amixer(*args: str) -> str:
    res = subprocess.run(["amixer", "-c", CARD, *args],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         text=True, timeout=5)
    return res.stdout


def read_level() -> int:
    """Текущее Master в сырых единицах (0..100)."""
    m = re.search(r"(?:Playback|Capture)\s+(\d+)\s+\[", amixer("sget", "Master"))
    return int(m.group(1)) if m else MASTER_MAX


_beeps: dict[int, bytes] = {}


def beep(code: int) -> None:
    """Короткий щелчок: выше при прибавлении, ниже при убавлении."""
    freq = BEEP_HZ.get(code)
    if freq is None:
        return
    data = _beeps.get(freq)
    if data is None:
        rate = 48000
        n = rate * BEEP_MS // 1000
        a = array.array("h")
        for i in range(n):
            env = math.sin(math.pi * i / n)       # плавные края, без щелчков
            a.append(int(BEEP_AMP * env *
                         math.sin(2 * math.pi * freq * i / rate)))
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(a.tobytes())
        data = _beeps[freq] = buf.getvalue()
    try:
        subprocess.run(["aplay", "-q", "-D", OUT_DEVICE, "-"], input=data,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        pass


def apply(value: int) -> None:
    value = max(0, min(MASTER_MAX, value))
    playback = round(value / MASTER_MAX * PLAYBACK_MAX)
    amixer("sset", "Master", str(value))
    amixer("sset", "Playback", str(playback))
    print(f"громкость: {value}% (Master {value}, Playback {playback})", flush=True)


def main() -> int:
    global level
    level = read_level()
    fmt = struct.Struct("llHHi")           # 16 байт на 32-бит, 24 на 64-бит
    fds = {}
    for path in sorted(glob.glob("/dev/input/event*")):
        try:
            fds[os.open(path, os.O_RDONLY | os.O_NONBLOCK)] = path
        except OSError:
            pass
    if not fds:
        print("громкость: нет доступа к /dev/input/event*", flush=True)
        return 1
    print(f"громкость: {level}%, кнопки на {', '.join(fds.values())}", flush=True)
    buffers = {fd: b"" for fd in fds}
    while True:
        try:
            ready, _, _ = select.select(list(fds), [], [], 1.0)
        except OSError:
            return 1
        for fd in ready:
            try:
                data = buffers[fd] + os.read(fd, fmt.size * 64)
            except OSError:
                continue
            cut = len(data) - len(data) % fmt.size
            buffers[fd] = data[cut:]
            for off in range(0, cut, fmt.size):
                _, _, typ, code, value = fmt.unpack_from(data, off)
                if typ != 1 or value == 0:      # только нажатия, не отпускание
                    continue
                if code == KEY_VOLUMEUP:
                    level = min(MASTER_MAX, level + STEP)
                elif code == KEY_VOLUMEDOWN:
                    level = max(0, level - STEP)
                else:
                    continue
                apply(level)
                beep(code)
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
