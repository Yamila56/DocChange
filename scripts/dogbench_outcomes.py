"""Map runtime outcomes to the released DoGBench prediction contract."""


def runtime_result(decision):
    """The evaluation controller owns status, independently of any tool receipt."""
    fields = {key: value for key, value in decision.items() if key != "status"}
    return {**fields, "status": "completed"}


def official_prediction(row):
    prediction = {
        key: row[key]
        for key in ("instance_id", "status", "decision", "patch", "error")
        if key in row
    }
    if row.get("status") == "completed" and row.get("decision") == "unresolved":
        # Official DoGBench only accepts patch/abstain as completed decisions.
        # Encode unresolved as a zero-credit failure, preserving its distinction
        # from execution errors in metadata and in the local per-task result.
        return {
            "instance_id": row["instance_id"],
            "status": "error",
            "error": row.get("reason") or "Evidence was insufficient to resolve the task",
            "agent": {
                "semantic_decision": "unresolved",
                "reason_code": row.get("reason_code"),
                "terminal_phase": row.get("terminal_phase"),
            },
        }
    return prediction
