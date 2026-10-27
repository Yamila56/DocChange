"""Read-only account preflight. No completion requests, no key/response-body logging."""

import asyncio
import json

import httpx

from shopresolve.config import Settings


async def main():
    settings = Settings()
    if not settings.model_api_key:
        raise SystemExit(
            "Configure SHOPRESOLVE_MODEL_API_KEY in local .env; never paste it in chat."
        )
    async with httpx.AsyncClient(
        base_url="https://api.deepseek.com",
        timeout=15,
        follow_redirects=False,
        trust_env=False,
        headers={"Authorization": f"Bearer {settings.model_api_key}"},
    ) as client:
        summary = {}
        for path in ("/user/balance", "/models"):
            try:
                response = await client.get(path)
                if response.status_code != 200:
                    summary[path] = {"http_status": response.status_code}
                    continue
                data = response.json()
                if path == "/models":
                    summary["models"] = [row["id"] for row in data.get("data", [])]
                else:
                    summary["balance"] = {
                        "is_available": data.get("is_available"),
                        "balances": [
                            {
                                key: row.get(key)
                                for key in (
                                    "currency",
                                    "total_balance",
                                    "granted_balance",
                                    "topped_up_balance",
                                )
                            }
                            for row in data.get("balance_infos", [])
                        ],
                    }
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                summary[path] = {"error_type": type(exc).__name__}
        print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
