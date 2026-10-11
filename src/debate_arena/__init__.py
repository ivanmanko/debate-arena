"""Rational Check — three rational LLM analysts dissect a text, a judge issues a verdict."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("debate-arena")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0"
