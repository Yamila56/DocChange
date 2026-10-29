"""Fixed document checker over the existing authenticated Docker manager."""

import asyncio
import base64
import csv
import io
from uuid import NAMESPACE_URL, uuid5

import httpx

from shopresolve.errors import DomainError

CHECK_CODE = """import csv,json
files={}
for row in csv.DictReader(open('/input/data.csv',encoding='utf-8')):
    path=bytes.fromhex(row['path_hex']).decode('utf-8')
    line=bytes.fromhex(row['line_hex']).decode('utf-8')
    files.setdefault(path,[]).append(line)
findings=[]
for path,lines in files.items():
    fences=sum(line.lstrip().startswith('```') for line in lines)
    if fences%2: findings.append({'path':path,'code':'UNBALANCED_BACKTICK_FENCE'})
    if any('\\x00' in line for line in lines): findings.append({'path':path,'code':'NUL_BYTE'})
json.dump({'files_checked':len(files),'findings':findings,'arbitrary_code_executed':False},
          open('/output/document-check.json','w'),ensure_ascii=False)
"""


async def check_in_sandbox(change, settings):
    if not settings.sandbox_token:
        raise DomainError("SANDBOX_DISABLED", "Sandbox manager is not configured", 503)
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    writer.writerow(["path_hex", "line_hex"])
    for path, doc in change["documents"].items():
        for line in doc["content"].splitlines() or [""]:
            if len(line.encode()) > 2000:
                raise DomainError(
                    "CHECK_LIMIT", "Split lines longer than 2000 bytes before sandbox checks", 422
                )
            writer.writerow([path.encode().hex(), line.encode().hex()])
    job_id = str(uuid5(NAMESPACE_URL, "docchange:" + change["id"] + change["payload_hash"]))
    headers = {"Authorization": f"Bearer {settings.sandbox_token}"}
    url = settings.sandbox_url.rstrip("/") + "/internal/jobs/" + job_id
    try:
        async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
            response = await client.post(
                url,
                headers=headers,
                json={
                    "code": CHECK_CODE,
                    "csv_base64": base64.b64encode(buf.getvalue().encode()).decode(),
                },
            )
            response.raise_for_status()
            for _ in range(24):
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                result = response.json()
                if result["status"] in {"COMPLETED", "FAILED", "TIMED_OUT"}:
                    return {"job_id": job_id, "changeset_hash": change["payload_hash"], **result}
                await asyncio.sleep(1)
            return {"job_id": job_id, "status": "RUNNING"}
    except httpx.HTTPError as exc:
        raise DomainError(
            "SANDBOX_UNAVAILABLE", "Start the local sandbox manager, then retry", 503
        ) from exc
