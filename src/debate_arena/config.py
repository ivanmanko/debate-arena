"""Application settings (pydantic-settings). Values mirror SPEC §7 — change them there
in the same commit.

Model strings are ``provider/model`` (ADR-001). When the selected provider has no key the
app falls back to the mock engine and says so (SPEC §6 №7).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .domain.schemas import parse_model_ref

MOCK_MODEL = "mock/rational"

Effort = Literal["low", "medium", "high", "xhigh", "max"]

# Below these a reply is cut mid-sentence and the judge JSON never closes (SPEC §7):
# on Opus 5.5 thinking tokens count toward max_tokens.
MIN_TURN_MAX_TOKENS = 1024
MIN_JUDGE_MAX_TOKENS = 4096

# Catalogue shown in the UI model picker, per provider (only providers with a key).
_ANTHROPIC_MODELS = [
    ("anthropic/claude-opus-5-5", "Claude Opus 5.5"),
    ("anthropic/claude-sonnet-5-5", "Claude Sonnet 5.5"),
]
_DEEPSEEK_MODELS = [
    ("deepseek/deepseek-chat", "DeepSeek V3 (chat)"),
    ("deepseek/deepseek-reasoner", "DeepSeek R1 (reasoner)"),
]


class ModelOption(BaseModel):
    id: str
    label: str
    provider: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        protected_namespaces=(),
    )

    # --- Providers (ADR-001) ---
    anthropic_api_key: str = ""
    deepseek_api_key: str = ""
    model_analyst: str = "anthropic/claude-opus-5-5"
    model_judge: str = "anthropic/claude-opus-5-5"
    # Opus 5.5: thinking is always on; effort is the only depth control (SPEC §7).
    analyst_effort: Effort = "medium"
    judge_effort: Effort = "high"
    deepseek_temperature: float = 0.3
    # Mirrors the Anthropic SDK default of 2 retries; DeepSeek goes through plain httpx.
    deepseek_max_retries: int = 2
    # Reasoner replies can take minutes.
    deepseek_timeout_s: float = 600.0
    mock_llm: bool = False

    # --- Analysis shape (SPEC §7) ---
    default_rounds: int = 2
    max_rounds: int = 3
    max_text_chars: int = 20_000
    max_question_chars: int = 500
    turn_max_tokens: int = 6_000
    judge_max_tokens: int = 16_000
    llm_concurrency: int = 3
    language: str = "русский"

    # --- Storage / web / bot ---
    database_url: str = "sqlite+aiosqlite:///./debate_arena.db"
    web_host: str = "127.0.0.1"
    web_port: int = 8000
    sse_heartbeat_s: float = 15.0
    history_limit: int = 50
    title_chars: int = 80
    # Finished runs kept in memory for SSE replay before falling back to the DB.
    runs_kept_in_memory: int = 50
    # auto mode: share of speaker lines needed to treat the input as a dialogue (SPEC §4.0)
    dialogue_line_share: float = 0.6
    # auto mode: this many messages from ≥ 2 speakers is a dialogue even with long bodies
    dialogue_min_messages: int = 4
    # Real participant names never reach the model or the DB (SPEC §4.0b).
    anonymize_participants: bool = True
    bot_token: str = ""
    # Shown in bot replies so a chat user can open the full verdict in the web UI.
    public_url: str = ""

    @field_validator("model_analyst", "model_judge")
    @classmethod
    def _valid_model_ref(cls, value: str) -> str:
        parse_model_ref(value)  # raises ValueError with a readable message
        return value

    @model_validator(mode="after")
    def _token_budgets_are_sane(self) -> Settings:
        if self.turn_max_tokens < MIN_TURN_MAX_TOKENS:
            raise ValueError(
                f"TURN_MAX_TOKENS={self.turn_max_tokens} is below {MIN_TURN_MAX_TOKENS}: replies "
                "would be cut mid-sentence (thinking counts toward the limit). Remove the legacy "
                "line from .env or raise it."
            )
        if self.judge_max_tokens < MIN_JUDGE_MAX_TOKENS:
            raise ValueError(
                f"JUDGE_MAX_TOKENS={self.judge_max_tokens} is below {MIN_JUDGE_MAX_TOKENS}: the "
                "judge's JSON verdict would be truncated. Remove the legacy line from .env or "
                "raise it."
            )
        return self

    def has_key(self, provider: str) -> bool:
        """Whether the provider can be used: has an API key, or needs none (mock)."""
        return {
            "anthropic": bool(self.anthropic_api_key),
            "deepseek": bool(self.deepseek_api_key),
            "mock": True,
        }.get(provider, False)

    @property
    def use_mock(self) -> bool:
        """Mock if forced, or if either selected model's provider has no key."""
        if self.mock_llm:
            return True
        providers = (
            parse_model_ref(self.model_analyst).provider,
            parse_model_ref(self.model_judge).provider,
        )
        return not all(self.has_key(p) for p in providers)

    @property
    def effective_model_analyst(self) -> str:
        return MOCK_MODEL if self.use_mock else self.model_analyst

    @property
    def effective_model_judge(self) -> str:
        return MOCK_MODEL if self.use_mock else self.model_judge

    @property
    def available_models(self) -> list[ModelOption]:
        """Models the UI may offer: real ones for keyed providers, else the mock."""
        if self.mock_llm:
            return [ModelOption(id=MOCK_MODEL, label="Mock (без ключей)", provider="mock")]
        options: list[ModelOption] = []
        if self.anthropic_api_key:
            options += [
                ModelOption(id=i, label=n, provider="anthropic") for i, n in _ANTHROPIC_MODELS
            ]
        if self.deepseek_api_key:
            options += [
                ModelOption(id=i, label=n, provider="deepseek") for i, n in _DEEPSEEK_MODELS
            ]
        return options or [ModelOption(id=MOCK_MODEL, label="Mock (без ключей)", provider="mock")]


@lru_cache
def get_settings() -> Settings:
    return Settings()
