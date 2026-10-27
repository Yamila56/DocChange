from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SHOPRESOLVE_", env_file=".env", extra="ignore")
    database_url: str = "mysql+asyncmy://shopresolve:unset@127.0.0.1:13316/shopresolve"
    enable_demo: bool = False
    demo_code: str = ""
