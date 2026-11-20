"""``[[wiki-link]]`` parsing and rendering."""

from __future__ import annotations

import re
from urllib.parse import quote, unquote

# [[Target]], [[Target|Alias]], [[Target#Heading|Alias]]
WIKILINK_RE = re.compile(r"\[\[([^\[\]|#\n]+)(?:#([^\[\]|\n]*))?(?:\|([^\[\]\n]+))?\]\]")
NOTE_LINK_SCHEME = "note:"


def extract_links(body: str) -> list[str]:
    """Unique link targets in order of appearance."""
    targets: list[str] = []
    seen: set[str] = set()
    for match in WIKILINK_RE.finditer(body):
        target = match.group(1).strip()
        key = target.lower()
        if target and key not in seen:
            seen.add(key)
            targets.append(target)
    return targets


def wikilinks_to_markdown(body: str) -> str:
    """Rewrite wiki-links as markdown links with a ``note:`` URL for the preview pane."""

    def _sub(match: re.Match[str]) -> str:
        target = match.group(1).strip()
        label = (match.group(3) or target).strip().replace("]", "\\]")
        return f"[{label}]({NOTE_LINK_SCHEME}{quote(target)})"

    return WIKILINK_RE.sub(_sub, body)


def target_from_url(url: str) -> str | None:
    if not url.startswith(NOTE_LINK_SCHEME):
        return None
    return unquote(url[len(NOTE_LINK_SCHEME) :])
