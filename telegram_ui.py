"""Shared visual system for Telegram bot messages.

Raw text is escaped before HTML formatting. Dynamic market/team/player names are safe.
"""
import html
import re
from urllib.parse import urlsplit

SEPARATOR = "━━━━━━━━━━━━━━━━━━━━"
MAX_RAW_CHUNK = 3000

_LABEL_RE = re.compile(r"^(\s*)([^:：]{2,42})(:|：)(\s*)(.*)$")
_URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)


def esc(value):
    return html.escape(str(value if value is not None else "—"), quote=True)


def _is_separator(line):
    stripped = line.strip()
    return bool(stripped) and len(stripped) >= 5 and all(c in "━─—-_*· " for c in stripped)


def _is_heading(line):
    stripped = line.strip()
    if not stripped or len(stripped) > 76:
        return False
    letters = [c for c in stripped if c.isalpha()]
    if len(letters) < 3:
        return False
    # Headings such as "📈 FORMA E CAMPANHA" or "RENTABILIDADE SIMULADA".
    return sum(c.isupper() for c in letters) / len(letters) >= 0.82


def _safe_url(url):
    candidate = url.rstrip(".,);]")
    parts = urlsplit(candidate)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None, url
    return candidate, url[len(candidate):]


def _format_line(line, first=False):
    if not line.strip():
        return ""
    if _is_separator(line):
        return f"<code>{esc(SEPARATOR)}</code>"

    stripped = line.strip()
    if first:
        return f"<b>{esc(stripped)}</b>"
    if _is_heading(stripped):
        return f"\n<b>{esc(stripped)}</b>"

    # Keep any visible URL usable, but never trust raw HTML from external data.
    match = _URL_RE.search(line)
    if match:
        url, trailing = _safe_url(match.group(0))
        if url:
            before = esc(line[:match.start()])
            after = esc(line[match.end():])
            link = f'<a href="{esc(url)}">Abrir link ↗</a>'
            return f"{before}{link}{esc(trailing)}{after}"

    command = re.match(r"^(\s*)(/[a-z0-9_]+)(\s+.*)?$", line, re.IGNORECASE)
    if command:
        indent, name, rest = command.groups()
        return f"{esc(indent)}<code>{esc(name)}</code>{esc(rest or '')}"

    label = _LABEL_RE.match(line)
    if label:
        indent, key, colon, spacing, value = label.groups()
        return (
            f"{esc(indent)}<b>{esc(key)}{esc(colon)}</b>"
            f"{esc(spacing)}{esc(value)}"
        )

    return esc(line)


def render_message(text, first_chunk=True):
    """Convert plain bot copy to consistently styled, safe Telegram HTML."""
    lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    output = []
    first = first_chunk
    for line in lines:
        if line.strip():
            output.append(_format_line(line, first=first))
            first = False
        else:
            output.append("")
    # Avoid excessive blank space while preserving paragraph hierarchy.
    rendered = "\n".join(output)
    rendered = re.sub(r"\n{3,}", "\n\n", rendered)
    return rendered.strip()


def split_message(text, max_chars=MAX_RAW_CHUNK):
    """Split long plain messages at line boundaries before HTML expansion."""
    raw = str(text or "")
    if len(raw) <= max_chars:
        return [raw]
    chunks = []
    current = []
    current_len = 0
    for line in raw.splitlines():
        if len(line) > max_chars:
            if current:
                chunks.append("\n".join(current))
                current, current_len = [], 0
            for start in range(0, len(line), max_chars):
                chunks.append(line[start:start + max_chars])
            continue
        added = len(line) + (1 if current else 0)
        if current and current_len + added > max_chars:
            chunks.append("\n".join(current))
            current, current_len = [line], len(line)
        else:
            current.append(line)
            current_len += added
    if current:
        chunks.append("\n".join(current))
    return chunks or [""]


def keyboard_url(url, label="Abrir mercado"):
    candidate, _ = _safe_url(str(url or ""))
    if not candidate:
        return None
    return {
        "inline_keyboard": [[{
            "text": str(label)[:64],
            "url": candidate,
        }]]
    }
