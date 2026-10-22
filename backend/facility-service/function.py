"""Workshop Lambda entrypoint; preserve the repository's service path convention."""

from copy import deepcopy

from mangum import Mangum

from facility.database import reuse_connections
from facility.main import app

# Mangum runs every request on one event loop per warm container, so database
# connections can stay open between requests instead of reconnecting each time.
reuse_connections()

adapter = Mangum(app, lifespan="off")


def handler(event: dict, context: object) -> dict:
    """Handle Function URL/API Gateway HTTP events without an HTTP migration route."""
    if not (
        "requestContext" in event and ("http" in event["requestContext"] or "httpMethod" in event)
    ):
        raise ValueError("HTTP events only; invoke migration_handler.handler separately")
    adjusted = deepcopy(event)
    prefix = "/api/facility-service"
    for field in ("path", "rawPath"):
        path = adjusted.get(field, "")
        if path == prefix or path.startswith(prefix + "/"):
            adjusted[field] = path[len(prefix) :] or "/"
    http = adjusted.get("requestContext", {}).get("http", {})
    path = http.get("path", "")
    if path == prefix or path.startswith(prefix + "/"):
        http["path"] = path[len(prefix) :] or "/"
    return adapter(adjusted, context)
