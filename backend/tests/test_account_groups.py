import unittest
from types import SimpleNamespace

from app.services.account_group_service import is_10k_account, is_1k_account


def _account(name="acct", credits_limit=None, credits_currency="USD"):
    return SimpleNamespace(name=name, credits_limit=credits_limit, credits_currency=credits_currency)


class GrantTierClassification(unittest.TestCase):
    def test_1k_from_grant(self):
        self.assertTrue(is_1k_account(_account(credits_limit=1000)))
        self.assertTrue(is_1k_account(_account(credits_limit=1200)))
        self.assertFalse(is_10k_account(_account(credits_limit=1000)))

    def test_10k_from_grant(self):
        self.assertTrue(is_10k_account(_account(credits_limit=10000)))
        self.assertTrue(is_10k_account(_account(credits_limit=10200)))
        self.assertTrue(is_10k_account(_account(credits_limit=9500)))
        self.assertFalse(is_1k_account(_account(credits_limit=10000)))

    def test_custom_grant_beats_name(self):
        self.assertTrue(is_10k_account(_account(name="foo-1k", credits_limit=9500)))
        self.assertFalse(is_1k_account(_account(name="foo-1k", credits_limit=9500)))
        self.assertTrue(is_1k_account(_account(name="foo-10k", credits_limit=1200)))
        self.assertFalse(is_10k_account(_account(name="foo-10k", credits_limit=1200)))

    def test_name_used_when_grant_missing(self):
        self.assertTrue(is_10k_account(_account(name="foo-10k")))
        self.assertFalse(is_1k_account(_account(name="foo-10k")))

    def test_1k_name(self):
        self.assertTrue(is_1k_account(_account(name="team_1k")))
        self.assertFalse(is_10k_account(_account(name="team_1k")))
