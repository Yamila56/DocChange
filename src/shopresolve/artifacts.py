import csv
import hashlib
import io
import re
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from shopresolve.errors import DomainError
from shopresolve.models import Artifact
from shopresolve.service import CommerceService, digest


def validate_csv(content):
    if not content or len(content) > 2 * 1024 * 1024:
        raise DomainError("FILE_LIMIT", "CSV must be nonempty and at most 2 MiB", 413)
    try:
        text = content.decode("utf-8-sig")
        if "\x00" in text:
            raise ValueError("Binary data")
        reader = csv.reader(io.StringIO(text), strict=True)
        headers = next(reader)
        if not headers or len(headers) > 64 or len(set(headers)) != len(headers):
            raise ValueError("Invalid headers")
        if any(h.lstrip().startswith(("=", "+", "@", "-")) for h in headers):
            raise ValueError("Spreadsheet formula headers are not accepted")
        rows = 0
        for row in reader:
            rows += 1
            if rows > 10000 or len(row) != len(headers) or any(len(c) > 4096 for c in row):
                raise ValueError("CSV dimensions exceeded")
            # Prevent spreadsheet-formula injection when the original is downloaded.
            for cell in row:
                value = cell.lstrip()
                if value.startswith(("=", "+", "@")) or (
                    value.startswith("-") and not re.fullmatch(r"-\d+(\.\d+)?", value)
                ):
                    raise ValueError("Spreadsheet formula cells are not accepted")
        return rows
    except (UnicodeError, csv.Error, StopIteration, ValueError) as exc:
        raise DomainError(
            "INVALID_CSV", "Invalid UTF-8 CSV, dimensions, or unsafe formula", 422
        ) from exc


class ArtifactStore:
    def __init__(self, sessions, root):
        self.sessions = sessions
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.commerce = CommerceService(sessions)

    def path(self, key):
        if not re.fullmatch(r"[0-9a-f]{64}", key):
            raise DomainError("ARTIFACT_INTEGRITY", "Invalid storage key")
        path = self.root / key
        if path.is_symlink() or not path.resolve().is_relative_to(self.root):
            raise DomainError("ARTIFACT_INTEGRITY", "Invalid storage location")
        return path

    async def upload(self, owner, name, content, key):
        if not name.endswith(".csv") or any(x in name for x in ("/", "\\", "\x00")):
            raise DomainError("INVALID_FILENAME", "A plain .csv filename is required", 422)
        validate_csv(content)
        sha = hashlib.sha256(content).hexdigest()
        storage_key = digest([owner, sha])
        path = self.path(storage_key)
        if not path.exists():
            temporary = self.root / (str(uuid4()) + ".tmp")
            temporary.write_bytes(content)
            temporary.replace(path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise DomainError("ARTIFACT_INTEGRITY", "Stored file hash mismatch")
        async with self.sessions.begin() as session:
            idem = await self.commerce._idem(
                session, owner, "artifact", key, {"name": name, "sha256": sha}
            )
            if idem.response is not None:
                return idem.response
            artifact = Artifact(
                id=str(uuid4()),
                owner_id=owner,
                name=name,
                storage_key=storage_key,
                sha256=sha,
                size=len(content),
            )
            session.add(artifact)
            idem.response = {"id": artifact.id, "name": name, "sha256": sha, "size": len(content)}
            return idem.response

    async def read(self, owner, artifact_id):
        async with self.sessions() as session:
            row = await session.scalar(
                select(Artifact).where(Artifact.id == artifact_id, Artifact.owner_id == owner)
            )
            if row is None:
                raise DomainError("NOT_FOUND", "Resource not found", 404)
            content = self.path(row.storage_key).read_bytes()
            if hashlib.sha256(content).hexdigest() != row.sha256:
                raise DomainError("ARTIFACT_INTEGRITY", "Stored file hash mismatch")
            return row.name, content
