"""Current document-agent profile; historical benchmark configurations stay intact."""

from shopresolve.config import Settings

PRO_MODEL = "deepseek-v4-pro"


def pro_settings(**overrides):
    return Settings(
        **(
            {
                "provider": "deepseek",
                "model": PRO_MODEL,
                "model_base_url": "https://api.deepseek.com",
                "allow_paid_model": True,
                "model_max_output_tokens": 8192,
                "model_max_input_bytes": 120000,
                "model_timeout_seconds": 120,
                "active_budget_ms": 900000,
            }
            | overrides
        )
    )


def configure_document_payload(payload, settings):
    if settings.provider == "deepseek":
        if settings.model == PRO_MODEL:
            payload["thinking"] = {"type": "enabled"}
            payload["reasoning_effort"] = "high"
            payload["max_tokens"] = 8192  # Includes reasoning and final JSON.
            payload.pop("temperature", None)
        else:
            payload["thinking"] = {"type": "disabled"}  # Explicit legacy reproduction only.
    return payload


def cache_hit_rate(model):
    return {"deepseek-flash": 0.04, PRO_MODEL: 0.30}[model]
