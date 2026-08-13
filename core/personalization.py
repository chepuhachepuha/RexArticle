"""Shared helpers for personalization settings."""


def normalise_store_suffix_exclusions(value):
    """Return valid internal store names as a set."""
    if not isinstance(value, (list, tuple, set, frozenset)):
        return set()
    return {
        name
        for name in value
        if isinstance(name, str) and name
    }


def should_append_store_suffix(
    store_name,
    append_enabled,
    excluded_store_names=None,
):
    """Return whether a delivery folder should receive the store suffix."""
    if not append_enabled or not store_name:
        return False
    exclusions = normalise_store_suffix_exclusions(excluded_store_names)
    return store_name not in exclusions
