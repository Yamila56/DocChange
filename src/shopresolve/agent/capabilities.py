"""One server-owned capability manifest, shared by context and runtime guards."""

SUPPORTED_INTENTS = frozenset(
    {
        "ORDER_QUERY",
        "SHIPMENT_QUERY",
        "POLICY_QUERY",
        "RETURN_REQUEST",
        "CANCEL_ORDER",
        "AMBIGUOUS",
    }
)


def manifest():
    return {
        "version": "commerce-capabilities-v1",
        "supported_intents": sorted(SUPPORTED_INTENTS),
        "unsupported_intents": ["REPLACEMENT_SEARCH", "COMPARE_PRODUCTS", "HUMAN_ESCALATION"],
        "shipment_scope": "Order dispatch state only; no carrier event timeline or ETA",
        "sandbox_scope": "Standalone CSV workbench; not an agent tool yet",
        "writes": "Simulated cancellation/refund or return request; explicit approval only",
    }
