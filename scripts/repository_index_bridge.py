"""Read a verified DoGBench implementation corpus on the filesystem that owns it."""

import argparse
import json
import subprocess
from pathlib import Path

from shopresolve.documents.repository import RepositoryEnvironment
from shopresolve.errors import DomainError


class WslRepositoryEnvironment(RepositoryEnvironment):
    def __init__(self, *args, wsl_command, workspace, **kwargs):
        super().__init__(*args, **kwargs)
        self.wsl_command = wsl_command
        self.linux_workspace = workspace

    def index_documents(self, root):
        if root != "code" or self.roots["code"] is None:
            yield from super().index_documents(root)
            return
        head = self._git(self.roots["code"], "rev-parse", "HEAD").decode().strip()
        command = [
            *self.wsl_command,
            "-m",
            "scripts.repository_index_bridge",
            self.linux_workspace,
            head,
        ]
        with subprocess.Popen(command, stdout=subprocess.PIPE) as process:
            previous = ""
            try:
                for line in process.stdout:
                    if self.cancel_event.is_set():
                        raise DomainError("TOOL_CANCELLED", "Implementation indexing stopped")
                    path, content = json.loads(line)
                    if (
                        path not in self.tracked[root]
                        or path <= previous
                        or not isinstance(content, str)
                    ):
                        raise DomainError("REPOSITORY_IO", "Invalid implementation index stream")
                    previous = path
                    yield path, content
                if process.wait() != 0:
                    raise DomainError("REPOSITORY_IO", "WSL implementation index reader failed")
            finally:
                if process.poll() is None:
                    process.kill()


def stream_index(workspace, expected_head):
    allowed = (Path.home() / ".cache/docchange/dogbench-prepared/sealed/workspaces").resolve()
    workspace = Path(workspace).resolve()
    if not workspace.is_relative_to(allowed):
        raise ValueError("Use a sealed DoGBench workspace")
    code = workspace / "code"
    if code.is_symlink() or not code.is_dir():
        raise ValueError("Expected a local implementation checkout")
    environment = RepositoryEnvironment(workspace / "docs", code, workspace)
    if environment._git(code, "rev-parse", "HEAD").decode().strip() != expected_head:
        raise ValueError("Implementation HEAD differs from the verified checkout")
    for entry in environment.index_documents("code"):
        print(json.dumps(entry, separators=(",", ":")))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace")
    parser.add_argument("expected_head")
    args = parser.parse_args()
    stream_index(args.workspace, args.expected_head)
