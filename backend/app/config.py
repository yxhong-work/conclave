from functools import lru_cache
from pydantic_settings import BaseSettings
from pydantic import model_validator


class Settings(BaseSettings):
    database_url: str
    llm_base_url: str = "http://10.241.77.188:8000/v1"
    llm_api_key: str = "probe"
    llm_model: str = ""
    llm_max_tokens: int = 8192
    llm_temperature: float = 0.3
    llm_timeout: float = 60.0
    sse_keepalive_s: int = 15
    deadline_scan_s: int = 5

    # --- Adaptive per-member questioning (SPEC docs/ADAPTIVE_SPEC.md §12.1) ---
    # Global hot kill-switch: off → P stage skipped, /my-question unified 404,
    # /state reports adaptive_questions=false (front-end falls back to anchor UI).
    adaptive_questions_enabled: bool = True
    # P-stage member gate: groups with more submitted members than this skip the
    # P stage entirely (§4.2 — bounds call count & concurrency for large groups).
    adaptive_p_max_members: int = 20

    # --- MCP research stage (SPEC MCP_SPEC §6.1) ---
    mcp_enabled: bool = False
    # csv subset of maps,apify_threads_post,realping (stable deployment aliases
    # for the direct tools in mcp_tools). webSearch is OpenAI-native and is not
    # routed through research_phase.
    mcp_providers: str = ""
    mcp_research_model: str = ""  # required when mcp_enabled
    mcp_required: bool = False  # treat research degrade as analysis failure
    mcp_max_rounds: int = 3  # v1.0 legacy (agentic rounds); unused in v1.1
    mcp_max_tool_calls: int = 3  # v1.1: max tool calls the planner may plan per run
    mcp_result_chars: int = 3000
    mcp_total_result_chars: int = 12000
    mcp_tool_timeout_s: float = 30.0
    mcp_connect_timeout_s: float = 10.0
    mcp_run_timeout_s: float = 180.0
    mcp_research_max_tokens: int = 1024
    mcp_allow_opinion_context: bool = False  # opt-out hatch for private opinion leakage
    mcp_log_verbose: bool = False

    # --- MCP provider credentials (optional; also covered by redact()) ---
    openai_api_key: str = ""       # real OpenAI key for web_search tool, distinct from llm_api_key
    apify_token: str = ""
    google_maps_api_key: str = ""
    realping_api_key: str = ""

    # --- ReasoningBank-Lite memory sidecar (SPEC docs in reasoningbank repo) ---
    rbank_enabled: bool = False
    rbank_base_url: str = "http://reasoningbank:8000"

    model_config = {"env_prefix": "", "case_sensitive": False}

    @model_validator(mode="after")
    def _validate_mcp(self):
        if self.mcp_enabled:
            if not self.mcp_research_model.strip():
                raise ValueError(
                "MCP_ENABLED=true requires MCP_RESEARCH_MODEL (a tool-calling-capable model)."
            )
            if not self.mcp_providers.strip():
                raise ValueError(
                "MCP_ENABLED=true requires MCP_PROVIDERS (csv subset of "
                "maps,apify_threads_post,realping)."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
