"""Trusted container supervisor: keep tmpfs available until the manager collects outputs.

The marker is only liveness information, never evidence of business authorization
or calculation correctness. Untrusted code runs with the same restricted UID.
"""

import pathlib
import runpy
import time
import traceback

exit_code = 0
try:
    runpy.run_path("/app/main.py", run_name="__main__")
except BaseException:
    traceback.print_exc()
    exit_code = 1
pathlib.Path("/tmp/shopresolve-finished").write_text(str(exit_code))
while True:
    time.sleep(0.2)
