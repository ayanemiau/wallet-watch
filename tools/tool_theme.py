"""Shared palette + macOS light/dark detection for the DearPyGui tools.

Pure data (RGB int-lists) plus one subprocess probe — no DPG, no CLI, no lib/
imports. Both tools/rule_editor/editor.py and tools/review_approver/approver.py
import this so the Claude-ish palette lives in exactly one place (it used to be
copy-pasted between the two, kept in sync by a hand comment).

Each tool loads a palette dict into its module globals (the bare names BG,
SURFACE, ... that its theme builders and inline `color=` args reference), so the
key set here is the union of what either tool uses (rule_editor's DANGER and
approver's OK).
"""
import subprocess


def _rgb(value):
    value = value.lstrip("#")
    return [int(value[i:i + 2], 16) for i in (0, 2, 4)]


# Claude-ish warm LIGHT palette — the current look, values unchanged.
LIGHT = {
    "BG": _rgb("#FAF9F5"), "SURFACE": _rgb("#FFFFFF"), "TEXT": _rgb("#3D3929"),
    "MUTED": _rgb("#83827D"), "ACCENT": _rgb("#D97757"),
    "ACCENT_ACTIVE": _rgb("#C4623F"), "BORDER": _rgb("#E5E4DF"),
    "DANGER": _rgb("#B54A34"), "OK": _rgb("#617A5C"), "WHITE": [255, 255, 255],
    "TABLE_HEADER": _rgb("#EAE8E1"),
}

# Warm DARK palette — same coral accent, warm (not cold-grey) neutrals. The
# table-header relationship inverts vs light: on dark the header is *lighter*
# than the rows, so it still reads as a header.
DARK = {
    "BG": _rgb("#262624"), "SURFACE": _rgb("#30302E"), "TEXT": _rgb("#ECEAE1"),
    "MUTED": _rgb("#9A9992"), "ACCENT": _rgb("#D97757"),
    "ACCENT_ACTIVE": _rgb("#E08A6C"), "BORDER": _rgb("#3A3A37"),
    "DANGER": _rgb("#E5654A"), "OK": _rgb("#7C9B76"), "WHITE": [255, 255, 255],
    "TABLE_HEADER": _rgb("#3E3E3B"),
}


def detect_system_theme():
    """Return "dark" or "light" from the macOS global setting.

    `defaults read -g AppleInterfaceStyle` prints "Dark" in dark mode and exits
    non-zero (the key is absent) in light mode. Any failure — light mode,
    non-macOS, timeout — falls back to "light".
    """
    try:
        out = subprocess.run(
            ["defaults", "read", "-g", "AppleInterfaceStyle"],
            capture_output=True, text=True, timeout=2,
        )
        return "dark" if out.stdout.strip() == "Dark" else "light"
    except Exception:
        return "light"


def resolve_theme(choice):
    """"auto" -> detect; "light"/"dark" -> passthrough. Always a concrete name."""
    return detect_system_theme() if choice == "auto" else choice


def palette(choice):
    """The palette dict for a choice ("auto" | "light" | "dark")."""
    return DARK if resolve_theme(choice) == "dark" else LIGHT
