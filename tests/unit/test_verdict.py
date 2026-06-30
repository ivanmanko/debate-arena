import pytest
from pydantic import ValidationError

from debate_arena.bot.presenter import render_standings
from debate_arena.debate.schemas import PersonaScore, PersonaSpec, Verdict


def _spec(name, emoji="🔥"):
    return PersonaSpec(name=name, emoji=emoji, role="r", system_principles="p", model="m/x")


def _score(name, **kw):
    base = dict(logic=8, evidence=7, rhetoric=6, responsiveness=5)
    base.update(kw)
    return PersonaScore(persona=name, note="ok", **base)


def test_persona_score_total():
    assert _score("X", logic=10, evidence=10, rhetoric=10, responsiveness=10).total == 40


def test_verdict_card_marks_winner_and_bars():
    from debate_arena.bot.presenter import TelegramPresenter

    verdict = Verdict(
        scores=[_score("Ницше", logic=9), _score("Поппер", logic=4)],
        winner="Ницше",
        punchline="Воля победила осторожность.",
    )
    personas = [_spec("Ницше", "🔥"), _spec("Поппер", "🧊")]
    card = TelegramPresenter.render_verdict_card(verdict, personas)

    assert "ВЕРДИКТ СУДЬИ" in card
    assert "👑" in card  # winner crown
    assert "🏆 Победитель" in card
    assert "Воля победила осторожность." in card
    assert "█" in card and "░" in card  # ascii score bars


def test_verdict_rejects_out_of_range_scores():
    with pytest.raises(ValidationError):
        PersonaScore(persona="X", logic=11, evidence=5, rhetoric=5, responsiveness=5, note="!")


def test_standings_empty_when_no_debates():
    class FakePersona:
        debates = 0
        wins = 0
        total_score = 0
        emoji = "🔥"
        name = "Ницше"

    text = render_standings([FakePersona()])
    assert "Пока ни одного дебата" in text
