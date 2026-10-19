from trellum.rendering.cdn import get_cdn_url, register_cdn
from trellum.rendering.js_runtime import generate_js_runtime

# render_report pulls in every component and with them pandas; it resolves on
# first use so trellum.rendering.cdn stays light for the processes that only
# need CDN metadata.
_LAZY = {"render_report": "trellum.rendering.html_builder"}


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


__all__ = ["render_report", "generate_js_runtime", "get_cdn_url", "register_cdn"]
