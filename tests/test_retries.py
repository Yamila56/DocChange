import pytest
from sqlalchemy.exc import OperationalError

from shopresolve.service import retry_local_transaction


async def test_deadlock_retry_is_bounded_and_keeps_arguments():
    calls = []

    @retry_local_transaction
    async def execute(operation_id):
        calls.append(operation_id)
        if len(calls) < 3:
            raise OperationalError("test", {}, Exception(1213, "synthetic deadlock"))
        return "ok"

    assert await execute("stable-id") == "ok"
    assert calls == ["stable-id"] * 3


async def test_persistent_deadlock_stops_after_three_attempts():
    calls = []

    @retry_local_transaction
    async def execute():
        calls.append(1)
        raise OperationalError("test", {}, Exception(1213, "synthetic deadlock"))

    with pytest.raises(OperationalError):
        await execute()
    assert len(calls) == 3


async def test_connection_loss_is_not_blindly_retried():
    calls = []

    @retry_local_transaction
    async def execute():
        calls.append(1)
        raise OperationalError("test", {}, Exception(2006, "server gone; result may be unknown"))

    with pytest.raises(OperationalError):
        await execute()
    assert len(calls) == 1
