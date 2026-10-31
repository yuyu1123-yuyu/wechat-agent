"""Run the bridge with bounded restart backoff; no machine startup changes."""
import os
import subprocess
import sys
import time
from pathlib import Path


def main():
    delay = 5
    root = Path(__file__).resolve().parents[2]
    lock = root / 'data' / 'supervisor.lock'
    lock.parent.mkdir(exist_ok=True)
    with lock.open('a+b') as handle:
        import msvcrt
        handle.seek(0)
        if not handle.read(1):
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            print('Bridge supervisor already running.', flush=True)
            return 1
        env = dict(os.environ, PYTHONIOENCODING='utf-8')
        while True:
            started = time.monotonic()
            child = subprocess.Popen([sys.executable, '-m', 'wechat_agent.main', *sys.argv[1:]],
                                     cwd=root, env=env)
            try:
                code = child.wait()
            except KeyboardInterrupt:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
                return 0
            if code == 0:
                return 0
            if time.monotonic() - started > 120:
                delay = 5
            print(f'Bridge exited ({code}); retry in {delay}s. Check WeChat login.', flush=True)
            time.sleep(delay)
            delay = min(60, delay * 2)


if __name__ == '__main__':
    sys.exit(main())
