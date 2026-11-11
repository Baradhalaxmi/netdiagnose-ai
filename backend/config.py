"""Application settings, loaded from environment variables and .env.

Variable names match .env.example (case-insensitive), e.g. DATABASE_URL.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "development"
    database_url: str = "postgresql+psycopg2://netdiag:netdiag@localhost:5432/netdiagnose"
    api_base_url: str = "http://localhost:8000"

    model_path: str = "ml/model.joblib"
    anomaly_threshold: float | None = None     # None -> use threshold in model_meta.json

    incident_window_minutes: int = 5
    incident_quiet_minutes: int = 10
    diagnosis_baseline_minutes: int = 60       # normal history used as the device baseline

    dataset_path: str = "data/raw/UNSW_NB15_testing-set.csv"   # attack flows for /simulator

    agent_enabled: bool = False
    agent_max_tool_calls: int = 6
    agent_effort: str = "medium"               # low | medium | high | xhigh | max
    llm_provider: str = "anthropic"
    llm_model: str = ""                        # empty -> agent.llm_client.DEFAULT_MODEL
    llm_api_key: str = ""                      # empty -> ANTHROPIC_API_KEY / `ant auth login`

    @field_validator("anomaly_threshold", mode="before")
    @classmethod
    def _empty_is_none(cls, v):
        return None if v in ("", None) else v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
