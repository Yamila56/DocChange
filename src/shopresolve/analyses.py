import base64
from uuid import uuid4

import httpx
from sqlalchemy import select

from shopresolve.errors import DomainError
from shopresolve.models import Analysis
from shopresolve.service import CommerceService

COMPARISON_CODE = """import csv,json
rows=list(csv.DictReader(open('/input/data.csv',encoding='utf-8-sig')))
items=[]
for row in rows:
    price=int(row['price_cents']);quantity=int(row.get('quantity','1'))
    if price<0 or quantity<1: raise ValueError('invalid price or quantity')
    items.append({'name':row.get('name',''),'price_cents':price,'quantity':quantity})
json.dump({'items':items,'total_cents':sum(x['price_cents']*x['quantity'] for x in items)},
          open('/output/comparison.json','w'),ensure_ascii=False)
"""


class AnalysisService:
    def __init__(self, sessions, artifacts, settings):
        self.sessions, self.artifacts, self.settings = sessions, artifacts, settings
        self.commerce = CommerceService(sessions)

    async def submit(self, owner, artifact_id, key):
        await self.artifacts.read(owner, artifact_id)
        async with self.sessions.begin() as session:
            idem = await self.commerce._idem(
                session, owner, "analysis", key, {"artifact_id": artifact_id}
            )
            if idem.response is None:
                job = Analysis(id=str(uuid4()), owner_id=owner, artifact_id=artifact_id)
                session.add(job)
                idem.response = {"id": job.id, "status": "QUEUED"}
            result = idem.response
        await self._start(owner, result["id"])
        return result

    async def _job(self, owner, job_id):
        async with self.sessions() as session:
            job = await session.scalar(
                select(Analysis).where(Analysis.id == job_id, Analysis.owner_id == owner)
            )
            if job is None:
                raise DomainError("NOT_FOUND", "Resource not found", 404)
            return job

    async def _start(self, owner, job_id):
        job = await self._job(owner, job_id)
        _, content = await self.artifacts.read(owner, job.artifact_id)
        if not self.settings.sandbox_token:
            raise DomainError(
                "SANDBOX_DISABLED", "Configure the separate local sandbox manager", 503
            )
        try:
            async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
                response = await client.post(
                    f"{self.settings.sandbox_url}/internal/jobs/{job_id}",
                    headers={"Authorization": f"Bearer {self.settings.sandbox_token}"},
                    json={
                        "code": COMPARISON_CODE,
                        "csv_base64": base64.b64encode(content).decode(),
                    },
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise DomainError(
                "SANDBOX_UNAVAILABLE", "Manager unavailable; retry with the same key", 503
            ) from exc

    async def get(self, owner, job_id):
        await self._job(owner, job_id)
        if not self.settings.sandbox_token:
            raise DomainError("SANDBOX_DISABLED", "Sandbox manager disabled", 503)
        try:
            async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
                response = await client.get(
                    f"{self.settings.sandbox_url}/internal/jobs/{job_id}",
                    headers={"Authorization": f"Bearer {self.settings.sandbox_token}"},
                )
                if response.status_code == 404:
                    await self._start(owner, job_id)
                    return {"job_id": job_id, "status": "QUEUED"}
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            raise DomainError("SANDBOX_UNAVAILABLE", "Manager unavailable", 503) from exc
