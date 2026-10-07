import logging
import time
from typing import Any

from botocore.exceptions import ClientError
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from db.client import get_dynamodb_table
from db.schema import rate_limit_pk, rate_limit_sk

logger = logging.getLogger(__name__)


class DynamoDBRateLimitMiddleware(BaseHTTPMiddleware):
    """DynamoDB-backed atomic conditional rate-limiting middleware.

    Implements a fixed 1-minute window counter with TTL expiration.
    Fails open on throughput throttling or service degradation to preserve
    availability of the public read-only API.
    """

    def __init__(
        self,
        app: Any,
        table: Any | None = None,
        rate_limit: int = 60,
        excluded_paths: set[str] | None = None,
    ):
        super().__init__(app)
        self._table = table
        self.rate_limit = rate_limit
        self.excluded_paths = excluded_paths or {
            "/health",
            "/",
            "/docs",
            "/redoc",
            "/openapi.json",
        }

    @property
    def table(self) -> Any:
        if self._table is None:
            self._table = get_dynamodb_table()
        return self._table

    def check_rate_limit(self, client_ip: str) -> tuple[bool, int, int]:
        """Atomically check and increment the request counter for the current epoch minute.

        Returns (is_allowed, remaining_requests, seconds_until_reset).
        """
        now = time.time()
        epoch_minute = int(now // 60)
        expiry = int(now) + 120
        seconds_until_reset = max(1, 60 - int(now % 60))

        try:
            response = self.table.update_item(
                Key={
                    "PK": rate_limit_pk(client_ip),
                    "SK": rate_limit_sk(epoch_minute),
                },
                UpdateExpression="ADD request_count :one SET expiry = :ttl",
                ConditionExpression="attribute_not_exists(request_count) OR request_count < :limit",
                ExpressionAttributeValues={
                    ":one": 1,
                    ":limit": self.rate_limit,
                    ":ttl": expiry,
                },
                ReturnValues="UPDATED_NEW",
            )
            count = int(response.get("Attributes", {}).get("request_count", 1))
            remaining = max(0, self.rate_limit - count)
            return True, remaining, seconds_until_reset

        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "ConditionalCheckFailedException":
                # Rate limit exceeded for this minute window
                return False, 0, seconds_until_reset

            # Fail open for throughput exceeded, timeouts, or transient network errors
            logger.warning(
                "DynamoDB rate limit error for IP '%s' (%s), failing open: %s",
                client_ip,
                code,
                exc,
            )
            return True, self.rate_limit, seconds_until_reset

        except Exception as exc:
            logger.warning(
                "Unexpected error during rate limiting for IP '%s', failing open: %s",
                client_ip,
                exc,
            )
            return True, self.rate_limit, seconds_until_reset

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Bypass rate limiting for documentation and health checks
        if request.url.path in self.excluded_paths:
            return await call_next(request)

        # Source IP resolution:
        # Behind Lambda Function URL with AWS Lambda Web Adapter, the verified source IP
        # is populated in ASGI request.client.host from requestContext.http.sourceIp.
        # We do NOT trust client-injected X-Forwarded-For or CF-Connecting-IP headers.
        client_ip = request.client.host if request.client else "unknown"

        allowed, remaining, reset_secs = self.check_rate_limit(client_ip)

        if not allowed:
            from app.v2.errors import error_body, is_v2

            return JSONResponse(
                status_code=429,
                content=error_body(
                    request, "rate_limited", "Rate limit exceeded. Try again in the next minute."
                )
                if is_v2(request)
                else {"detail": "Rate limit exceeded. Try again in the next minute."},
                headers={
                    "Retry-After": str(reset_secs),
                    "X-RateLimit-Limit": str(self.rate_limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_secs),
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self.rate_limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset_secs)
        return response
