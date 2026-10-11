"""The three rational analysts (ADR-002). Code-defined; lenses live in prompts.py."""

from __future__ import annotations

from .prompts import analyst_meta, analyst_system_prompt, lens_names
from .schemas import AnalystSpec

DEFAULT_LANGUAGE = "русский"


def build_analysts(language: str = DEFAULT_LANGUAGE) -> tuple[AnalystSpec, ...]:
    specs = []
    for name in lens_names():
        emoji, focus = analyst_meta(name)
        specs.append(
            AnalystSpec(
                name=name,
                emoji=emoji,
                focus=focus,
                system_prompt=analyst_system_prompt(name, language),
            )
        )
    return tuple(specs)


ANALYSTS: tuple[AnalystSpec, ...] = build_analysts()
