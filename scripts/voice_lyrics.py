"""Parse external-song lyric files and detect their language."""

import re

# A line that is entirely a bracket section tag, e.g. [verse 1], [Pre-Chorus], [].
SECTION_TAG = re.compile(r"^\s*\[[^\]]*\]\s*$")

_OPEN_PARENS = "（("
_CLOSE_PARENS = "）)"
_ALL_PARENS = _OPEN_PARENS + _CLOSE_PARENS


def _is_whole_line_parenthetical(line: str) -> bool:
    """True if the stripped line is wrapped in a single pair of parens.

    The pair may be full-width （...） or half-width (...), and the inner
    text must not contain another parenthesis (i.e. the line is exactly
    one annotation, not a lyric with parens inside).
    """
    if len(line) < 2:
        return False
    if line[0] not in _OPEN_PARENS or line[-1] not in _CLOSE_PARENS:
        return False
    inner = line[1:-1]
    return not any(ch in _ALL_PARENS for ch in inner)


def parse_voice_lyrics(text: str) -> list[str]:
    """Strip tags/annotations from raw lyric text, keep real lyric lines.

    Removes empty lines, any [...] section tags (case-insensitive by
    construction, covers numbered/unknown names), and whole-line
    （...）/(...) annotation lines. Surviving lines are stripped of
    leading/trailing whitespace but keep internal spacing.
    """
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if SECTION_TAG.match(line):
            continue
        if _is_whole_line_parenthetical(line):
            continue
        lines.append(line)
    return lines


def detect_language(lines: list[str]) -> str:
    """Return "zh" if any line contains a CJK character, else "en"."""
    for line in lines:
        for ch in line:
            if "一" <= ch <= "鿿":
                return "zh"
    return "en"
