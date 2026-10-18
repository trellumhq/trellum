from trellum.data.connections import resolve_connection
from trellum.data.drivers import ConnectionDriver, get_driver, register_driver
from trellum.data.resolvers import CredentialResolver, register_resolver

# Names whose modules import pandas resolve on first use, so a process that
# only needs a light submodule (trellum.data.live_query_guard, say) does not
# load pandas, numpy and pyarrow just to reach it.
_LAZY = {
    "clear_cache": "trellum.data.query",
    "disable_cache": "trellum.data.query",
    "enable_cache": "trellum.data.query",
    "query_df": "trellum.data.query",
    "read_api": "trellum.data.query",
    "read_source": "trellum.data.query",
    "set_default_cache_ttl": "trellum.data.query",
    "set_max_concurrent_queries": "trellum.data.query",
    "df_to_json_records": "trellum.data.transforms",
}


def __getattr__(name: str):
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY))


__all__ = [
    "resolve_connection",
    "query_df",
    "read_source",
    "read_api",
    "set_max_concurrent_queries",
    "set_default_cache_ttl",
    "df_to_json_records",
    "disable_cache",
    "enable_cache",
    "clear_cache",
    "register_driver",
    "get_driver",
    "ConnectionDriver",
    "register_resolver",
    "CredentialResolver",
]
