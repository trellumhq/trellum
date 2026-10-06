"""Role constants shared by studios, permission groups, and the engine."""
VIEWER = "viewer"
DEVELOPER = "developer"
ADMIN = "admin"

STUDIO_ROLE_CHOICES = [
    (VIEWER, "Viewer"),
    (DEVELOPER, "Developer"),
    (ADMIN, "Admin"),
]

ORG_MEMBER = "member"
ORG_ADMIN = "admin"
ORG_ROLE_CHOICES = [
    (ORG_MEMBER, "Member"),
    (ORG_ADMIN, "Admin"),
]

_RANK = {None: 0, "": 0, VIEWER: 1, DEVELOPER: 2, ADMIN: 3}


def rank(role: str | None) -> int:
    return _RANK.get(role, 0)


def max_role(*roles: str | None) -> str | None:
    """Return the highest-ranked of the given roles (None if all empty)."""
    best = None
    for r in roles:
        if rank(r) > rank(best):
            best = r
    return best


def at_least(role: str | None, minimum: str) -> bool:
    return rank(role) >= rank(minimum)
