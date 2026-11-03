"""Native Windows process entry for a WSL controller's schema-only model call."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.pop("DOCCHANGE_CODEX_BINARY", None)
from scripts.score_dogbench import readonly_judge  # noqa: E402

readonly_judge(*(Path(p) for p in sys.argv[1:5]), sys.argv[5])
