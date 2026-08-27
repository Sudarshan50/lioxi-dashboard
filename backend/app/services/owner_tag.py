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
PERSON_ALIASES = (
    "person_associated",
    "person_assoicated",
    "person_associted",
    "personAssociated",
    "personAssoicated",
    "personAssocited",
    "PERSON_ASSOCIATED",
    "owner_tag",
    "ownerTag",
)


def _norm_field_key(key: str) -> str:
    return key.replace("_", "").replace("-", "").lower()


def is_person_field_key(key: str) -> bool:
    """Match person_associated and common typos (assoicated, associted)."""
    norm = _norm_field_key(key)
    if norm in {"ownertag", "ownertags"}:
        return True
    if not norm.startswith("person"):
        return False
    rest = norm[len("person") :]
    return rest.startswith("assoc") or rest.startswith("assoic")


def canonical_owner(name: str) -> str:
    # Split on whitespace; tokens with - or ' stay as typed, else First-upper + rest-lower.
    words: list[str] = []
    for token in (name or "").split():
        if "-" in token or "'" in token:
            words.append(token)
        else:
            words.append(token[:1].upper() + token[1:].lower())
    return " ".join(words)


def parse_owner_tag(value: str | None) -> str | None:
    owner = canonical_owner(value or "")
    if not owner:
        return None
    if len(owner) > OWNER_TAG_MAX:
        raise ValueError(f"Name tag must be {OWNER_TAG_MAX} characters or fewer.")
    return owner


def _payload_person_text(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list) and value and all(isinstance(item, str) for item in value):
        return value[0]
    return None


def person_from_payload(raw: dict | None) -> str | None:
    if not raw:
        return None
    lookup = {_norm_field_key(str(key)): value for key, value in raw.items()}
    for alias in PERSON_ALIASES:
        text = _payload_person_text(lookup.get(_norm_field_key(alias)))
        if text and text.strip():
            return parse_owner_tag(text)
    for key, value in lookup.items():
        if not is_person_field_key(key):
            continue
        text = _payload_person_text(value)
        if text and text.strip():
            return parse_owner_tag(text)
    return None


async def apply_person_associated_tags(session: AsyncSession, accounts: list[dict]) -> int:
    """Stamp person_associated onto portal rows whose resource key is in the payload.

    Never guess a sibling when wanted is empty or no resource matched; deploy upsert
    tags the Kimi row. Empty tags are filled; a different existing tag is left alone.
    """
    if not accounts:
        return 0
    portal = await AccountRepository(session).list_all()
    changed = 0
    for payload in accounts:
        person = person_from_payload(payload)
        sub = str(payload.get("AZURE_SUBSCRIPTION_ID") or payload.get("subscription_id") or "").strip()
        if not person or not sub:
            continue
        wanted = {
            key
            for value in (
                payload.get("account_name"),
                payload.get("azure_openai_endpoint"),
                payload.get("resource_name"),
            )
            if (key := resource_key(str(value) if value else None))
        }
        if not wanted:
            continue
        client_id = str(payload.get("AZURE_CLIENT_ID") or payload.get("client_id") or "").strip()
        candidates = [account for account in portal if (account.subscription_id or "").strip() == sub]
        if client_id:
            scoped = [account for account in candidates if account.client_id == client_id]
            if scoped:
                candidates = scoped
        for account in candidates:
            key = account_resource_key(account)
            if not key or key not in wanted:
                continue
            if account.owner_tag:
                continue
            account.owner_tag = person
            changed += 1
    if changed:
        await session.commit()
    return changed


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


def _owner_key(account: ProviderAccount) -> int:
    """Database PK when persisted; object identity only for unsaved rows."""
    pk = getattr(account, "id", None)
    return pk if pk is not None else id(account)


def unique_owners(accounts: list[ProviderAccount]) -> dict[int, str | None]:
    """Tag only when exactly one portal account has that CSV resource. Duplicates stay untagged."""
    mapping = owner_by_resource()
    keyed: dict[str, list[ProviderAccount]] = defaultdict(list)
    seen: set[int] = set()
    unique: list[ProviderAccount] = []
    for account in accounts:
        pk = _owner_key(account)
        if pk in seen:
            continue
        seen.add(pk)
        unique.append(account)
        key = account_resource_key(account)
        if key and key in mapping:
            keyed[key].append(account)
    result: dict[int, str | None] = {_owner_key(account): None for account in unique}
    for key, group in keyed.items():
        if len(group) != 1:
            continue
        result[_owner_key(group[0])] = mapping[key]
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
        owner = unique_owners(accounts).get(_owner_key(account))
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
        owner = desired.get(_owner_key(account))
        if not owner:
            continue
        account.owner_tag = owner
        changed += 1
    if changed:
        await session.commit()
    return changed
