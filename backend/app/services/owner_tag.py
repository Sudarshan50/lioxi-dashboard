import csv
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.provider_account import ProviderAccount
from app.repositories.account_repository import AccountRepository

# Name,Endpoint CSV used to stamp owner tags onto accounts.
# Real mapping lives at this path locally and is gitignored. See imp_data.csv.example.
_CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "imp_data.csv"
UNTAGGED = "__none__"


OWNER_TAG_MAX = 64


def canonical_owner(name: str) -> str:
    return " ".join((name or "").split()).title()


def parse_owner_tag(value: str | None) -> str | None:
    owner = canonical_owner(value or "")
    if not owner:
        return None
    if len(owner) > OWNER_TAG_MAX:
        raise ValueError(f"Name tag must be {OWNER_TAG_MAX} characters or fewer.")
    return owner


def resource_key(value: str | None) -> str:
    """Exact Azure resource name: first host label of an endpoint, or a bare resource name."""
    raw = (value or "").strip().lower().rstrip("/")
    if not raw:
        return ""
    if "://" in raw or "." in raw.split("/")[0]:
        if "://" not in raw:
            raw = "https://" + raw
        host = urlparse(raw).hostname or raw.split("/")[0]
        return host.split(":")[0].split(".")[0] if host else ""
    return raw


def _mapping_rows() -> list[list[str]]:
    text = _CSV_PATH.read_text(encoding="utf-8-sig")
    delimiter = "\t" if "\t" in text.split("\n", 1)[0] else ","
    return list(csv.reader(text.splitlines(), delimiter=delimiter))


def owner_by_resource() -> dict[str, str]:
    """CSV Name keyed by the exact resource name from the Endpoint column.

    A resource that appears under two different names is left unmapped.
    """
    if not _CSV_PATH.exists():
        return {}
    mapping: dict[str, str] = {}
    conflicted: set[str] = set()
    for row in _mapping_rows():
        if len(row) < 2:
            continue
        owner = canonical_owner(row[0])
        key = resource_key(row[1])
        if not owner or not key or owner.lower() == "name":
            continue
        existing = mapping.get(key)
        if existing and existing != owner:
            conflicted.add(key)
            continue
        mapping[key] = owner
    for key in conflicted:
        mapping.pop(key, None)
    return mapping


def account_resource_key(account: ProviderAccount) -> str:
    """Exact resource from resource_name and endpoint. Empty if they disagree or are missing."""
    from_name = resource_key(account.resource_name)
    from_endpoint = resource_key(account.endpoint)
    if from_name and from_endpoint and from_name != from_endpoint:
        return ""
    return from_name or from_endpoint


def unique_owners(accounts: list[ProviderAccount]) -> dict[int, str | None]:
    """Tag only when exactly one portal account has that CSV resource. Duplicates stay untagged."""
    mapping = owner_by_resource()
    keyed: dict[str, list[ProviderAccount]] = defaultdict(list)
    for account in accounts:
        key = account_resource_key(account)
        if key and key in mapping:
            keyed[key].append(account)
    result: dict[int, str | None] = {id(account): None for account in accounts}
    for key, group in keyed.items():
        if len(group) != 1:
            continue
        result[id(group[0])] = mapping[key]
    return result


def owner_for_account(endpoint: str | None, resource_name: str | None = None) -> str | None:
    mapping = owner_by_resource()
    if not mapping:
        return None
    from_name = resource_key(resource_name)
    from_endpoint = resource_key(endpoint)
    if from_name and from_endpoint and from_name != from_endpoint:
        return None
    key = from_name or from_endpoint
    return mapping.get(key) if key else None


def apply_owner_to_account(account: ProviderAccount, accounts: list[ProviderAccount] | None = None) -> bool:
    """Fill an empty tag from the CSV map. Never overwrite a tag set at create or edit."""
    if account.owner_tag:
        return False
    if accounts is None:
        owner = owner_for_account(account.endpoint, account.resource_name)
    else:
        owner = unique_owners(accounts).get(id(account))
    if not owner:
        return False
    account.owner_tag = owner
    return True


async def apply_owner_tags(session: AsyncSession) -> int:
    """Fill empty tags from an exact CSV resource match. Existing tags are left alone."""
    accounts = await AccountRepository(session).list_all()
    desired = unique_owners(accounts)
    changed = 0
    for account in accounts:
        if account.owner_tag:
            continue
        owner = desired.get(id(account))
        if not owner:
            continue
        account.owner_tag = owner
        changed += 1
    if changed:
        await session.commit()
    return changed
