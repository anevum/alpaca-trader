"""Run the repository's simulation suite without inherited secrets or network."""
import os
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    if (root / ".env").exists():
        print("Staging requires a clean checkout without a .env file.", file=sys.stderr)
        return 2
    clean = {k: os.environ[k] for k in ("PATH", "LANG", "SYSTEMROOT") if k in os.environ}
    clean.update({"PYTHONPATH": str(root), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                  "RHEN_STAGING": "1", "TZ": "America/New_York"})
    return subprocess.run([sys.executable, "-m", "pytest", "-p", "staging.network_guard", "-q",
                           *sys.argv[1:]], cwd=root, env=clean).returncode


if __name__ == "__main__":
    raise SystemExit(main())
