#!/usr/bin/env python3
"""Create the combined SP for the current Owner `az login` and upsert new_final.json.

Usage
-----
az login   # the user whose subscription should get the SP
python3 scripts/add_login_sp.py

Optional:
  python3 scripts/add_login_sp.py --email you@gmail.com --name you --out new_final.json

The file is a JSON array the Deploy K3 page can paste. Re-running for the same
subscription replaces that row (bootstrap resets the existing SP secret).
Never prints AZURE_CLIENT_SECRET.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    deploy = Path(__file__).resolve().parent / "kimi_k3_deploy.py"
    raise SystemExit(subprocess.call([sys.executable, str(deploy), "add-login", *sys.argv[1:]]))
