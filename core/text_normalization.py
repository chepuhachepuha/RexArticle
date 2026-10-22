"""Unicode normalization shared by parsers, indexes, and matchers."""
from __future__ import annotations

import unicodedata


def normalize_article(value) -> str:
    """Return the internal article key: NFC, lowercase, without whitespace."""
    text = unicodedata.normalize("NFC", str(value or "")).lower()
    return "".join(text.split())


def preserve_article(value) -> str:
    """Keep a user-facing value intact while stabilising its Unicode form."""
    return unicodedata.normalize("NFC", str(value or ""))
