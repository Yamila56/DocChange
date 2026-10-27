import pytest

from shopresolve.artifacts import ArtifactStore, validate_csv
from shopresolve.errors import DomainError


def test_formula_in_header_is_also_rejected():
    with pytest.raises(DomainError):
        validate_csv(b"=CMD(),price\nx,1\n")


@pytest.mark.parametrize(
    "content", [b"", b"a,a\n1,2\n", b"a\n1,2\n", b"a\n=CMD()\n", b"a\n\x00\n", b"a\n\xff\n"]
)
def test_invalid_or_unsafe_csv_rejected(content):
    with pytest.raises(DomainError):
        validate_csv(content)


async def test_artifact_owner_hash_and_request_replay(commerce, tmp_path):
    store = ArtifactStore(commerce[1], tmp_path / "artifacts")
    content = b"price_cents,quantity\n19900,1\n"
    first = await store.upload("alice", "prices.csv", content, "key")
    assert await store.upload("alice", "prices.csv", content, "key") == first
    name, restored = await store.read("alice", first["id"])
    assert name == "prices.csv" and restored == content
    with pytest.raises(DomainError) as error:
        await store.read("bob", first["id"])
    assert error.value.status == 404
    with pytest.raises(DomainError):
        await store.upload("alice", "prices.csv", b"a\n1\n", "key")
    with pytest.raises(DomainError):
        store.path("../../.env")


async def test_corrupt_artifact_is_not_silently_used(commerce, tmp_path):
    store = ArtifactStore(commerce[1], tmp_path / "artifacts")
    first = await store.upload("alice", "prices.csv", b"a\n1\n", "key")
    for file in store.root.iterdir():
        file.write_bytes(b"a\n2\n")
    with pytest.raises(DomainError) as error:
        await store.read("alice", first["id"])
    assert error.value.code == "ARTIFACT_INTEGRITY"
