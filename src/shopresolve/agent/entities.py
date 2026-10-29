"""Conservative lexical narrowing over authorized candidates, never LLM-invented IDs."""

import re


def narrow_candidates(candidates, mention):
    filtered = candidates
    if any(word in mention for word in ("没收到", "未收到", "不是", "不要", "除了")):
        # Negation/exclusion needs clarification, not a positive-keyword guess.
        return candidates
    explicit_ids = set(
        re.findall(
            r"(?<![a-zA-Z0-9-])(?:order-[a-z0-9-]+|"
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
            r"(?![a-zA-Z0-9-])",
            mention,
        )
    )
    if explicit_ids:
        # An explicit identifier narrows only the already-authorized candidate set.
        # Unknown IDs return no match, rather than falling back to every order.
        filtered = [row for row in filtered if row["id"] in explicit_ids]
    conditions = (
        (("未发货", "没发货", "待发货"), "PENDING_SHIPMENT"),
        (("已签收", "已收货", "收到", "收到了"), "DELIVERED"),
        (("已发货", "运输中"), "SHIPPED"),
    )
    for words, status in conditions:
        if any(word in mention for word in words):
            filtered = [row for row in filtered if row["status"] == status]
    # Product nouns are extracted from catalog labels, not a model's semantic guess.
    ignored = {"订单", "商品", "合成", "测试"}
    terms = {
        term
        for row in candidates
        for item in row["items"]
        for term in (
            re.findall(r"[a-zA-Z0-9]+", item["name"])
            + re.findall(r"(?=([\u4e00-\u9fff]{2}))", item["name"])
        )
        if term and term not in ignored and term in mention
    }
    if terms:
        filtered = [
            row
            for row in filtered
            if any(term in item["name"] for term in terms for item in row["items"])
        ]
    return filtered


def explicit_choice(text, candidates):
    """Only literal ID or an explicit ordinal; '好的' and '退两个' select nothing."""
    selection = re.fullmatch(
        r"\s*(?:(?:就选|选择|选)(?:订单)?\s*)?(order-[a-z0-9-]+)[。！!]?\s*", text
    )
    ids = [row["id"] for row in candidates if selection and row["id"] == selection.group(1)]
    if len(ids) == 1:
        return ids[0]
    match = re.fullmatch(
        r"\s*(?:就|选|选择)?第([一二三四五六七八九十]|\d{1,2})(?:个|笔|单)(?:吧)?[。！!]?\s*", text
    )
    if not match:
        return None
    number = match.group(1)
    ordinal = int(number) if number.isdecimal() else "一二三四五六七八九十".index(number) + 1
    return candidates[ordinal - 1]["id"] if 1 <= ordinal <= len(candidates) else None
