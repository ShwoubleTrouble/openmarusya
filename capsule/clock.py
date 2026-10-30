#!/usr/bin/env python3
"""Часы на экране капсулы: раз в минуту вызывает `clockdisp HH:MM`."""

import signal
import subprocess
import sys
import time

CMD = "clockdisp"


def show(hhmm: str) -> None:
    try:
        subprocess.run([CMD, hhmm], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"clockdisp: {exc}", flush=True)


def main() -> int:
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    shown = None
    while True:
        now = time.localtime()
        hhmm = time.strftime("%H:%M", now)
        if hhmm != shown:
            show(hhmm)
            shown = hhmm
            print(f"часы: {hhmm}", flush=True)
        # просыпаемся сразу после смены минуты, чтобы не отставать на тик
        time.sleep(60 - now.tm_sec + 0.2)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
