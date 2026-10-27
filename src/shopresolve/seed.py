"""Deterministic synthetic fixtures. No personal data and no destructive reset."""

import time

from sqlalchemy import select

from shopresolve.models import Order, OrderItem, Policy
from shopresolve.service import digest


async def seed(sessions, now=None):
    now = int(time.time()) if now is None else now
    async with sessions.begin() as session:
        if await session.scalar(select(Order.id).limit(1)) is not None:
            raise RuntimeError("Seed requires an empty orders table; no existing data was changed")
        rules = {
            "return_days": 7,
            "excluded_categories": ["personal_care"],
            "document": "模拟政策 v1：签收后七天内可申请退货；个人护理品转人工。"
            "待发货订单可取消；退货申请并不等于已退款。",
        }
        session.add(
            Policy(version="demo-policy-v1", active=True, content_hash=digest(rules), **rules)
        )
        specs = [
            ("order-pending", "alice", "PENDING_SHIPMENT", None, 19900),
            ("order-delivered", "alice", "DELIVERED", now - 86400, 39800),
            ("order-shipped", "alice", "SHIPPED", None, 19900),
            ("order-expired", "alice", "DELIVERED", now - 9 * 86400, 19900),
            ("order-excluded", "alice", "DELIVERED", now - 86400, 19900),
            ("order-bob", "bob", "PENDING_SHIPMENT", None, 19900),
        ]
        for oid, owner, status, delivered, paid in specs:
            session.add(
                Order(
                    id=oid,
                    owner_id=owner,
                    status=status,
                    delivered_at=delivered,
                    paid_cents=paid,
                    refunded_cents=0,
                    version=1,
                    currency="CNY",
                )
            )
        await session.flush()
        for oid, _, _, _, paid in specs:
            session.add(
                OrderItem(
                    id=f"item-{oid.removeprefix('order-')}",
                    order_id=oid,
                    name="合成测试耳机" if oid != "order-excluded" else "合成护理商品",
                    category="audio" if oid != "order-excluded" else "personal_care",
                    quantity=paid // 19900,
                    unit_paid_cents=19900,
                    reserved_return_quantity=0,
                )
            )
