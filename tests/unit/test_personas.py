from debate_arena.debate.schemas import PersonaSpec
from debate_arena.storage.seed import MANIFEST, default_personas


def test_three_default_personas():
    personas = default_personas()
    assert len(personas) == 3
    names = {p["name"] for p in personas}
    assert names == {"Ницше", "Поппер", "Сократ"}


def test_each_persona_carries_the_manifest():
    # The character must be manifesto-bound: a chunk of the manifest in every backstory.
    marker = "БРИТВА ОККАМА"
    for p in default_personas():
        assert marker in p["system_principles"]
        assert MANIFEST.splitlines()[0] in p["system_principles"]


def test_personas_have_a_model_each():
    # Each persona must resolve to a model string; whether they share a model or
    # use different providers is a deployment choice (driven by config/.env).
    models = [p["model"] for p in default_personas()]
    assert all(m for m in models)
    assert len(models) == 3


def test_persona_spec_roundtrips_from_seed():
    spec = PersonaSpec(**default_personas()[0])
    assert spec.emoji
    assert spec.role
    assert 0.0 <= spec.temperature <= 2.0
