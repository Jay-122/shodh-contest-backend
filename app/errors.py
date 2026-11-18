"""Error taxonomy (Stage 1 requirement): callers can always tell apart
- validation errors   -> 422, type "validation"      (malformed input)
- business-rule errors-> 409, type "business_rule"   (well-formed but not allowed now)
- authorization       -> 403, type "forbidden" / 404, type "not_found"
- infrastructure      -> 503, type "infra"           (DB/judge unavailable etc.)

Cross-org objects are reported as 404 not_found so that knowing an object's
ID never reveals that it exists in another organization.
"""

from fastapi import HTTPException


class ApiError(HTTPException):
    def __init__(self, status_code: int, error_type: str, message: str):
        super().__init__(
            status_code=status_code,
            detail={"type": error_type, "message": message},
        )


def validation(message: str) -> ApiError:
    return ApiError(422, "validation", message)


def business_rule(message: str) -> ApiError:
    return ApiError(409, "business_rule", message)


def forbidden(message: str = "You do not have permission to perform this action") -> ApiError:
    return ApiError(403, "forbidden", message)


def not_found(message: str = "Resource not found") -> ApiError:
    return ApiError(404, "not_found", message)


def conflict(message: str) -> ApiError:
    return ApiError(409, "conflict", message)


def infra(message: str) -> ApiError:
    return ApiError(503, "infra", message)
