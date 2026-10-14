"""Explicit executable coverage bindings, independent of route policy.

Infrastructure checks deliberately certify no planned product journey. Add a
reviewed Binding for each concrete principal/condition when its journey lands.
"""
from dataclasses import dataclass
import hashlib
import json


@dataclass(frozen=True)
class Binding:
    route: str
    method: str
    action: str | None
    principal: str
    condition: str
    node_id: str
    obligations: tuple[str, ...]
    browser: str = "api"
    viewport: str = "none"
    mode: str = "api"

    @property
    def key(self):
        return (self.route, self.method, self.action, self.principal,
                self.condition, self.browser, self.viewport, self.mode)


# Do not populate these from status codes or prose in routes.json. Conditions
# are named explicit cases; fixture IDs and secrets are resolved only at runtime.
BINDINGS: tuple[Binding, ...] = ()
CONDITIONS: dict[str, str] = {}


def binding_digest(bindings=BINDINGS, conditions=None):
    from dataclasses import asdict
    payload = {"bindings": [asdict(row) for row in bindings],
               "conditions": CONDITIONS if conditions is None else conditions}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
