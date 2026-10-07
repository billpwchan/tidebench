"""Run both local development services and terminate both on exit."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
processes = []


def stop(*_):
    for process in processes:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)


signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)
try:
    processes.append(subprocess.Popen([sys.executable, "-m", "tidebench"], cwd=root, start_new_session=True))
    processes.append(subprocess.Popen(["npm", "run", "dev"], cwd=root / "frontend", start_new_session=True))
    print("Tidebench: http://localhost:5173 · API: http://127.0.0.1:8000", flush=True)
    while all(process.poll() is None for process in processes):
        time.sleep(0.25)
finally:
    stop()
    for process in processes:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
sys.exit(
    next((process.returncode for process in processes if process.returncode and process.returncode > 0), 0)
)
