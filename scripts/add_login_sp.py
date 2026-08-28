#!/usr/bin/env python3
"""Create the combined SP for the current Owner `az login` and upsert new_final.json.

Usage
-----
az login   # the user whose subscription should get the SP
python3 scripts/add_login_sp.py --name sudarshan

Optional:
  python3 scripts/add_login_sp.py --name sudarshan --email you@gmail.com
  python3 scripts/add_login_sp.py --name sudarshan --out new_final.json

--name is required. It is stored on every JSON row as person_associated.
Rows are appended to repo-root new_final.json by default (same subscription
replaces that row). Never prints AZURE_CLIENT_SECRET.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    deploy = Path(__file__).resolve().parent / "kimi_k3_deploy.py"
    raise SystemExit(subprocess.call([sys.executable, str(deploy), "add-login", *sys.argv[1:]]))
