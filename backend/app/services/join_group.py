"""SB / VCS join groups.

Every Join submission and every portal account carries a group tag. SB is the
historical behaviour and stays the default everywhere, including for rows that
predate this column. VCS differs in:

* the portal account name is derived from the Azure login email
  (john.doe@corp.com -> jd-corp) instead of Lioxi-<Name>
* the O1 NewAPI channel is named cs-proxy-X instead of kimi-k3-500k-proxy-X
* the Azure stack is named <slug>-proxy instead of <slug>-kimi

Roles, deploy steps, the FW-Kimi-K3 model deployment, quota tiers, sheets and
Telegram are identical for both groups. Azure names always derive from the
portal account name (slugified), never from the owner tag.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

GROUP_SB = "sb"
GROUP_VCS = "vcs"
GROUPS = (GROUP_SB, GROUP_VCS)
DEFAULT_GROUP = GROUP_SB
GROUP_MAX = 8

GROUP_LABELS = {GROUP_SB: "SB", GROUP_VCS: "VCS"}

# Suffix on the Azure resource group / account / project. It is the marker that
# says "this stack is ours", and reuse and delete guards key off it, so both
# spellings must stay recognised for as long as any stack uses them.
STACK_SUFFIX_BY_GROUP = {GROUP_SB: "kimi", GROUP_VCS: "proxy"}
STACK_SUFFIXES = tuple(STACK_SUFFIX_BY_GROUP.values())

# Tokens inside an email local part: john.doe / john_doe / john-doe / john+tag.
_LOCAL_SPLIT = re.compile(r"[^a-z0-9]+")
_NAME_MAX = 64


def normalize_group(value: str | None) -> str:
    """Any unknown or missing value means SB, so old rows keep working."""
    group = (value or "").strip().lower()
    return group if group in GROUPS else DEFAULT_GROUP


def is_vcs(value: str | None) -> bool:
    return normalize_group(value) == GROUP_VCS


def group_label(value: str | None) -> str:
    return GROUP_LABELS[normalize_group(value)]


def stack_suffix(group: str | None) -> str:
    return STACK_SUFFIX_BY_GROUP[normalize_group(group)]


def looks_like_managed_stack(resource_name: str | None, resource_group: str | None) -> bool:
    """True only for a stack the deploy script created, in either group.

    Matches the exact shape it creates -- rg-<slug>-<suffix> holding
    <slug>-<suffix>-<rand6> -- and nothing else. A looser marker would be
    dangerous: "-proxy-" is ordinary Azure vocabulary, and this same shape
    gates the delete path that removes a whole resource group.
    """
    name = (resource_name or "").strip().lower()
    rg = (resource_group or "").strip().lower()
    for suffix in STACK_SUFFIXES:
        tail = f"-{suffix}"
        if not (rg.startswith("rg-") and rg.endswith(tail) and len(rg) > 3 + len(tail)):
            continue
        slug = rg[3 : -len(tail)]
        if re.fullmatch(rf"{re.escape(slug)}{tail}-[a-z0-9]{{6}}", name):
            return True
    return False


async def resolve_group_for_resource(
    session,
    stated: str | None,
    subscription_id: str,
    resource_name: str = "",
) -> str:
    """Group a deploy belongs to: the payload's, else the portal account's.

    Falls back to the subscription alone when the resource name does not match,
    because a purge-and-redeploy mints a new random suffix and would otherwise
    look like an unknown resource and silently drop the account back to SB.
    Never raises: a naming lookup must not fail a deploy.
    """
    if (stated or "").strip():
        return normalize_group(stated)
    if session is None or not (subscription_id or "").strip():
        return DEFAULT_GROUP
    from app.repositories.account_repository import AccountRepository

    try:
        rows = await AccountRepository(session).list_by_subscription(subscription_id)
    except Exception:  # noqa: BLE001
        logger.warning("Could not read the portal group for %s", subscription_id, exc_info=True)
        return DEFAULT_GROUP
    if not rows:
        return DEFAULT_GROUP
    wanted = (resource_name or "").strip().lower()
    exact = next((row for row in rows if (row.resource_name or "").strip().lower() == wanted), None)
    match = exact or rows[0]
    return normalize_group(getattr(match, "group_tag", None))


def compact_person(name: str | None) -> str:
    """'Ayush P' -> 'AyushP'. Azure names cannot carry spaces."""
    return "".join((name or "").split())


def _local_and_domain(email: str | None) -> tuple[str, str]:
    text = (email or "").strip().lower()
    if "@" not in text:
        return "", ""
    local, _, domain = text.partition("@")
    # A plus-address is the same mailbox; the tag is not part of the person.
    local = local.split("+", 1)[0]
    return local.strip(), domain.strip()


def email_initials(email: str | None) -> str:
    """First letter of each token in the local part: john.doe -> jd, rahul -> r."""
    local, _ = _local_and_domain(email)
    initials = "".join(token[0] for token in _LOCAL_SPLIT.split(local) if token)
    return initials[:16]


def email_domain_label(email: str | None) -> str:
    """First domain label: corp.com -> corp, acme.co.uk -> acme."""
    _, domain = _local_and_domain(email)
    for part in _LOCAL_SPLIT.split(domain):
        if part:
            return part[:24]
    return ""


def vcs_account_name(email: str | None) -> str:
    """jd-corp for john.doe@corp.com. Empty when the login had no usable email."""
    initials = email_initials(email)
    domain = email_domain_label(email)
    if not initials or not domain:
        return ""
    return f"{initials}-{domain}"[:_NAME_MAX]


def join_account_name(
    group: str | None, email: str | None, sb_name: str, person: str | None = None
) -> str:
    """Preferred (pre-collision) portal account name for a Join submission.

    SB keeps Lioxi-<Name>. VCS uses the enrolled name the member picked, which
    is readable and unique per person; it falls back to the sign-in email
    initials, then to the SB name, so a submission is never blocked by a
    missing name or mail claim.
    """
    if not is_vcs(group):
        return sb_name
    return compact_person(person) or vcs_account_name(email) or sb_name
