import asyncio
import logging

from app.core.exceptions import AzureApiError
from app.providers.azure.arm_client import AzureArmClient
from app.providers.base import DeploymentInfo, DiscoveredResource, ProviderCredentials

_COGNITIVE_SERVICES_API = "2023-05-01"
_DEPLOYMENTS_API = "2024-10-01"
_PROJECTS_API = "2025-04-01-preview"
_RESOURCES_API = "2021-04-01"
_COGNITIVE_ACCOUNT_TYPE = "microsoft.cognitiveservices/accounts"

logger = logging.getLogger(__name__)


class AzureDiscoveryService:
    """Read-only ARM lookups: which accounts and deployments exist. Never invokes a model."""

    def __init__(self, arm_client: AzureArmClient) -> None:
        self._arm_client = arm_client

    async def list_accounts(self, credentials: ProviderCredentials) -> list[DiscoveredResource]:
        # The provider-specific list is the usual path, but some Foundry
        # subscriptions return an empty page (and a hanging nextLink) even
        # when Cognitive Services accounts exist. The generic ARM resources
        # list still returns them.
        by_id: dict[str, dict] = {}
        for loader in (self._list_provider_accounts, self._list_subscription_resources):
            try:
                items = await loader(credentials)
            except Exception:
                logger.info("Account discovery via %s failed", loader.__name__, exc_info=True)
                continue
            for item in items:
                resource_id = (item.get("id") or "").rstrip("/")
                if not resource_id:
                    continue
                key = resource_id.lower()
                current = by_id.get(key)
                if current is None or _resource_detail_score(item) > _resource_detail_score(current):
                    by_id[key] = item
        hydrated = await asyncio.gather(
            *[self._hydrate_account(credentials, item) for item in by_id.values()]
        )
        resources = [_to_resource(item) for item in hydrated if item.get("id")]
        if len(resources) <= 1:
            return resources
        return await self._prefer_resources_with_deployments(credentials, resources)

    async def _list_provider_accounts(self, credentials: ProviderCredentials) -> list[dict]:
        path = f"/subscriptions/{credentials.subscription_id}/providers/Microsoft.CognitiveServices/accounts"
        return await self._arm_client.get_all_pages(
            credentials, path, params={"api-version": _COGNITIVE_SERVICES_API}
        )

    async def _list_subscription_resources(self, credentials: ProviderCredentials) -> list[dict]:
        path = f"/subscriptions/{credentials.subscription_id}/resources"
        items: list[dict] = []
        try:
            items = await self._arm_client.get_all_pages(
                credentials,
                path,
                params={
                    "api-version": _RESOURCES_API,
                    "$filter": "resourceType eq 'Microsoft.CognitiveServices/accounts'",
                },
            )
        except AzureApiError:
            items = []
        accounts = [item for item in items if _is_cognitive_account(item)]
        if accounts:
            return accounts
        items = await self._arm_client.get_all_pages(
            credentials, path, params={"api-version": _RESOURCES_API}
        )
        return [item for item in items if _is_cognitive_account(item)]

    async def _hydrate_account(self, credentials: ProviderCredentials, item: dict) -> dict:
        properties = item.get("properties") or {}
        if item.get("kind") and item.get("location") and properties.get("endpoint"):
            return item
        resource_id = item.get("id")
        if not resource_id:
            return item
        try:
            detail = await self._arm_client.get(
                credentials, resource_id, params={"api-version": _COGNITIVE_SERVICES_API}
            )
        except AzureApiError:
            return item
        return detail or item

    async def _prefer_resources_with_deployments(
        self, credentials: ProviderCredentials, resources: list[DiscoveredResource]
    ) -> list[DiscoveredResource]:
        counts = await asyncio.gather(
            *[self._deployment_count(credentials, resource.resource_id) for resource in resources]
        )
        ranked = sorted(
            zip(resources, counts, strict=True),
            key=lambda pair: (-pair[1], pair[0].name.lower()),
        )
        return [resource for resource, _count in ranked]

    async def _deployment_count(self, credentials: ProviderCredentials, resource_id: str) -> int:
        try:
            return len(await self.list_deployments(credentials, resource_id))
        except Exception:
            return 0

    async def list_deployments(self, credentials: ProviderCredentials, resource_id: str) -> list[DeploymentInfo]:
        account_task = self._list_at(
            credentials,
            f"{resource_id}/deployments",
            [_DEPLOYMENTS_API, _COGNITIVE_SERVICES_API],
        )
        projects_task = self._list_project_deployments(credentials, resource_id)
        account_items, project_deployments = await asyncio.gather(account_task, projects_task)
        deployments = [_to_deployment(item) for item in account_items]
        deployments.extend(project_deployments)
        return _unique_deployments(deployments)

    async def _list_project_deployments(
        self, credentials: ProviderCredentials, resource_id: str
    ) -> list[DeploymentInfo]:
        try:
            projects = await self._arm_client.get_all_pages(
                credentials, f"{resource_id}/projects", params={"api-version": _PROJECTS_API}
            )
        except AzureApiError:
            return []
        results = await asyncio.gather(
            *[self._list_one_project(credentials, resource_id, project.get("name", "")) for project in projects]
        )
        deployments: list[DeploymentInfo] = []
        for batch in results:
            deployments.extend(batch)
        return deployments

    async def _list_one_project(
        self, credentials: ProviderCredentials, resource_id: str, project_name: str
    ) -> list[DeploymentInfo]:
        if not project_name:
            return []
        try:
            items = await self._arm_client.get_all_pages(
                credentials,
                f"{resource_id}/projects/{project_name}/deployments",
                params={"api-version": _PROJECTS_API},
            )
        except AzureApiError:
            return []
        return [_to_deployment(item) for item in items]

    async def _list_at(self, credentials: ProviderCredentials, path: str, api_versions: list[str]) -> list[dict]:
        last_error: AzureApiError | None = None
        for version in api_versions:
            try:
                return await self._arm_client.get_all_pages(credentials, path, params={"api-version": version})
            except AzureApiError as exc:
                last_error = exc
                if not _can_retry_api_version(exc):
                    raise
        if last_error is not None:
            if "(404)" in str(last_error):
                return []
            raise last_error
        return []


def _can_retry_api_version(exc: AzureApiError) -> bool:
    message = str(exc)
    return "(400)" in message or "(404)" in message


def _unique_deployments(deployments: list[DeploymentInfo]) -> list[DeploymentInfo]:
    seen: dict[str, DeploymentInfo] = {}
    for deployment in deployments:
        if deployment.name not in seen:
            seen[deployment.name] = deployment
    return list(seen.values())


def _to_resource(item: dict) -> DiscoveredResource:
    properties = item.get("properties", {})
    resource_id = item["id"]
    return DiscoveredResource(
        resource_id=resource_id,
        name=item["name"],
        resource_group=_extract_resource_group(resource_id),
        kind=item.get("kind", ""),
        location=item.get("location", ""),
        endpoint=properties.get("endpoint", ""),
    )


def _to_deployment(item: dict) -> DeploymentInfo:
    properties = item.get("properties", {})
    model = properties.get("model", {})
    sku = item.get("sku", {})
    return DeploymentInfo(
        name=item["name"],
        model_name=model.get("name", ""),
        model_version=model.get("version", ""),
        sku=sku.get("name", ""),
        capacity=sku.get("capacity", 0),
    )


def _extract_resource_group(resource_id: str) -> str:
    parts = resource_id.split("/")
    return parts[parts.index("resourceGroups") + 1] if "resourceGroups" in parts else ""


def _is_cognitive_account(item: dict) -> bool:
    resource_type = (item.get("type") or "").lower()
    if resource_type == _COGNITIVE_ACCOUNT_TYPE:
        return True
    resource_id = (item.get("id") or "").lower()
    return "/providers/microsoft.cognitiveservices/accounts/" in resource_id


def _resource_detail_score(item: dict) -> int:
    properties = item.get("properties") or {}
    return int(bool(item.get("kind"))) + int(bool(item.get("location"))) + int(bool(properties.get("endpoint")))
