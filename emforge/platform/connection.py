"""Read a user-selected local connection file for an agent-side HTTP proxy."""
import json
from pathlib import Path
from urllib.parse import urlsplit

from .transport import RemotePlatform


def remote_from_file(path):
    try:
        config = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        endpoint, token = config["endpoint"], config["token"]
        parts = urlsplit(endpoint)
        if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username
                or parts.password or parts.query or parts.fragment
                or not isinstance(token, str) or not token.strip()):
            raise ValueError
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("Invalid agent connection: expected an HTTP endpoint and token") from None
    return RemotePlatform(endpoint, token=token)
