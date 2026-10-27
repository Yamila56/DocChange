import base64
import json
from uuid import uuid4

import pytest

from shopresolve.sandbox.runner import DockerSandbox, SandboxError


@pytest.fixture
async def runner(tmp_path):
    runner = DockerSandbox(tmp_path / "sandbox", timeout=5)
    try:
        rc, _, _ = await runner.command("image", "inspect", "shopresolve-sandbox:v1")
        if rc:
            pytest.skip("Trusted sandbox image is not built")
    except (OSError, TimeoutError):
        pytest.skip("Docker unavailable")
    return runner


async def test_sandbox_csv_and_same_job_replay(runner):
    job_id = str(uuid4())
    code = """import csv,json
rows=list(csv.DictReader(open('/input/data.csv')))
result={'total_cents':sum(int(r['price_cents'])*int(r['quantity']) for r in rows)}
json.dump(result,open('/output/result.json','w'))
"""
    content = b"price_cents,quantity\n19900,2\n5000,1\n"
    result = await runner.run(job_id, code, content)
    assert result["status"] == "COMPLETED", result
    output = result["outputs"][0]
    assert json.loads(base64.b64decode(output["base64"])) == {"total_cents": 44800}
    assert await runner.run(job_id, code, content) == result
    with pytest.raises(SandboxError, match="different inputs"):
        await runner.run(job_id, code + "\nprint('different')", content)


async def test_sandbox_has_no_network_keys_or_host_files(runner):
    code = """import os,socket,json
result={'has_key':bool(os.getenv('SHOPRESOLVE_MODEL_API_KEY')),
        'host_env_exists':os.path.exists('/input/../.env'),
        'docker_socket':os.path.exists('/var/run/docker.sock')}
try:
    socket.create_connection(('1.1.1.1',443),timeout=1)
    result['network']=True
except OSError:
    result['network']=False
json.dump(result,open('/output/isolation.json','w'))
"""
    result = await runner.run(str(uuid4()), code, b"x\n1\n")
    assert result["status"] == "COMPLETED", result
    data = json.loads(base64.b64decode(result["outputs"][0]["base64"]))
    assert data == {
        "has_key": False,
        "host_env_exists": False,
        "docker_socket": False,
        "network": False,
    }


async def test_sandbox_wall_timeout(runner):
    result = await runner.run(str(uuid4()), "while True: pass", b"x\n1\n")
    assert result["status"] == "TIMED_OUT", result


async def test_symlink_output_is_rejected(runner):
    code = "import os;os.symlink('/etc/passwd','/output/leak.txt')"
    with pytest.raises(SandboxError, match="Non-regular"):
        await runner.run(str(uuid4()), code, b"x\n1\n")


def test_workspace_traversal_rejected(tmp_path):
    with pytest.raises((ValueError, SandboxError)):
        DockerSandbox(tmp_path).folder("../../other-project")
