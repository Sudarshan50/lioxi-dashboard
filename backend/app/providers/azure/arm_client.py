import asyncio
import logging

import httpx

from app.core.exceptions import AzureApiError
from app.providers.azure.token_provider import AzureTokenProvider
from app.providers.base import ProviderCredentials
from app.runtime import AZURE_SYNC_CONCURRENCY

logger = logging.getLogger(__name__)

_MANAGEMENT_BASE = "https://management.azure.com"
_ARM_RETRIES = 4
_ARM_RETRY_SECONDS = 2.0
_arm_limit: asyncio.Semaphore | None = None


def _arm_sem() -> asyncio.Semaphore:
    global _arm_limit
    if _arm_limit is None:
        _arm_limit = asyncio.Semaphore(max(1, AZURE_SYNC_CONCURRENCY))
    return _arm_limit


def _is_throttled(exc: BaseException) -> bool:
    text = str(exc)
    return "(429)" in text or "Too many requests" in text


def _url(path: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"{_MANAGEMENT_BASE}{path}"


class AzureArmClient:
    """Thin authenticated GET/POST wrapper around Azure Resource Manager. Read-only usage only."""

    def __init__(self, token_provider: AzureTokenProvider) -> None:
        self._token_provider = token_provider

    async def get(self, credentials: ProviderCredentials, path: str, params: dict | None = None) -> dict:
        return await self._json_request("GET", credentials, _url(path), params=params)

    async def post(
        self, credentials: ProviderCredentials, path: str, json: dict, params: dict | None = None
    ) -> dict:
        return await self._json_request("POST", credentials, _url(path), params=params, json=json)

    async def get_all_pages(
        self, credentials: ProviderCredentials, path: str, params: dict | None = None
    ) -> list[dict]:
        """Follows ARM's `nextLink` pagination for standard list operations
        (`{"value": [...], "nextLink": "..."}`). Azure caps list responses at a
        small page size (e.g. 4 items for Cognitive Services accounts), so
        callers that need every resource in a subscription must page through
        rather than reading `value` from a single response.
        """
        items: list[dict] = []
        url = _url(path)
        request_params = params
        seen: set[str] = set()
        while url and url not in seen and len(seen) < 200:
            seen.add(url)
            body = await self._json_request("GET", credentials, url, params=request_params)
            page_items = body.get("value") or []
            items.extend(page_items)
            next_link = body.get("nextLink")
            # Some Cognitive Services list responses return value=[] plus a
            # nextLink that never completes. Stop instead of hanging.
            if not next_link or not page_items:
                break
            url = next_link
            request_params = None
        return items

    async def _json_request(
        self,
        method: str,
        credentials: ProviderCredentials,
        url: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
    ) -> dict:
        delay = _ARM_RETRY_SECONDS
        last_error: AzureApiError | None = None
        for attempt in range(_ARM_RETRIES):
            token = await self._token_provider.get_token(credentials)
            kwargs: dict = {"headers": self._auth_header(token), "params": params}
            if json is not None:
                kwargs["json"] = json
            async with _arm_sem():
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.request(method, url, **kwargs)
            try:
                return self._parse(response)
            except AzureApiError as exc:
                last_error = exc
                if attempt + 1 < _ARM_RETRIES and _is_throttled(exc):
                    logger.warning(
                        "Azure ARM throttled %s %s (attempt %s/%s)",
                        method,
                        url.split("?", 1)[0][-80:],
                        attempt + 1,
                        _ARM_RETRIES,
                    )
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 16)
                    continue
                raise
        raise last_error or AzureApiError("Azure API error (429): Too many requests. Please retry.")

    @staticmethod
    def _auth_header(token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def _parse(response: httpx.Response) -> dict:
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400:
            message = body.get("error", {}).get("message", response.text)
            raise AzureApiError(f"Azure API error ({response.status_code}): {message}")
        return body
