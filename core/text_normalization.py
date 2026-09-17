"""Unicode normalization shared by parsers, indexes, and matchers."""
from __future__ import annotations

import unicodedata


def normalize_article(value) -> str:
    """Return one canonical spelling without changing case or punctuation."""
    return unicodedata.normalize("NFC", str(value or ""))
