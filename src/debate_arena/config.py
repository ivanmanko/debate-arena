"""Application configuration via pydantic-settings.

All knobs live here: provider keys, round/length limits, pacing, and the
default per-persona LiteLLM model strings. Read once into a cached `settings`.
"""

from __future__ import annotations

from functools import lru_cache

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env into the real process environment so that vars we don't model as
# Settings fields — e.g. OLLAMA_API_BASE, which LiteLLM reads straight from the
# environment — also take effect. This makes .env the single source of truth.
load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
    )

    # --- Telegram ---
    bot_token: str = Field(default="", alias="BOT_TOKEN")

    # --- Storage ---
    # Defaults to a local SQLite file so the project runs with zero infra.
    # docker-compose overrides this with a Postgres async URL.
    database_url: str = Field(
        default="sqlite+aiosqlite:///./debate_arena.db",
        alias="DATABASE_URL",
    )

    # --- Provider API keys (consumed by LiteLLM inside CrewAI) ---
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    # --- Default models per slot (LiteLLM strings). Persona rows may override. ---
    # Cheap, fast models — one voice per provider so the personas sound different.
    model_radical: str = Field(default="deepseek/deepseek-chat", alias="MODEL_RADICAL")
    model_skeptic: str = Field(default="gemini/gemini-2.0-flash", alias="MODEL_SKEPTIC")
    model_questioner: str = Field(default="openai/gpt-4o-mini", alias="MODEL_QUESTIONER")
    model_judge: str = Field(default="openrouter/anthropic/claude-3.5-haiku", alias="MODEL_JUDGE")

    # --- Debate shape ---
    max_rounds: int = Field(default=3, alias="MAX_ROUNDS")
    max_personas: int = Field(default=3, alias="MAX_PERSONAS")
    # 450 leaves headroom for 1-2 paragraphs in Russian, where Cyrillic BPE tokens
    # run ~1.5-2x denser than Latin text — too tight a budget cuts speech mid-word.
    turn_max_tokens: int = Field(default=450, alias="TURN_MAX_TOKENS")
    judge_max_tokens: int = Field(default=900, alias="JUDGE_MAX_TOKENS")
    judge_temperature: float = Field(default=0.3, alias="JUDGE_TEMPERATURE")
    # Language the personas and judge must write in. Any name works: русский,
    # English, Deutsch, … — it's injected verbatim into the prompts.
    language: str = Field(default="русский", alias="LANGUAGE")

    # --- Showmanship / pacing (seconds) ---
    pause_between_turns: float = Field(default=2.2, alias="PAUSE_BETWEEN_TURNS")
    pause_after_header: float = Field(default=1.2, alias="PAUSE_AFTER_HEADER")
    typing_per_turn: float = Field(default=1.6, alias="TYPING_PER_TURN")

    # --- Behaviour flags ---
    # When true, skip CrewAI/LiteLLM entirely and use canned turns. Lets the bot
    # and tests run with no API keys. Auto-enabled if no provider keys are set.
    mock_llm: bool = Field(default=False, alias="MOCK_LLM")

    @property
    def has_any_provider_key(self) -> bool:
        return any(
            [
                self.openai_api_key,
                self.openrouter_api_key,
                self.deepseek_api_key,
                self.gemini_api_key,
                self.anthropic_api_key,
            ]
        )

    # Provider prefixes that run locally and need no API key (e.g. Ollama).
    _KEYLESS_PREFIXES = ("ollama", "ollama_chat", "local", "lm_studio")

    @property
    def uses_keyless_provider(self) -> bool:
        models = (
            self.model_radical,
            self.model_skeptic,
            self.model_questioner,
            self.model_judge,
        )
        return any(m.split("/", 1)[0] in self._KEYLESS_PREFIXES for m in models)

    @property
    def use_mock(self) -> bool:
        """Use the mock engine if explicitly asked, or if there's no way to call a real LLM.

        A local keyless provider (Ollama) counts as "can call a real LLM" even
        with no API keys set.
        """
        return self.mock_llm or not (self.has_any_provider_key or self.uses_keyless_provider)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
