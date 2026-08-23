import asyncio

from app.core.exceptions import AzureApiError
from app.providers.azure.arm_client import AzureArmClient
from app.providers.base import DeploymentInfo, DiscoveredResource, ProviderCredentials

_COGNITIVE_SERVICES_API = "2023-05-01"
_DEPLOYMENTS_API = "2024-10-01"
_PROJECTS_API = "2025-04-01-preview"


class AzureDiscoveryService:
    """Read-only ARM lookups: which accounts and deployments exist. Never invokes a model."""

    def __init__(self, arm_client: AzureArmClient) -> None:
        self._arm_client = arm_client

    async def list_accounts(self, credentials: ProviderCredentials) -> list[DiscoveredResource]:
        path = f"/subscriptions/{credentials.subscription_id}/providers/Microsoft.CognitiveServices/accounts"
        items = await self._arm_client.get_all_pages(credentials, path, params={"api-version": _COGNITIVE_SERVICES_API})
        return [_to_resource(item) for item in items]

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
