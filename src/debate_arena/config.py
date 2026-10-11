"""Application settings (pydantic-settings). Values mirror SPEC §7 — change them there
in the same commit.

Model strings are ``provider/model`` (ADR-001). When the selected provider has no key the
app falls back to the mock engine and says so (SPEC §6 №7).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

from .domain.schemas import parse_model_ref

MOCK_MODEL = "mock/rational"

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
    analyst_effort: str = "medium"
    judge_effort: str = "high"
    deepseek_temperature: float = 0.3
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
    bot_token: str = ""
    # Shown in bot replies so a chat user can open the full verdict in the web UI.
    public_url: str = ""

    def has_key(self, provider: str) -> bool:
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
