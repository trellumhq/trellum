"""Linear-time matching of an output suffix against secret prefixes."""
from __future__ import annotations


def prefix_table(value):
    """KMP fallback lengths; supports both Unicode text and bytes."""
    table = [0] * len(value)
    matched = 0
    for index in range(1, len(value)):
        while matched and value[index] != value[matched]:
            matched = table[matched - 1]
        if value[index] == value[matched]:
            matched += 1
        table[index] = matched
    return table


def suffix_length(data, secret, table):
    """Length of the longest suffix that is a proper secret prefix."""
    matched = 0
    for character in data[-(len(secret) - 1):] if len(secret) > 1 else ():
        while matched and character != secret[matched]:
            matched = table[matched - 1]
        if character == secret[matched]:
            matched += 1
    return matched


class SecretPrefixes:
    """Build-scoped tables: no secrets retained in a global cache."""

    def __init__(self, secrets):
        self.secrets = tuple(secrets)
        self.tables = [prefix_table(secret) for secret in self.secrets]

    def retained_length(self, data):
        return max((suffix_length(data, secret, table)
                    for secret, table in zip(self.secrets, self.tables)), default=0)
