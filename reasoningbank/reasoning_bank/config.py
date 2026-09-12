"""Environment-based configuration for ReasoningBank-Lite.

Loads all settings from environment variables (12-factor, container-friendly).
Missing API keys resolve to empty strings so the service still starts and
fails open per-call rather than crashing on import.
"""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # Embedding (external OpenAI-compatible API)
    embedding_api_base: str
    embedding_api_key: str
    embedding_model: str
    # LLM (reuse the host agent's endpoint/key/model)
    llm_api_base: str
    llm_api_key: str
    llm_model: str
    llm_reasoning: str | None
    # Retrieval / gate / dedup
    retrieval_top_k: int
    similarity_threshold: float
    dedup_threshold: float
    reflection_temperature: float
    # Service
    data_dir: str
    log_level: str


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def load_settings() -> Settings:
    return Settings(
        embedding_api_base=_env("RB_EMBEDDING_API_BASE"),
        embedding_api_key=_env("RB_EMBEDDING_API_KEY"),
        embedding_model=_env("RB_EMBEDDING_MODEL"),
        llm_api_base=_env("RB_LLM_API_BASE"),
        llm_api_key=_env("RB_LLM_API_KEY"),
        llm_model=_env("RB_LLM_MODEL"),
        llm_reasoning=_env("RB_LLM_REASONING") or None,
        retrieval_top_k=int(_env("RB_RETRIEVAL_TOP_K", "3")),
        similarity_threshold=float(_env("RB_SIMILARITY_THRESHOLD", "0.70")),
        dedup_threshold=float(_env("RB_DEDUP_THRESHOLD", "0.95")),
        reflection_temperature=float(_env("RB_REFLECTION_TEMPERATURE", "0.0")),
        data_dir=_env("RB_DATA_DIR", "/data"),
        log_level=_env("RB_LOG_LEVEL", "INFO"),
    )


settings = load_settings()
