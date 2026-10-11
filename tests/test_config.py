"""Settings: provider auto-detection and mock fallback (SPEC §6 №7, §7)."""

from __future__ import annotations

from debate_arena.config import Settings


def _settings(**overrides) -> Settings:
    base = dict(anthropic_api_key="", deepseek_api_key="", mock_llm=False)
    return Settings(_env_file=None, **(base | overrides))


def test_defaults_mirror_spec_section_7():
    s = _settings()
    assert s.model_analyst == "anthropic/claude-opus-5-5"
    assert s.model_judge == "anthropic/claude-opus-5-5"
    assert (s.default_rounds, s.max_rounds, s.max_text_chars) == (2, 3, 20_000)
    assert (s.turn_max_tokens, s.judge_max_tokens) == (6_000, 16_000)
    assert (s.analyst_effort, s.judge_effort) == ("medium", "high")
    assert s.deepseek_temperature == 0.3
    assert (s.judge_chat_effort, s.judge_chat_max_tokens) == ("medium", 6_000)
    assert (s.judge_chat_max_chars, s.judge_chat_max_messages) == (2_000, 20)
    assert s.judge_chat_timeout_s == 180.0
    assert (s.share_bucket, s.share_dir) == ("", "./shared")
    assert (s.share_token_bytes, s.share_cache_max_age_s) == (16, 60)


def test_no_keys_means_mock():
    assert _settings().use_mock is True


def test_forced_mock_wins_even_with_keys():
    assert _settings(anthropic_api_key="k", mock_llm=True).use_mock is True


def test_anthropic_key_enables_real_models():
    assert _settings(anthropic_api_key="k").use_mock is False


def test_missing_key_for_selected_provider_falls_back_to_mock():
    s = _settings(anthropic_api_key="k", model_analyst="deepseek/deepseek-chat")
    assert s.use_mock is True


def test_available_models_follow_keys():
    assert [m.id for m in _settings().available_models] == ["mock/rational"]
    ids = [m.id for m in _settings(anthropic_api_key="k", deepseek_api_key="k").available_models]
    assert "anthropic/claude-opus-5-5" in ids
    assert "deepseek/deepseek-chat" in ids
    assert "mock/rational" not in ids


def test_effective_models_in_mock_mode_point_to_mock():
    s = _settings()
    assert s.effective_model_analyst == "mock/rational"
    assert s.effective_model_judge == "mock/rational"


def test_tiny_token_budgets_fail_fast_with_a_readable_message():
    import pytest

    with pytest.raises(ValueError, match="TURN_MAX_TOKENS=450"):
        _settings(turn_max_tokens=450)
    with pytest.raises(ValueError, match="JUDGE_MAX_TOKENS=900"):
        _settings(judge_max_tokens=900)
    with pytest.raises(ValueError, match="JUDGE_CHAT_MAX_TOKENS=500"):
        _settings(judge_chat_max_tokens=500)
