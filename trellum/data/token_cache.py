"""Atomic MSAL Extensions persistence, compatible with existing MSAL JSON caches."""
from contextlib import contextmanager
from pathlib import Path

import msal
from msal_extensions import FilePersistence, PersistedTokenCache
from msal_extensions.persistence import PersistenceNotFound

from trellum.private_files import (
    CredentialPersistenceError,
    atomic_write,
    prepare_parent,
    private_lock,
    read_private,
)


class AtomicPersistence(FilePersistence):
    def load(self):
        try:
            return read_private(Path(self.get_location()))
        except FileNotFoundError:
            raise PersistenceNotFound() from None

    def save(self, content):
        atomic_write(Path(self.get_location()), content)


class PrivateTokenCache(PersistedTokenCache):
    """Use OS locking and reload every transaction, avoiding mtime races.

    MSAL Extensions' public persistence/cache interfaces are retained. Its
    timestamp optimization can miss writes between reload and last-sync updates;
    a local credential cache is small enough to reload without that shortcut.
    """
    def _reload_if_necessary(self):
        try:
            self.deserialize(self._persistence.load())
        except PersistenceNotFound:
            self.deserialize("{}")
        except (ValueError, UnicodeError):
            raise CredentialPersistenceError("Invalid credential cache; existing file was retained.") from None

    @contextmanager
    def _transaction(self):
        with self._lock:
            # MSAL search removes expired tokens through our modify override.
            # Reuse this transaction without relocking or reloading its state.
            if getattr(self, "_transaction_active", False):
                yield
                return
            with private_lock(Path(self._persistence.get_location())):
                self._reload_if_necessary()
                self._transaction_active = True
                try:
                    yield
                finally:
                    self._transaction_active = False

    def modify(self, credential_type, old_entry, new_key_value_pairs=None):
        with self._transaction():
            msal.SerializableTokenCache.modify(self, credential_type, old_entry, new_key_value_pairs)
            self._persistence.save(self.serialize())

    def search(self, credential_type, **kwargs):
        # Materialize while holding locks: MSAL search returns a lazy generator.
        with self._transaction():
            return iter(list(msal.SerializableTokenCache.search(self, credential_type, **kwargs)))


def load_cache(location):
    path = Path(location)
    prepare_parent(path, cache=True)
    cache = PrivateTokenCache(AtomicPersistence(str(path)))
    with private_lock(path):
        cache._reload_if_necessary()
    return cache
