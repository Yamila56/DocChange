"""Deterministic scope/coverage checks, NOT a universal semantic entailment model."""

from shopresolve.service import digest

SECTION_QUERIES = {
    "cancel-pending": "待发货取消 条件 批准 模拟退款",
    "cancel-shipped": "已经发货不能取消 SHIPPED",
    "return-window": "退货七天 签收时限",
    "return-excluded": "个人护理 品类例外",
    "return-request-state": "退货申请不等于退款",
    "return-quantity": "部分退货 数量 可退数量",
    "approval-binding": "明确批准 绑定提案",
    "currency-unit": "金额单位 整数分 币种",
    "refund-cap": "退款金额上限 实付金额 模拟退款",
    "return-undelivered": "未签收 运输中 不允许退货申请",
    "policy-version": "政策版本 旧政策 生效",
}


def required_sections(goal):
    text = " ".join(goal.get("mentions", []))
    intent = goal["intent"]
    required = set()
    if intent == "CANCEL_ORDER" or "取消" in text:
        required.add("cancel-pending")
        if "已发货" in text or "发货后" in text:
            required.add("cancel-shipped")
    if intent == "RETURN_REQUEST" or "退货" in text:
        required.update({"return-window", "return-excluded", "return-request-state"})
        if any(word in text for word in ("数量", "部分", "几件", "一件")):
            required.add("return-quantity")
        if any(word in text for word in ("未签收", "没收到", "运输中", "未收货")):
            required.add("return-undelivered")
    if "退款" in text and not required:
        required.update({"refund-cap", "return-request-state"})
    if any(word in text for word in ("金额", "上限", "多少钱")):
        required.add("refund-cap")
    if any(word in text for word in ("批准", "确认", "同意")):
        required.add("approval-binding")
    if any(word in text for word in ("单位", "币种")):
        required.add("currency-unit")
    if "政策版本" in text or "旧政策" in text:
        required.add("policy-version")
    # These subjects have no implemented policy bundle. Never infer an answer.
    gap = any(
        word in text
        for word in ("发票", "保修", "保险", "运费", "跨境", "会员", "赔偿", "积分", "优惠券")
    ) or ("到账" in text and any(word in text for word in ("多久", "几天", "什么时候")))
    return required, gap


def needs_order_check(goal):
    text = " ".join(goal.get("mentions", []))
    return (
        goal["mode"] == "INQUIRE"
        and goal["intent"] in {"RETURN_REQUEST", "CANCEL_ORDER"}
        and (
            "order_id" in goal.get("slots", {})
            or any(
                word in text
                for word in (
                    "这笔",
                    "那笔",
                    "这件",
                    "那个",
                    "这单",
                    "那单",
                    "我的订单",
                    "我的耳机",
                    "收到的",
                )
            )
        )
    )


def policy_goal_complete(goal):
    assessment = goal.get("policy_assessment", {})
    if not assessment.get("supported"):
        return False
    if not needs_order_check(goal):
        return True
    order = goal.get("order_observation")
    eligibility = goal.get("eligibility_observation")
    return bool(
        order
        and eligibility
        and eligibility["order_id"] == order["id"]
        and eligibility["order_version"] == order["version"]
        and assessment.get("versions") == [eligibility["policy_version"]]
    )


def fallback_sections(document):
    sections = []
    for fragment, section in (
        ("待发货订单可取消", "cancel-pending"),
        ("七天", "return-window"),
        ("个人护理", "return-excluded"),
        ("申请并不等于已退款", "return-request-state"),
    ):
        if fragment in document:
            sections.append(section)
    return sections


def assess(goal, evidence):
    required, unsupported = required_sections(goal)
    valid = [e for e in evidence if e.get("content_hash") == digest(e.get("quote", ""))]
    covered = {section for e in valid for section in e.get("policy_sections", [])}
    missing = required - covered
    return {
        "method": "deterministic-scope-coverage-v1-not-semantic-entailment",
        "required_sections": sorted(required),
        "missing_sections": sorted(missing),
        "supported": bool(required) and not missing and not unsupported,
        "unsupported_topic": unsupported,
        "evidence_ids": [e["id"] for e in valid if set(e.get("policy_sections", [])) & required],
        "versions": sorted({e["version"] for e in valid if e.get("version")}),
    }
