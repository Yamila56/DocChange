import asyncio
import base64
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from shopresolve.config import Settings
from shopresolve.sandbox.manager import create_manager
from shopresolve.sandbox.runner import DockerSandbox


async def test_manager_auth_and_durable_job_identity(tmp_path):
    class FakeRunner(DockerSandbox):
        async def run(self, job_id, code, csv_bytes):
            path = self.folder(job_id) / "manifest.json"
            result = {"job_id": job_id, "status": "COMPLETED", "outputs": []}
            self.write_manifest(path, result)
            return result

    runner = FakeRunner(tmp_path)
    settings = Settings(sandbox_token="internal-test-token")
    app = create_manager(settings, runner)
    job = str(uuid4())
    spec = {"code": "print('ok')", "csv_base64": base64.b64encode(b"a\n1\n").decode()}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://manager") as client:
        assert (await client.post(f"/internal/jobs/{job}", json=spec)).status_code == 401
        auth = {"Authorization": "Bearer internal-test-token"}
        assert (
            await client.post(f"/internal/jobs/{job}", json=spec, headers=auth)
        ).status_code == 202
        await asyncio.sleep(0.01)
        assert (await client.get(f"/internal/jobs/{job}", headers=auth)).json()[
            "status"
        ] == "COMPLETED"
        changed = {**spec, "code": "print('changed')"}
        assert (
            await client.post(f"/internal/jobs/{job}", json=changed, headers=auth)
        ).status_code == 409
        assert (await client.get("/internal/jobs/not-a-uuid", headers=auth)).status_code == 422
