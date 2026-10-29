"""Constrained Docker adapter. No shell strings, arbitrary flags, images or host paths."""

import asyncio
import base64
import hashlib
import io
import json
import os
import stat
import tarfile
import time
from pathlib import Path, PurePosixPath
from uuid import UUID

from shopresolve.service import digest


class SandboxError(RuntimeError):
    pass


class DockerSandbox:
    def __init__(self, root=".cache/sandbox", image="shopresolve-sandbox:v1", timeout=20):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.image, self.timeout = image, timeout
        self.semaphore = asyncio.Semaphore(1)

    async def command(self, *args, timeout=10, limit=65536):
        process = await asyncio.create_subprocess_exec(
            "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        async def bounded(stream):
            result = bytearray()
            while data := await stream.read(8192):
                if len(result) < limit:
                    result.extend(data[: limit - len(result)])
            return bytes(result)

        try:
            async with asyncio.timeout(timeout):
                stdout, stderr = await asyncio.gather(
                    bounded(process.stdout), bounded(process.stderr)
                )
                code = await process.wait()
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise
        return code, stdout, stderr

    async def inspect(self, name):
        code, output, _ = await self.command("inspect", name)
        if code:
            return None
        return json.loads(output)[0]

    @staticmethod
    def owned(info, job_id):
        labels = info.get("Config", {}).get("Labels", {})
        return (
            labels.get("shopresolve.job_id") == job_id
            and labels.get("shopresolve.project") == "sandbox"
        )

    def folder(self, job_id):
        if str(UUID(job_id)) != job_id:
            raise SandboxError("Job ID must be a canonical UUID")
        folder = (self.root / job_id).resolve()
        if not folder.is_relative_to(self.root) or folder == self.root:
            raise SandboxError("Invalid workspace")
        return folder

    @staticmethod
    def write_manifest(path, data):
        temp = path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        temp.replace(path)

    async def run(self, job_id, code, csv_bytes):
        if len(code.encode()) > 20000 or len(csv_bytes) > 2 * 1024 * 1024:
            raise SandboxError("Input limit exceeded")
        folder = self.folder(job_id)
        request_hash = digest([code, hashlib.sha256(csv_bytes).hexdigest()])
        async with self.semaphore:
            folder.mkdir(parents=True, exist_ok=True)
            manifest_path = folder / "manifest.json"
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest["request_hash"] != request_hash:
                    raise SandboxError("Job ID reused with different inputs")
                if manifest["status"] in {"COMPLETED", "FAILED", "TIMED_OUT"}:
                    return manifest
            else:
                manifest = {
                    "job_id": job_id,
                    "request_hash": request_hash,
                    "status": "PREPARED",
                    "created_at": time.time(),
                    "outputs": [],
                }
                self.write_manifest(manifest_path, manifest)
                (folder / "input").mkdir(exist_ok=True)
                (folder / "output").mkdir(exist_ok=True)
                (folder / "input" / "data.csv").write_bytes(csv_bytes)
                (folder / "main.py").write_text(code, encoding="utf-8")
            name = "shopresolve-sandbox-" + job_id
            info = await self.inspect(name)
            if info is not None and not self.owned(info, job_id):
                raise SandboxError("Container identity collision; refusing to touch it")
            if info is None:
                # All flags fixed by the operator. Only generated, validated workspace
                # paths are mounted; the repo, keys and Docker socket are NOT mounted.
                arguments = [
                    "create",
                    "--name",
                    name,
                    "--label",
                    "shopresolve.project=sandbox",
                    "--label",
                    f"shopresolve.job_id={job_id}",
                    "--network",
                    "none",
                    "--read-only",
                    "--user",
                    "10001:10001",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--pids-limit",
                    "64",
                    "--memory",
                    "512m",
                    "--cpus",
                    "1",
                    "--tmpfs",
                    "/tmp:rw,noexec,nosuid,size=32m",
                    "--tmpfs",
                    "/output:rw,noexec,nosuid,size=8m,mode=1777",
                    "--log-driver",
                    "local",
                    "--log-opt",
                    "max-size=1m",
                    "--log-opt",
                    "max-file=1",
                    "--log-opt",
                    "compress=false",
                    "--mount",
                    f"type=bind,source={folder / 'input'},target=/input,readonly",
                    "--mount",
                    f"type=bind,source={folder / 'main.py'},target=/app/main.py,readonly",
                    self.image,
                    "python",
                    "-I",
                    "/app/entry.py",
                ]
                rc, _, _ = await self.command(*arguments)
                if rc:
                    raise SandboxError("Cannot create the constrained execution container")
                info = await self.inspect(name)
            manifest["status"] = "RUNNING"
            self.write_manifest(manifest_path, manifest)
            if info["State"]["Status"] == "created":
                rc, _, _ = await self.command("start", name)
                if rc:
                    if self.owned(info, job_id):
                        await self.command("rm", "-f", name)
                    raise SandboxError("Cannot start sandbox")
            try:
                deadline = manifest["created_at"] + self.timeout
                while True:
                    info = await self.inspect(name)
                    if info is None or not self.owned(info, job_id):
                        raise SandboxError("Sandbox disappeared or identity changed")
                    if not info["State"]["Running"]:
                        manifest["status"] = "FAILED"
                        manifest["exit_code"] = info["State"]["ExitCode"]
                        break
                    marker_rc, marker, _ = await self.command(
                        "exec", name, "cat", "/tmp/shopresolve-finished"
                    )
                    if marker_rc == 0:
                        manifest["status"] = "COMPLETED" if marker.strip() == b"0" else "FAILED"
                        manifest["exit_code"] = 0 if marker.strip() == b"0" else 1
                        break
                    if time.time() >= deadline:
                        await self.command("kill", name)
                        manifest["status"] = "TIMED_OUT"
                        break
                    await asyncio.sleep(0.2)
                _, stdout, stderr = await self.command("logs", name)
                manifest["stdout"] = stdout.decode(errors="replace")[:65536]
                manifest["stderr"] = stderr.decode(errors="replace")[:65536]
                if info["State"]["Running"] and manifest["status"] != "TIMED_OUT":
                    rc, archive, _ = await self.command(
                        "exec",
                        name,
                        "/bin/tar",
                        "-C",
                        "/output",
                        "-cf",
                        "-",
                        ".",
                        limit=9 * 1024 * 1024,
                    )
                    if rc:
                        raise SandboxError("Cannot collect sandbox output")
                    manifest["outputs"] = self.collect_archive(archive)
                return manifest
            except SandboxError:
                manifest["status"] = "FAILED"
                manifest["error_code"] = "OUTPUT_REJECTED"
                raise
            finally:
                info = await self.inspect(name)
                if info and self.owned(info, job_id):
                    # Exact, verified project container only. Never prune global Docker state.
                    await self.command("rm", "-f", name)
                # Publish terminal status only after container cleanup. Otherwise
                # a caller can stop the manager on COMPLETED while cleanup is in flight.
                self.write_manifest(manifest_path, manifest)

    @staticmethod
    def collect_archive(data):
        outputs, total = [], 0
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
            for member in archive:
                parts = PurePosixPath(member.name).parts
                if member.isdir() and parts in {(), ("output",)}:
                    continue
                if parts and parts[0] == "output":
                    parts = parts[1:]
                if not member.isreg() or len(parts) != 1 or parts[0] in {"..", "."}:
                    raise SandboxError("Non-regular sandbox output rejected")
                filename = parts[0]
                if Path(filename).suffix.lower() not in {".json", ".csv", ".txt"}:
                    raise SandboxError("Unsupported output type")
                total += member.size
                if total > 5 * 1024 * 1024:
                    raise SandboxError("Sandbox output size limit exceeded")
                content = archive.extractfile(member).read()
                outputs.append(
                    {
                        "name": filename,
                        "size": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "base64": base64.b64encode(content).decode(),
                    }
                )
        return outputs

    @staticmethod
    def collect_outputs(output_dir):
        outputs, total = [], 0
        for path in output_dir.iterdir():
            metadata = path.lstat()
            if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
                raise SandboxError("Non-regular sandbox output rejected")
            if path.suffix.lower() not in {".json", ".csv", ".txt"}:
                raise SandboxError("Unsupported output type")
            total += metadata.st_size
            if total > 5 * 1024 * 1024:
                raise SandboxError("Sandbox output size limit exceeded")
            content = path.read_bytes()
            outputs.append(
                {
                    "name": path.name,
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "base64": base64.b64encode(content).decode(),
                }
            )
        return outputs
