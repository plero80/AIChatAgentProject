"""Server-issued chat identity. Never trust a client-supplied user_id."""

import re
from uuid import uuid4

SESSION_COOKIE = "alona_session"
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def resolve_session_id(cookie_value: str | None) -> tuple[str, bool]:
    """Return (user_id, is_new). Rejects anything that is not a UUID."""
    if cookie_value and _UUID.match(cookie_value):
        return cookie_value, False
    return str(uuid4()), True
