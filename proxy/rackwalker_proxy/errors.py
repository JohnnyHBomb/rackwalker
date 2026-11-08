"""The one error shape (contracts/api.md "Errors: one shape")."""
from __future__ import annotations

from typing import Any

STATUS_FOR = {
    "bad_request": 400, "not_found": 404, "validation_failed": 422, "conflict": 409, "write_disabled": 403,
    "netbox_unreachable": 502, "netbox_error": 502, "unsupported_netbox_version": 502, "unauthorized": 401, "forbidden": 403,
    "internal": 500,
}


class ApiError(Exception):
    """Raised anywhere in the proxy; rendered as an ErrorResponse (route level) or an OpResult.error (per op)."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None, status: int | None = None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details
        self.status = status or STATUS_FOR[code]

    def body(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}
