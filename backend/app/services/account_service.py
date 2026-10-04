import logging

from sqlalchemy.exc import IntegrityError

from app.core.crypto import SecretBox
from app.models.model_catalog import MonitoredModel
from app.models.provider_account import ProviderAccount
from app.providers.base import ProviderCredentials
from app.providers.registry import get_provider
from app.repositories.account_repository import AccountRepository
from app.repositories.model_repository import ModelRepository
from app.repositories.registered_model_repository import RegisteredModelRepository
from app.schemas.account import (
    AccountCreateRequest,
    AccountDiscoverDeploymentsRequest,
    AccountDiscoverRequest,
    AccountUpdateRequest,
)
from app.services.openai_key_store import attach_stored_key
from app.services.join_group import normalize_group
from app.services.owner_tag import apply_owner_to_account, parse_owner_tag, person_from_payload

logger = logging.getLogger(__name__)


class AccountNotFoundError(Exception):
    pass


class DuplicateAccountError(Exception):
    pass


class AccountValidationError(Exception):
    pass


def azure_stack_already_gone(*texts: str | None) -> bool:
    blob = " ".join(text or "" for text in texts).lower()
    if not blob:
        return False
    return azure_resource_missing(blob) or any(
        needle in blob
        for needle in (
            "subscriptionnotfound",
            "aadsts700016",
            "application not found",
            "no subscription found",
            "subscription not found",
        )
    )


def azure_resource_missing(*texts: str | None) -> bool:
    blob = " ".join(text or "" for text in texts).lower()
    if not blob:
        return False
    return any(
        needle in blob
        for needle in (
            "resourcegroupnotfound",
            "resourcenotfound",
            "could not be found",
            "was not found",
            "does not exist",
        )
    )


def portal_account_for_redeploy(siblings: list, preferred_name: str):
    if not siblings:
        return None
    wanted = (preferred_name or "").strip().lower()
    if wanted:
        named = next((row for row in siblings if (row.name or "").strip().lower() == wanted), None)
        if named is not None:
            return named
    if len(siblings) == 1:
        return siblings[0]
    return None


def allocate_unique_name(preferred: str, taken_lower: set[str]) -> str:
    """Return preferred, or preferred1 / preferred2 / … if that name is already used."""
    cleaned = " ".join((preferred or "").split())[:128] or "account"
    if cleaned.lower() not in taken_lower:
        return cleaned
    for index in range(1, 1000):
        suffix = str(index)
        candidate = f"{cleaned[: 128 - len(suffix)]}{suffix}"
        if candidate.lower() not in taken_lower:
            return candidate
    raise AccountValidationError("Could not allocate a unique account name.")


class AccountService:
    """CRUD and discovery for provider accounts. Sync orchestration lives separately
    in SyncOrchestrator to keep this class focused on account management only.
    """

    def __init__(self, account_repository: AccountRepository, secret_box: SecretBox) -> None:
        self._account_repository = account_repository
        self._secret_box = secret_box

    async def discover(self, payload: AccountDiscoverRequest) -> list[dict]:
        provider = get_provider("azure_openai")
        credentials = ProviderCredentials(
            tenant_id=payload.tenant_id,
            client_id=payload.client_id,
            client_secret=payload.client_secret,
            subscription_id=payload.subscription_id,
        )
        resources = await provider.discover_resources(credentials)
        return [resource.__dict__ for resource in resources]

    async def discover_deployments(self, payload: AccountDiscoverDeploymentsRequest) -> list[dict]:
        provider = get_provider("azure_openai")
        credentials = ProviderCredentials(
            tenant_id=payload.tenant_id,
            client_id=payload.client_id,
            client_secret=payload.client_secret,
            subscription_id=payload.subscription_id,
        )
        deployments = await provider.list_deployments(credentials, payload.resource_id)
        return [deployment.__dict__ for deployment in deployments]

    async def create_account(self, payload: AccountCreateRequest) -> ProviderAccount:
        resource = _filled_resource(
            subscription_id=payload.subscription_id,
            resource_name=payload.resource_name,
            resource_id=payload.resource_id,
            resource_group=payload.resource_group,
            endpoint=payload.endpoint,
            kind=payload.kind,
            location=payload.location,
        )
        name = await _unique_account_name(
            self._account_repository,
            preferred=payload.name,
            resource_name=payload.resource_name,
            subscription_id=payload.subscription_id,
        )
        account = ProviderAccount(
            name=name,
            provider_type="azure_openai",
            tenant_id=payload.tenant_id,
            client_id=payload.client_id,
            client_secret_encrypted=self._secret_box.encrypt(payload.client_secret),
            subscription_id=payload.subscription_id,
            resource_id=resource["resource_id"],
            resource_group=resource["resource_group"],
            resource_name=resource["resource_name"],
            endpoint=resource["endpoint"],
            kind=resource["kind"],
            location=resource["location"],
            group_tag=normalize_group(payload.group_tag),
        )
        _apply_credit_grant(account, payload.credits_limit, manual=True)
        try:
            account.owner_tag = parse_owner_tag(payload.owner_tag)
        except ValueError as exc:
            raise AccountValidationError(str(exc)) from exc
        siblings = await self._account_repository.list_all()
        apply_owner_to_account(account, [*siblings, account])
        await attach_stored_key(self._account_repository._session, account)
        try:
            created = await self._account_repository.create(account)
        except IntegrityError:
            account.name = await _unique_account_name(
                self._account_repository,
                preferred=payload.name,
                resource_name=payload.resource_name,
                subscription_id=payload.subscription_id,
            )
            created = await self._account_repository.create(account)
        await self._attach_emails([created])
        return created

    async def upsert_from_kimi_deploy(
        self,
        *,
        payload: dict[str, str],
        resource_name: str,
        resource_group: str,
        endpoint: str,
        location: str,
        owner_tag: str | None,
        credits_limit: float | None,
        credits_remaining: float | None,
        credits_used: float | None,
        credits_currency: str | None,
        credits_label: str | None,
        deployment_name: str | None,
    ) -> ProviderAccount:
        """Create or refresh the portal monitoring account for a successful Kimi deploy."""
        subscription_id = (payload.get("AZURE_SUBSCRIPTION_ID") or "").strip()
        tenant_id = (payload.get("AZURE_TENANT_ID") or "").strip()
        client_id = (payload.get("AZURE_CLIENT_ID") or "").strip()
        client_secret = (payload.get("AZURE_CLIENT_SECRET") or "").strip()
        if not subscription_id or not tenant_id or not client_id or not client_secret or not resource_name:
            raise AccountValidationError("Deploy result is missing credentials or Foundry resource name.")

        resource = _filled_resource(
            subscription_id=subscription_id,
            resource_name=resource_name,
            resource_group=resource_group,
            endpoint=endpoint,
            kind="AIServices",
            location=location,
        )
        account = await self._account_repository.get_by_subscription_and_resource(subscription_id, resource_name)
        if account is None:
            account = portal_account_for_redeploy(
                await self._account_repository.list_by_subscription(subscription_id),
                payload.get("name") or "",
            )
        created = account is None
        old_resource = None if created else (account.resource_name or "")
        if account is None:
            name = await _unique_account_name(
                self._account_repository,
                preferred=payload.get("name") or payload.get("account_holder") or resource_name,
                resource_name=resource_name,
                subscription_id=subscription_id,
            )
            account = ProviderAccount(
                name=name,
                provider_type="azure_openai",
                tenant_id=tenant_id,
                client_id=client_id,
                client_secret_encrypted=self._secret_box.encrypt(client_secret),
                subscription_id=subscription_id,
                resource_id=resource["resource_id"],
                resource_group=resource["resource_group"],
                resource_name=resource["resource_name"],
                endpoint=resource["endpoint"],
                kind=resource["kind"],
                location=resource["location"],
            )
        else:
            account.tenant_id = tenant_id
            account.client_id = client_id
            account.client_secret_encrypted = self._secret_box.encrypt(client_secret)
            for key, value in resource.items():
                setattr(account, key, value)
            if old_resource and old_resource.lower() != resource_name.lower():
                from app.services.azure_inventory_cache import drop_azure_inventory
                from app.services.openai_key_store import drop_foundry_key

                await drop_foundry_key(self._account_repository._session, subscription_id, old_resource)
                await drop_azure_inventory(subscription_id, old_resource)

        try:
            # Only JSON person_associated may overwrite; owner_tag arg can be a portal copy.
            tag = person_from_payload(payload)
        except ValueError as exc:
            raise AccountValidationError(str(exc)) from exc
        if tag:
            account.owner_tag = tag
        # Join group is decided once, at onboarding. A later re-deploy of the
        # same resource must not silently move an account between pools.
        if created:
            account.group_tag = normalize_group(payload.get("group_tag"))
        siblings = await self._account_repository.list_all()
        apply_owner_to_account(account, siblings)

        if credits_limit and not account.credits_limit_manual:
            _apply_credit_grant(account, credits_limit, manual=False)
            if credits_remaining is not None:
                account.credits_remaining = credits_remaining
            if credits_used is not None:
                account.credits_used = credits_used
            if credits_currency:
                account.credits_currency = credits_currency
            if credits_label:
                account.credits_label = credits_label

        await attach_stored_key(self._account_repository._session, account)
        if created:
            try:
                account = await self._account_repository.create(account)
            except IntegrityError:
                account.name = await _unique_account_name(
                    self._account_repository,
                    preferred=payload.get("name") or payload.get("account_holder") or resource_name,
                    resource_name=resource_name,
                    subscription_id=subscription_id,
                )
                account = await self._account_repository.create(account)
        else:
            account = await self._account_repository.save(account)

        await _link_kimi_deployment(
            self._account_repository._session,
            account.id,
            deployment_name or "FW-Kimi-K3",
        )
        return account

    async def list_accounts(self) -> list[ProviderAccount]:
        accounts = await self._account_repository.list_all()
        await self._attach_emails(accounts)
        return accounts

    async def _attach_emails(self, accounts: list[ProviderAccount]) -> None:
        from app.services.service_principal_store import emails_by_subscription

        emails = await emails_by_subscription(self._account_repository._session)
        for account in accounts:
            account.email = emails.get((account.subscription_id or "").strip().lower())

    async def list_deployments(self, account_id: int) -> list[dict]:
        account = await self._get_or_raise(account_id)
        return await self.discover_deployments_for_account(account_id, account.resource_id)

    async def discover_deployments_for_account(self, account_id: int, resource_id: str) -> list[dict]:
        account = await self._get_or_raise(account_id)
        provider = get_provider(account.provider_type)
        deployments = await provider.list_deployments(self._credentials_for(account), resource_id)
        return [deployment.__dict__ for deployment in deployments]

    async def reveal_api_key(self, account_id: int) -> dict[str, str | None]:
        account = await self._get_or_raise(account_id)
        from app.services.openai_key_store import decrypt_foundry_key

        api_key = (await decrypt_foundry_key(
            self._account_repository._session, account.subscription_id, account.resource_name
        ) or "").strip()
        azure_error = ""
        if not api_key:
            api_key, azure_error = await self._fetch_and_store_foundry_key(account)
        if not api_key:
            raise AccountValidationError(
                azure_error or "No stored Foundry API key. Deploy or test the model first."
            )
        return {"api_key": api_key, "endpoint": account.endpoint or "", "new_api_error": None}

    async def rotate_api_key(self, account_id: int) -> dict[str, str | None]:
        account = await self._get_or_raise(account_id)
        from app.core.exceptions import AzureApiError
        from app.providers.azure.arm_client import AzureArmClient
        from app.providers.azure.token_provider import AzureTokenProvider

        resource_id = _foundry_resource_id(account)
        if not resource_id:
            raise AccountValidationError("This account has no Foundry resource to rotate.")
        try:
            keys = await AzureArmClient(AzureTokenProvider()).post(
                self._credentials_for(account),
                f"{resource_id}/regenerateKey",
                json={"keyName": "Key1"},
                params={"api-version": "2023-05-01"},
            )
        except AzureApiError as exc:
            raise AccountValidationError(str(exc)) from exc
        api_key = str(keys.get("key1") or keys.get("Key1") or "").strip()
        if not api_key:
            raise AccountValidationError("Azure rotated the key but did not return Key1.")
        await self._store_foundry_key(account, api_key)
        new_api_error = await self._push_newapi_key(account, api_key)
        return {"api_key": api_key, "endpoint": account.endpoint or "", "new_api_error": new_api_error or None}

    async def _fetch_and_store_foundry_key(self, account: ProviderAccount) -> tuple[str, str]:
        from app.core.exceptions import AzureApiError
        from app.providers.azure.arm_client import AzureArmClient
        from app.providers.azure.token_provider import AzureTokenProvider

        resource_id = _foundry_resource_id(account)
        if not resource_id:
            return "", ""
        try:
            keys = await AzureArmClient(AzureTokenProvider()).post(
                self._credentials_for(account),
                f"{resource_id}/listKeys",
                json={},
                params={"api-version": "2023-05-01"},
            )
        except AzureApiError as exc:
            return "", str(exc)
        api_key = str(keys.get("key1") or keys.get("Key1") or keys.get("key2") or keys.get("Key2") or "").strip()
        if not api_key:
            return "", ""
        await self._store_foundry_key(account, api_key)
        return api_key, ""

    async def _store_foundry_key(self, account: ProviderAccount, api_key: str) -> None:
        from app.services.openai_key_store import persist_foundry_api_keys

        try:
            await persist_foundry_api_keys(
                self._account_repository._session,
                [
                    {
                        "api_key": api_key,
                        "subscription_id": account.subscription_id,
                        "resource_name": account.resource_name,
                        "resource_group": account.resource_group,
                        "endpoint": account.endpoint,
                    }
                ],
            )
        except Exception:
            logger.exception("Could not store Foundry API key for %s", account.resource_name)

    async def _push_newapi_key(self, account: ProviderAccount, api_key: str) -> str:
        from app.services.kimi_newapi import (
            _channel_update_body,
            _put_channel,
            existing_pool_channel,
            invalidate_kimi_pool_cache,
            kimi_pool_gateway,
            list_kimi_pool_channels,
        )
        from app.services.owner_tag import resource_key

        resource_name = (account.resource_name or "").strip()
        endpoint = account.endpoint or ""
        if not resource_name and not endpoint:
            return ""
        try:
            gateway = kimi_pool_gateway()
            channels = await list_kimi_pool_channels(force=True)
            channel = existing_pool_channel(
                channels,
                {key for key in (resource_key(resource_name), resource_key(endpoint)) if key},
                {
                    "new_api_name": account.new_api_name or "",
                    "new_api_channel_id": str(account.new_api_channel_id or ""),
                },
            )
            if channel is None:
                return ""
            await _put_channel(
                gateway,
                _channel_update_body(
                    channel,
                    (channel.get("name") or account.new_api_name or "").strip(),
                    key=api_key,
                ),
            )
            invalidate_kimi_pool_cache()
            return ""
        except Exception as exc:
            logger.exception("Could not update NewAPI after key rotate for %s", account.name)
            return str(exc)[:240]

    async def test_connection(self, account_id: int) -> dict:
        account = await self._get_or_raise(account_id)
        provider = get_provider(account.provider_type)
        credentials = self._credentials_for(account)
        try:
            await provider.list_deployments(credentials, account.resource_id)
            return {"status": "ok"}
        except Exception as exc:  # noqa: BLE001 - surfaced directly to the admin UI
            if not azure_resource_missing(str(exc)):
                return {"status": "error", "detail": str(exc)}
            try:
                await provider.discover_resources(credentials)
            except Exception as inner:  # noqa: BLE001
                return {"status": "error", "detail": str(inner)}
            return {
                "status": "ok",
                "detail": "Service principal is valid. The Foundry account is missing — redeploy to recreate it.",
            }

    async def discover_for_account(self, account_id: int) -> list[dict]:
        account = await self._get_or_raise(account_id)
        provider = get_provider(account.provider_type)
        resources = await provider.discover_resources(self._credentials_for(account))
        return [resource.__dict__ for resource in resources]

    async def update_account(self, account_id: int, payload: AccountUpdateRequest) -> ProviderAccount:
        account = await self._get_or_raise(account_id)
        if payload.name is not None and payload.name != account.name:
            existing = await self._account_repository.get_by_name(payload.name)
            if existing is not None:
                raise DuplicateAccountError("An account with that name already exists.")
        resource_fields = {
            "resource_id": payload.resource_id if payload.resource_id is not None else account.resource_id,
            "resource_group": payload.resource_group if payload.resource_group is not None else account.resource_group,
            "resource_name": payload.resource_name if payload.resource_name is not None else account.resource_name,
            "endpoint": payload.endpoint if payload.endpoint is not None else account.endpoint,
            "kind": payload.kind if payload.kind is not None else account.kind,
            "location": payload.location if payload.location is not None else account.location,
        }
        if any(
            getattr(payload, field) is not None
            for field in ("resource_id", "resource_group", "resource_name", "endpoint", "kind", "location")
        ):
            filled = _filled_resource(subscription_id=account.subscription_id, **resource_fields)
            for key, value in filled.items():
                setattr(account, key, value)
        if payload.name is not None:
            account.name = payload.name
        if payload.credits_limit is not None:
            _apply_credit_grant(account, payload.credits_limit, manual=True)
        if payload.credits_limit_manual is not None:
            account.credits_limit_manual = payload.credits_limit_manual
        if payload.group_tag is not None:
            account.group_tag = normalize_group(payload.group_tag)
        if payload.owner_tag is not None:
            try:
                account.owner_tag = parse_owner_tag(payload.owner_tag)
            except ValueError as exc:
                raise AccountValidationError(str(exc)) from exc
        else:
            apply_owner_to_account(account, await self._account_repository.list_all())
        try:
            saved = await self._account_repository.save(account)
        except IntegrityError as exc:
            raise DuplicateAccountError("An account with that name already exists.") from exc
        await self._attach_emails([saved])
        return saved

    def _undeploy_payload(self, account: ProviderAccount) -> dict[str, str]:
        return {
            "AZURE_TENANT_ID": account.tenant_id,
            "AZURE_CLIENT_ID": account.client_id,
            "AZURE_CLIENT_SECRET": self._secret_box.decrypt(account.client_secret_encrypted),
            "AZURE_SUBSCRIPTION_ID": account.subscription_id,
            "name": account.name,
            "account_name": account.resource_name or "",
            "resource_group": account.resource_group or "",
            "azure_openai_endpoint": account.endpoint or "",
            "person_associated": account.owner_tag or "",
            "group_tag": account.group_tag or "",
            "new_api_name": account.new_api_name or "",
        }

    async def _redeploy_payload(self, account: ProviderAccount) -> dict[str, str]:
        payload = self._undeploy_payload(account)
        if account.new_api_priority is not None:
            payload["new_api_priority"] = str(account.new_api_priority)
        if account.new_api_weight is not None:
            payload["new_api_weight"] = str(account.new_api_weight)
        if account.new_api_channel_id is not None:
            payload["new_api_channel_id"] = str(account.new_api_channel_id)
        payload["new_api_enable"] = "1"
        session = self._account_repository._session
        from sqlalchemy import func, select

        from app.models.azure_service_principal import AzureServicePrincipal

        wanted = (account.subscription_id or "").strip().lower()
        if wanted:
            stored = (
                await session.execute(
                    select(AzureServicePrincipal).where(func.lower(AzureServicePrincipal.subscription_id) == wanted)
                )
            ).scalar_one_or_none()
            if stored and stored.account_holder:
                payload["account_holder"] = stored.account_holder
        return payload

    async def queue_redeploy(self, account_id: int) -> dict[str, str | None]:
        account = await self._get_or_raise(account_id)
        if not account.client_secret_encrypted:
            raise AccountValidationError("This account has no service principal secret to redeploy with.")
        from app.services.deploy_defaults import resolve_routing
        from app.services.deploy_job_runner import start_kimi_deploy_job
        from app.services.kimi_deploy_service import KimiDeployError

        session = self._account_repository._session
        payload = await self._redeploy_payload(account)
        target_id = account.id

        async def on_complete(results) -> None:
            if not results or not getattr(results[0], "ok", False):
                return
            from app.core.crypto import get_secret_box
            from app.database import SessionLocal
            from app.repositories.account_repository import AccountRepository

            async with SessionLocal() as db:
                service = AccountService(AccountRepository(db), get_secret_box())
                summary = await service.finalize_redeploy(target_id)
            result = results[0]
            if summary.get("new_api_status") is not None:
                result.new_api_status = summary["new_api_status"]
                result.new_api_present = True
                result.new_api_status_label = "enabled" if summary["new_api_status"] == 1 else "disabled"
            if summary.get("new_api_name"):
                result.new_api_name = summary["new_api_name"]
            if summary.get("resource_name"):
                result.account_name = summary["resource_name"]
            if summary.get("endpoint"):
                result.azure_openai_endpoint = summary["endpoint"]

        try:
            priority, weight = await resolve_routing(session, account.new_api_priority, account.new_api_weight)
            job = await start_kimi_deploy_job([payload], 1, priority, weight, on_complete=on_complete)
        except KimiDeployError as exc:
            raise AccountValidationError(str(exc)) from exc
        return {"status": "queued", "job_id": job.job_id, "name": account.name}

    async def finalize_redeploy(self, account_id: int) -> dict:
        account = await self._get_or_raise(account_id)
        session = self._account_repository._session
        await self._retarget_newapi_channel(account)
        new_api: dict = {"status": "skipped"}
        try:
            from app.services.new_api_service import set_gateway_status

            new_api = await set_gateway_status(session, account_id, 1, "O1")
        except Exception as exc:  # noqa: BLE001
            new_api = {"status": "error", "error": str(exc)[:300]}
        from app.dependencies import get_sync_orchestrator

        sync = await get_sync_orchestrator().sync_one(account_id)
        session.expire_all()
        account = await self._get_or_raise(account_id)
        return {
            "name": account.name,
            "resource_name": account.resource_name,
            "endpoint": account.endpoint,
            "last_sync_status": account.last_sync_status,
            "last_sync_error": account.last_sync_error,
            "new_api_name": account.new_api_name,
            "new_api_status": account.new_api_status,
            "new_api_status_o1": account.new_api_status_o1,
            "sync": sync,
            "new_api": new_api,
        }

    async def _retarget_newapi_channel(self, account: ProviderAccount) -> None:
        from app.services.kimi_newapi import (
            _channel_update_body,
            _put_channel,
            existing_pool_channel,
            kimi_pool_gateway,
            list_kimi_pool_channels,
            openai_base_url,
        )
        from app.services.new_api_service import _host_key
        from app.services.openai_key_store import decrypt_foundry_key
        from app.services.owner_tag import resource_key

        resource_name = (account.resource_name or "").strip()
        endpoint = account.endpoint or ""
        if not resource_name and not endpoint:
            return
        try:
            gateway = kimi_pool_gateway()
            channels = await list_kimi_pool_channels(force=True)
        except Exception:
            return
        channel = existing_pool_channel(
            channels,
            {key for key in (resource_key(resource_name), resource_key(endpoint)) if key},
            {
                "new_api_name": account.new_api_name or "",
                "new_api_channel_id": str(account.new_api_channel_id or ""),
            },
        )
        if channel is None:
            return
        target = openai_base_url(endpoint, resource_name)
        if _host_key(channel.get("base_url")) == _host_key(target):
            return
        api_key = await decrypt_foundry_key(
            self._account_repository._session, account.subscription_id, resource_name
        )
        if not api_key:
            return
        await _put_channel(
            gateway,
            _channel_update_body(
                channel,
                (channel.get("name") or account.new_api_name or "").strip(),
                base_url=target,
                key=api_key,
            ),
        )
        from app.services.kimi_newapi import invalidate_kimi_pool_cache

        invalidate_kimi_pool_cache()

    async def delete_account(self, account_id: int) -> None:
        account = await self._get_or_raise(account_id)
        session = self._account_repository._session
        from app.services.google_sheet_inventory import mark_deleted_inventory
        from app.services.kimi_deploy_service import KimiDeployError, delete_accounts
        from app.services.openai_key_store import drop_foundry_key
        from app.services.azure_inventory_cache import drop_azure_inventory
        from app.services.service_principal_store import drop_stored_principal
        from app.services.submit_service import release_join_for_subscription

        last_sync = account.last_sync_error or ""
        try:
            results = await delete_accounts([self._undeploy_payload(account)], jobs=1, session=session)
        except KimiDeployError as exc:
            if not azure_stack_already_gone(str(exc), last_sync):
                raise AccountValidationError(str(exc)) from exc
            results = None
        if results and not results[0].ok:
            if not azure_stack_already_gone(results[0].error, last_sync):
                raise AccountValidationError(results[0].error or "Could not undeploy the Azure stack.")
        await mark_deleted_inventory(
            endpoint=account.endpoint,
            resource_name=account.resource_name,
            proxy_name=account.new_api_name,
        )
        await drop_foundry_key(session, account.subscription_id, account.resource_name)
        await drop_azure_inventory(account.subscription_id, account.resource_name)
        await drop_stored_principal(session, account.subscription_id)
        await release_join_for_subscription(session, account.subscription_id, account.name)
        await self._account_repository.delete(account)

    async def _get_or_raise(self, account_id: int) -> ProviderAccount:
        account = await self._account_repository.get(account_id)
        if account is None:
            raise AccountNotFoundError(f"Account {account_id} not found")
        return account

    def _credentials_for(self, account: ProviderAccount) -> ProviderCredentials:
        return ProviderCredentials(
            tenant_id=account.tenant_id,
            client_id=account.client_id,
            client_secret=self._secret_box.decrypt(account.client_secret_encrypted),
            subscription_id=account.subscription_id,
        )


def _foundry_resource_id(account: ProviderAccount) -> str:
    resource_id = (account.resource_id or "").strip()
    if resource_id:
        return resource_id
    if account.subscription_id and account.resource_group and account.resource_name:
        return (
            f"/subscriptions/{account.subscription_id}/resourceGroups/{account.resource_group}"
            f"/providers/Microsoft.CognitiveServices/accounts/{account.resource_name}"
        )
    return ""


def _filled_resource(
    *,
    subscription_id: str,
    resource_name: str,
    resource_id: str = "",
    resource_group: str = "",
    endpoint: str = "",
    kind: str = "",
    location: str = "",
) -> dict[str, str]:
    name = (resource_name or "").strip()
    if not name:
        raise AccountValidationError("Resource name is required.")
    group = (resource_group or "").strip() or "manual"
    kind_value = (kind or "").strip() or "AIServices"
    location_value = (location or "").strip()
    endpoint_value = (endpoint or "").strip()
    if not endpoint_value:
        endpoint_value = f"https://{name}.cognitiveservices.azure.com/"
    resource_id_value = (resource_id or "").strip()
    if not resource_id_value:
        resource_id_value = (
            f"/subscriptions/{subscription_id}/resourceGroups/{group}"
            f"/providers/Microsoft.CognitiveServices/accounts/{name}"
        )
    return {
        "resource_name": name,
        "resource_group": group,
        "kind": kind_value,
        "location": location_value,
        "endpoint": endpoint_value,
        "resource_id": resource_id_value,
    }


def _apply_credit_grant(account: ProviderAccount, limit: float | None, *, manual: bool) -> None:
    if limit is None:
        return
    if limit <= 0:
        raise AccountValidationError("Credit grant must be greater than 0.")
    account.credits_limit = float(limit)
    if account.credits_remaining is None or account.credits_remaining > limit:
        account.credits_remaining = float(limit)
    if account.credits_used is None:
        account.credits_used = 0.0
    account.credits_currency = account.credits_currency or "USD"
    account.credits_unit = "currency"
    account.credits_label = "Manual credit grant" if manual else (account.credits_label or "Credits")
    account.credits_available = True
    if manual:
        account.credits_limit_manual = True


async def _unique_account_name(
    repo: AccountRepository,
    *,
    preferred: str,
    resource_name: str,
    subscription_id: str,
) -> str:
    cleaned = " ".join((preferred or "").split())[:128]
    resource_name = (resource_name or "").strip()
    taken: set[str] = set()
    reuse: str | None = None
    for row in await repo.list_all():
        same_resource = bool(
            resource_name
            and (row.subscription_id or "") == subscription_id
            and (row.resource_name or "").lower() == resource_name.lower()
        )
        if same_resource:
            reuse = row.name
            continue
        if row.name:
            taken.add(row.name.lower())
    if reuse:
        return reuse
    return allocate_unique_name(cleaned or resource_name or f"kimi-{subscription_id[:8]}", taken)


async def _link_kimi_deployment(session, account_id: int, deployment_name: str) -> None:
    registered = await RegisteredModelRepository(session).get_by_name(deployment_name)
    if registered is None:
        return
    models = ModelRepository(session)
    existing = await models.find_by_account_and_deployment(account_id, deployment_name)
    if existing is not None:
        return
    await models.create(
        MonitoredModel(
            provider_account_id=account_id,
            registered_model_id=registered.id,
            deployment_name=deployment_name,
            enabled=True,
        )
    )
