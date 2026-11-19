"""Correctness and bounded work for text/binary incremental secret matching."""
import itertools

import pytest

from trellum.secret_prefix import SecretPrefixes, prefix_table, suffix_length


@pytest.mark.parametrize('binary', [False, True])
def test_prefix_matching_overlaps_and_full_matches(binary):
    words = [''.join(chars) for length in range(1, 5)
             for chars in itertools.product('ab', repeat=length)]
    if binary:
        words = [word.encode() for word in words]
    for secret in words:
        matcher = SecretPrefixes([secret])
        for data in words:
            expected = max([0] + [length for length in range(1, len(secret))
                                   if data.endswith(secret[:length])])
            assert matcher.retained_length(data) == expected


@pytest.mark.heavy
def test_long_secret_matching_has_linear_character_access_bound():
    class Counted:
        def __init__(self, value):
            self.value, self.accesses = value, 0

        def __len__(self):
            return len(self.value)

        def __getitem__(self, index):
            self.accesses += 1
            return self.value[index]

    secret = Counted('a' * 16383 + 'b')
    table = prefix_table(secret)
    assert secret.accesses < 8 * len(secret)
    secret.accesses = 0
    assert suffix_length('a' * 16383 + 'c', secret, table) == 0
    assert secret.accesses < 4 * len(secret)
