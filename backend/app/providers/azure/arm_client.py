import httpx

from app.core.exceptions import AzureApiError
from app.providers.azure.token_provider import AzureTokenProvider
from app.providers.base import ProviderCredentials

_MANAGEMENT_BASE = "https://management.azure.com"


class AzureArmClient:
    """Thin authenticated GET/POST wrapper around Azure Resource Manager. Read-only usage only."""

    def __init__(self, token_provider: AzureTokenProvider) -> None:
        self._token_provider = token_provider

    async def get(self, credentials: ProviderCredentials, path: str, params: dict | None = None) -> dict:
        token = await self._token_provider.get_token(credentials)
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{_MANAGEMENT_BASE}{path}", headers=self._auth_header(token), params=params)
        return self._parse(response)

    async def post(
        self, credentials: ProviderCredentials, path: str, json: dict, params: dict | None = None
    ) -> dict:
        token = await self._token_provider.get_token(credentials)
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{_MANAGEMENT_BASE}{path}", headers=self._auth_header(token), params=params, json=json
            )
        return self._parse(response)

    async def get_all_pages(
        self, credentials: ProviderCredentials, path: str, params: dict | None = None
    ) -> list[dict]:
        """Follows ARM's `nextLink` pagination for standard list operations
        (`{"value": [...], "nextLink": "..."}`). Azure caps list responses at a
        small page size (e.g. 4 items for Cognitive Services accounts), so
        callers that need every resource in a subscription must page through
        rather than reading `value` from a single response.
        """
        token = await self._token_provider.get_token(credentials)
        items: list[dict] = []
        url = f"{_MANAGEMENT_BASE}{path}"
        request_params = params
        seen: set[str] = set()
        async with httpx.AsyncClient(timeout=30) as client:
            while url and url not in seen and len(seen) < 200:
                seen.add(url)
                response = await client.get(url, headers=self._auth_header(token), params=request_params)
                body = self._parse(response)
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
