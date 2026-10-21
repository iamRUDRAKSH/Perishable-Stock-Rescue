"""Start both services locally; Ctrl+C stops both. No package installation needed."""
import subprocess
import sys
import time
from pathlib import Path

from stock_rescue.common import APIError, call


def main():
    root = Path(__file__).resolve().parent
    processes = []
    try:
        processes.append(subprocess.Popen([sys.executable, '-m', 'stock_rescue.inventory'], cwd=root))
        for _ in range(50):
            if processes[0].poll() is not None:
                raise RuntimeError('Stock service failed to start; check port 8001.')
            try:
                call('http://127.0.0.1:8001/health')
                break
            except APIError:
                time.sleep(0.1)
        else:
            raise RuntimeError('Stock service did not become ready.')
        processes.append(subprocess.Popen([sys.executable, '-m', 'stock_rescue.coordinator'], cwd=root))
        print('Open http://localhost:8000 — Ctrl+C stops both services.', flush=True)
        while all(process.poll() is None for process in processes):
            time.sleep(0.5)
        raise RuntimeError('A service stopped unexpectedly. Check the logs above.')
    except KeyboardInterrupt:
        print('\nStopping services.')
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            process.wait()


if __name__ == '__main__':
    main()
