import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.join_group import (
    GROUP_SB,
    GROUP_VCS,
    compact_person,
    email_domain_label,
    email_initials,
    looks_like_managed_stack,
    stack_suffix,
    group_label,
    is_vcs,
    join_account_name,
    normalize_group,
    vcs_account_name,
)
from app.services.kimi_newapi import (
    KIMI_CHANNEL_PREFIX,
    VCS_CHANNEL_PREFIX,
    channel_index,
    channel_prefix,
    is_kimi_channel_name,
    next_kimi_channel_name,
    next_kimi_index,
)


def _row(name: str) -> dict:
    return {"name": name, "tag": " kimi-k3-pool"}


class NormalizeGroupTests(unittest.TestCase):
    def test_known_groups_round_trip(self):
        self.assertEqual(normalize_group("sb"), GROUP_SB)
        self.assertEqual(normalize_group("VCS"), GROUP_VCS)
        self.assertEqual(normalize_group("  vcs  "), GROUP_VCS)

    def test_unknown_and_missing_read_as_sb(self):
        for value in (None, "", "   ", "sbx", "kimi", "0"):
            self.assertEqual(normalize_group(value), GROUP_SB, value)

    def test_helpers(self):
        self.assertTrue(is_vcs("vcs"))
        self.assertFalse(is_vcs(None))
        self.assertEqual(group_label("vcs"), "VCS")
        self.assertEqual(group_label(None), "SB")


class EmailNameTests(unittest.TestCase):
    def test_initials_and_domain(self):
        self.assertEqual(email_initials("john.doe@corp.com"), "jd")
        self.assertEqual(email_initials("rahul@acme.io"), "r")
        self.assertEqual(email_domain_label("john.doe@corp.com"), "corp")
        self.assertEqual(email_domain_label("a@acme.co.uk"), "acme")

    def test_vcs_account_name(self):
        self.assertEqual(vcs_account_name("john.doe@corp.com"), "jd-corp")
        self.assertEqual(vcs_account_name("rahul@acme.io"), "r-acme")
        self.assertEqual(vcs_account_name("rahul_kumar_singh@x.org"), "rks-x")

    def test_case_and_plus_address_are_the_same_person(self):
        self.assertEqual(vcs_account_name("John_Doe+spam@Corp.COM"), "jd-corp")

    def test_unusable_email_is_empty(self):
        for value in (None, "", "weird", "@corp.com", "john@", "...@corp.com"):
            self.assertEqual(vcs_account_name(value), "", value)


class JoinAccountNameTests(unittest.TestCase):
    def test_sb_keeps_the_lioxi_name(self):
        self.assertEqual(join_account_name(GROUP_SB, "john.doe@corp.com", "Lioxi-Gaurav", "Gaurav"), "Lioxi-Gaurav")
        self.assertEqual(join_account_name(None, "john.doe@corp.com", "Lioxi-Gaurav", "Gaurav"), "Lioxi-Gaurav")

    def test_vcs_uses_the_enrolled_name(self):
        self.assertEqual(join_account_name(GROUP_VCS, "ayushpandey747721@gmail.com", "Lioxi-Gaurav15", "Ayush P"), "AyushP")
        self.assertEqual(join_account_name(GROUP_VCS, "tanishkumar741@gmail.com", "Lioxi-Gaurav17", "Tanish"), "Tanish")

    def test_vcs_falls_back_to_the_email_then_the_sb_name(self):
        self.assertEqual(join_account_name(GROUP_VCS, "john.doe@corp.com", "Lioxi-Gaurav", None), "jd-corp")
        self.assertEqual(join_account_name(GROUP_VCS, None, "Lioxi-Gaurav", None), "Lioxi-Gaurav")
        self.assertEqual(join_account_name(GROUP_VCS, "not-an-email", "Lioxi-Gaurav", ""), "Lioxi-Gaurav")

    def test_compact_person_drops_spaces_for_azure(self):
        self.assertEqual(compact_person("Ayush P"), "AyushP")
        self.assertEqual(compact_person(None), "")


class StackSuffixTests(unittest.TestCase):
    def test_suffix_per_group(self):
        self.assertEqual(stack_suffix(GROUP_SB), "kimi")
        self.assertEqual(stack_suffix(GROUP_VCS), "proxy")
        self.assertEqual(stack_suffix(None), "kimi")

    def test_recognises_both_stacks(self):
        self.assertTrue(looks_like_managed_stack("lioxigaurav1-kimi-7vh3tr", "rg-lioxigaurav1-kimi"))
        self.assertTrue(looks_like_managed_stack("ambarish-proxy-a1b2c3", "rg-ambarish-proxy"))

    def test_rejects_unrelated_resources(self):
        # This guard gates deletion, so a half match must never pass.
        self.assertFalse(looks_like_managed_stack("ss927-mt5ljtz2", "rg-abk2-1k"))
        self.assertFalse(looks_like_managed_stack("ambarish-proxy-a1", "prod-ambarish-proxy"))
        self.assertFalse(looks_like_managed_stack("plainname", "rg-ambarish-proxy"))
        self.assertFalse(looks_like_managed_stack("ambarish-proxy-a1", "rg-ambarish-kimi"))


class ChannelNamingTests(unittest.TestCase):
    def test_prefix_per_group(self):
        self.assertEqual(channel_prefix(GROUP_SB), KIMI_CHANNEL_PREFIX)
        self.assertEqual(channel_prefix(GROUP_VCS), VCS_CHANNEL_PREFIX)
        self.assertEqual(channel_prefix(None), KIMI_CHANNEL_PREFIX)

    def test_channel_index_reads_both_series(self):
        self.assertEqual(channel_index("kimi-k3-500k-proxy-12"), 12)
        self.assertEqual(channel_index("cs-proxy-3"), 3)
        self.assertIsNone(channel_index("azure-old"))
        self.assertIsNone(channel_index(None))

    def test_is_kimi_channel_name_covers_vcs(self):
        self.assertTrue(is_kimi_channel_name("kimi-k3-500k-proxy-1"))
        self.assertTrue(is_kimi_channel_name("CS-PROXY-9"))
        self.assertFalse(is_kimi_channel_name("cs-proxy"))
        self.assertFalse(is_kimi_channel_name("some-other-channel"))

    def test_series_advance_independently(self):
        channels = [_row("kimi-k3-500k-proxy-20"), _row("cs-proxy-2"), _row("unrelated")]
        self.assertEqual(next_kimi_index(channels, GROUP_SB), 21)
        self.assertEqual(next_kimi_index(channels, GROUP_VCS), 3)
        self.assertEqual(next_kimi_channel_name(channels, GROUP_SB), "kimi-k3-500k-proxy-21")
        self.assertEqual(next_kimi_channel_name(channels, GROUP_VCS), "cs-proxy-3")

    def test_empty_pool_starts_each_series_at_one(self):
        self.assertEqual(next_kimi_channel_name([], GROUP_SB), "kimi-k3-500k-proxy-1")
        self.assertEqual(next_kimi_channel_name([], GROUP_VCS), "cs-proxy-1")

    def test_a_gap_is_not_reused(self):
        channels = [_row("cs-proxy-1"), _row("cs-proxy-5")]
        self.assertEqual(next_kimi_channel_name(channels, GROUP_VCS), "cs-proxy-6")


class UniqueNameTests(unittest.TestCase):
    def test_collisions_get_a_numeric_suffix(self):
        from app.services.account_service import allocate_unique_name

        taken = {"jd-corp"}
        first = allocate_unique_name(join_account_name(GROUP_VCS, "john.doe@corp.com", "x"), taken)
        self.assertEqual(first, "jd-corp1")
        taken.add(first.lower())
        self.assertEqual(
            allocate_unique_name(join_account_name(GROUP_VCS, "jane.dawson@corp.com", "x"), taken),
            "jd-corp2",
        )


class GroupFallbackTests(unittest.TestCase):
    """A deploy that carries no group must keep the account's own."""

    def _resolve(self, stated, rows, resource="jd-kimi-ab12cd"):
        import app.repositories.account_repository as repo_module

        class _Repo:
            def __init__(self, _session):
                pass

            async def list_by_subscription(self, _sub):
                return list(rows)

        with patch.object(repo_module, "AccountRepository", _Repo):
            from app.services.join_group import resolve_group_for_resource

            return asyncio.run(
                resolve_group_for_resource(object(), stated, "sub-1", resource)
            )

    def test_payload_group_wins(self):
        rows = [SimpleNamespace(resource_name="jd-kimi-ab12cd", group_tag=GROUP_SB)]
        self.assertEqual(self._resolve("vcs", rows), GROUP_VCS)

    def test_falls_back_to_the_portal_account(self):
        rows = [SimpleNamespace(resource_name="jd-proxy-ab12cd", group_tag=GROUP_VCS)]
        self.assertEqual(self._resolve(None, rows, "jd-proxy-ab12cd"), GROUP_VCS)

    def test_subscription_fallback_survives_a_new_random_suffix(self):
        # After a purge and redeploy the rand6 changes, so the exact resource
        # name no longer matches; the subscription still identifies the owner.
        rows = [SimpleNamespace(resource_name="jd-proxy-OLDSUF", group_tag=GROUP_VCS)]
        self.assertEqual(self._resolve(None, rows, "jd-proxy-newsuf"), GROUP_VCS)

    def test_exact_resource_match_wins_over_a_sibling(self):
        rows = [
            SimpleNamespace(resource_name="other-kimi-111111", group_tag=GROUP_SB),
            SimpleNamespace(resource_name="jd-proxy-ab12cd", group_tag=GROUP_VCS),
        ]
        self.assertEqual(self._resolve(None, rows, "jd-proxy-ab12cd"), GROUP_VCS)

    def test_unknown_subscription_is_sb(self):
        self.assertEqual(self._resolve(None, []), GROUP_SB)

    def test_no_session_is_sb(self):
        from app.services.join_group import resolve_group_for_resource

        self.assertEqual(
            asyncio.run(resolve_group_for_resource(None, None, "sub-1", "res")), GROUP_SB
        )

    def test_a_repository_error_never_fails_the_deploy(self):
        import app.repositories.account_repository as repo_module

        class _Boom:
            def __init__(self, _session):
                pass

            async def list_by_subscription(self, _sub):
                raise RuntimeError("db down")

        with patch.object(repo_module, "AccountRepository", _Boom):
            from app.services.join_group import resolve_group_for_resource

            self.assertEqual(
                asyncio.run(resolve_group_for_resource(object(), None, "sub-1", "r")), GROUP_SB
            )


if __name__ == "__main__":
    unittest.main()
