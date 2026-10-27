from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SHOPRESOLVE_", env_file=".env", extra="ignore")
    database_url: str = "mysql+asyncmy://shopresolve:unset@127.0.0.1:13316/shopresolve"
    enable_demo: bool = False
    demo_code: str = ""
    redis_url: str = "redis://127.0.0.1:16379/0"
    provider: str = "demo"
    model: str = "demo-v1"
    model_base_url: str = ""
    model_api_key: str = ""
    allow_paid_model: bool = False
    model_limit: int = 12
    active_budget_ms: int = 120000
    model_timeout_seconds: int = 30
    model_max_output_tokens: int = Field(default=1500, ge=256, le=8192)
    model_max_input_bytes: int = Field(default=60000, ge=1024, le=200000)
    shutdown_grace_seconds: float = Field(default=10, gt=0, le=60)
    lease_seconds: int = 30
    queue_name: str = "shopresolve:runs"
    policy_corpus: str = "data/policies/demo.json"
    retrieval_mode: str = "bm25"
    enable_reranker: bool = False
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    reranker_model: str = "BAAI/bge-reranker-v2-m3-int8"
    model_cache_dir: str = ".cache/models"
    artifact_root: str = ".cache/artifacts"
    sandbox_root: str = ".cache/sandbox"
    sandbox_url: str = "http://127.0.0.1:18081"
    sandbox_token: str = ""
