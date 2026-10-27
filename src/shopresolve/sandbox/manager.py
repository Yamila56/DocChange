"""Separate local control process. Only this process needs Docker access."""

import asyncio
import base64
import json
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from shopresolve.artifacts import validate_csv
from shopresolve.config import Settings
from shopresolve.errors import DomainError
from shopresolve.sandbox.runner import DockerSandbox


class JobSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    code: str = Field(min_length=1, max_length=20000)
    csv_base64: str = Field(max_length=2800000)


def create_manager(settings=None, runner=None):
    settings = settings or Settings()
    app = FastAPI(title="ShopResolve Sandbox Manager", docs_url=None, redoc_url=None)
    runner = runner or DockerSandbox(settings.sandbox_root)
    tasks = {}
    bearer = HTTPBearer(auto_error=False)

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse(
            status_code=exc.status, content={"error_code": exc.code, "message": exc.message}
        )

    async def authorized(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ):
        if (
            not settings.sandbox_token
            or credentials is None
            or not secrets.compare_digest(credentials.credentials, settings.sandbox_token)
        ):
            raise HTTPException(401, "Internal authorization required")

    def schedule(job_id, spec):
        if job_id in tasks:
            return
        if len(tasks) >= 8:
            raise HTTPException(429, "Sandbox queue full")

        async def execute():
            try:
                await runner.run(
                    job_id, spec.code, base64.b64decode(spec.csv_base64, validate=True)
                )
            except Exception:
                # Error details may include untrusted code/data. The durable manifest
                # records status where possible; do not print request contents.
                pass
            finally:
                tasks.pop(job_id, None)

        tasks[job_id] = asyncio.create_task(execute())

    @app.post("/internal/jobs/{job_id}", dependencies=[Depends(authorized)], status_code=202)
    async def submit(job_id: UUID, spec: JobSpec):
        job_id = str(job_id)
        folder = runner.folder(job_id)
        try:
            validate_csv(base64.b64decode(spec.csv_base64, validate=True))
        except ValueError as exc:
            raise HTTPException(422, "Invalid input encoding") from exc
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "spec.json"
        if path.exists() and json.loads(path.read_text(encoding="utf-8")) != spec.model_dump():
            raise HTTPException(409, "Job ID input conflict")
        if not path.exists():
            runner.write_manifest(path, spec.model_dump())
        schedule(job_id, spec)
        return {"id": job_id, "status": "QUEUED"}

    @app.get("/internal/jobs/{job_id}", dependencies=[Depends(authorized)])
    async def status(job_id: UUID):
        job_id = str(job_id)
        folder = runner.folder(job_id)
        path = folder / "manifest.json"
        if path.exists():
            manifest = json.loads(path.read_text(encoding="utf-8"))
            if manifest["status"] in {"COMPLETED", "FAILED", "TIMED_OUT"}:
                return manifest
        spec_path = folder / "spec.json"
        if not spec_path.exists():
            raise HTTPException(404, "Not found")
        schedule(job_id, JobSpec.model_validate_json(spec_path.read_text(encoding="utf-8")))
        return {"job_id": job_id, "status": "RUNNING" if path.exists() else "QUEUED"}

    return app
