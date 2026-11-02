"""Launch an explicitly selected project command in WSL; controller credentials stay in env."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
linux_root = "/mnt/" + root.drive[0].lower() + root.as_posix()[2:]
environment = os.environ.copy()
if len(sys.argv) > 1 and Path(sys.argv[1]).name == "prepare_dogbench.py":
    gh = shutil.which("gh") or str(
        Path(environment["LOCALAPPDATA"]) / "Programs/GitHubCLI/2.103.3/bin/gh.exe"
    )
    environment["GH_TOKEN"] = subprocess.run(
        [gh, "auth", "token"], capture_output=True, text=True, check=True
    ).stdout.strip()
    environment["WSLENV"] = ":".join(filter(None, (environment.get("WSLENV"), "GH_TOKEN")))
else:
    environment.pop("GH_TOKEN", None)
python = (
    ".venv-bench-linux/bin/python"
    if (root / ".venv-bench-linux/pyvenv.cfg").exists()
    else "python3"
)
command = ["wsl", "-d", "Ubuntu", "--cd", linux_root, "--", python, *sys.argv[1:]]
raise SystemExit(subprocess.run(command, env=environment, check=False).returncode)
