"""HTTP JSON requests with bounded retries and credential-free error messages."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request


class ServiceError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(url: str, *, payload: dict | None = None, headers: dict | None = None,
                 timeout: float = 120, retries: int = 2, service: str = "Remote service") -> dict:
    request_headers = {"Accept": "application/json", **(headers or {})}
    data = None
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
    request = urllib.request.Request(url, data=data, headers=request_headers)
    opener = urllib.request.build_opener(NoRedirect())
    for attempt in range(retries + 1):
        try:
            with opener.open(request, timeout=timeout) as response:
                value = json.loads(response.read())
            if not isinstance(value, dict):
                raise ServiceError(f"{service} returned a non-object JSON response")
            return value
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            if (code != 429 and not 500 <= code <= 599) or attempt == retries:
                raise ServiceError(f"{service} returned HTTP {code}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == retries:
                raise ServiceError(f"{service} connection failed or timed out") from None
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise ServiceError(f"{service} returned invalid JSON") from exc
        time.sleep(min(2 ** attempt, 8))
    raise AssertionError("Retry loop must return or raise")
