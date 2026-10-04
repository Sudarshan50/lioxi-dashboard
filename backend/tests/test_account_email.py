import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.account_service import AccountService
from app.services.service_principal_store import add_holder_emails, email_or_none


class HolderEmails(unittest.TestCase):
    def test_keeps_first_real_email(self):
        by_sub = add_holder_emails(
            {},
            [
                ("Sub-1", "alice@example.com"),
                ("sub-1", "other@example.com"),
                ("sub-2", "not an email"),
                ("", "bob@example.com"),
            ],
        )
        self.assertEqual(by_sub, {"sub-1": "alice@example.com"})

    def test_falls_back_when_primary_missing(self):
        by_sub = add_holder_emails({}, [("sub-9", None)])
        add_holder_emails(by_sub, [("SUB-9", "bob@contoso.com")])
        self.assertEqual(by_sub["sub-9"], "bob@contoso.com")

    def test_email_or_none(self):
        self.assertEqual(email_or_none("  jane@corp.com "), "jane@corp.com")
        self.assertIsNone(email_or_none("Jane"))
        self.assertIsNone(email_or_none(""))


class AttachAccountEmails(unittest.IsolatedAsyncioTestCase):
    async def test_list_accounts_sets_email(self):
        account = SimpleNamespace(subscription_id="Sub-1", name="Alice")
        repo = SimpleNamespace(_session=object(), list_all=AsyncMock(return_value=[account]))
        service = AccountService(repo, SimpleNamespace())
        with patch(
            "app.services.service_principal_store.emails_by_subscription",
            new=AsyncMock(return_value={"sub-1": "alice@example.com"}),
        ):
            rows = await service.list_accounts()
        self.assertEqual(rows[0].email, "alice@example.com")

    async def test_list_accounts_leaves_blank_when_unknown(self):
        account = SimpleNamespace(subscription_id="sub-2", name="NoMail")
        repo = SimpleNamespace(_session=object(), list_all=AsyncMock(return_value=[account]))
        service = AccountService(repo, SimpleNamespace())
        with patch(
            "app.services.service_principal_store.emails_by_subscription",
            new=AsyncMock(return_value={}),
        ):
            rows = await service.list_accounts()
        self.assertIsNone(rows[0].email)
