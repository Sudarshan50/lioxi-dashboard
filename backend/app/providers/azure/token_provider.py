import time

import httpx

from app.providers.base import ProviderCredentials

_TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
_SCOPE = "https://management.azure.com/.default"


class AzureTokenProvider:
    """Acquires and caches Azure AD management-plane bearer tokens per identity."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[str, float]] = {}

    async def get_token(self, credentials: ProviderCredentials) -> str:
        cache_key = f"{credentials.tenant_id}:{credentials.client_id}"
        cached = self._cache.get(cache_key)
        if cached and cached[1] > time.time() + 60:
            return cached[0]

        url = _TOKEN_URL.format(tenant_id=credentials.tenant_id)
        data = {
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "grant_type": "client_credentials",
            "scope": _SCOPE,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, data=data)
        response.raise_for_status()
        payload = response.json()
        token = payload["access_token"]
        expires_at = time.time() + payload.get("expires_in", 3600)
        self._cache[cache_key] = (token, expires_at)
        return token
