"""Shared palette + theme-resolution tests (tools/tool_theme.py).

No DPG, no window — tool_theme is pure data plus the macOS detection probe.
"""

import sys
from pathlib import Path

# tool_theme lives one level up, in tools/
TOOLS = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(TOOLS))

import tool_theme  # noqa: E402


def test_light_and_dark_share_the_same_keys():
    # each tool loads a whole palette into its globals, so a missing key in one
    # would leave a stale colour behind on a toggle
    assert set(tool_theme.LIGHT) == set(tool_theme.DARK)


def test_explicit_choice_is_passthrough():
    # explicit choices never consult the system setting
    assert tool_theme.resolve_theme("light") == "light"
    assert tool_theme.resolve_theme("dark") == "dark"


def test_palette_selects_by_resolved_name():
    assert tool_theme.palette("light") is tool_theme.LIGHT
    assert tool_theme.palette("dark") is tool_theme.DARK


def test_colours_are_rgb_triples():
    for pal in (tool_theme.LIGHT, tool_theme.DARK):
        for name, rgb in pal.items():
            assert len(rgb) == 3, name
            assert all(0 <= c <= 255 for c in rgb), name


def test_detect_falls_back_to_light(monkeypatch):
    # any failure in the probe (non-macOS, missing binary, timeout) -> light
    def boom(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr(tool_theme.subprocess, "run", boom)
    assert tool_theme.detect_system_theme() == "light"
    assert tool_theme.resolve_theme("auto") == "light"
