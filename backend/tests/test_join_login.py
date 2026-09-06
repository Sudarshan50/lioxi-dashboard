import unittest

from app.services.az_cli_session import (
    ACCESS_DENIED_MESSAGE,
    NO_SUBSCRIPTION_MESSAGE,
    SECURITY_DEFAULTS_STILL_BLOCKED,
    accounts_from_arm_subscriptions,
    enabled_subscription_accounts,
    humanize_login_failure,
    is_tenant_level_account,
    interactive_retry_tenant_id,
    is_personal_microsoft_account,
    login_access_denied,
    needs_second_interactive_login,
    no_subscription_message,
    normalize_tenant_id,
    parse_blocked_tenants,
    parse_device_prompt,
    parse_failed_against_tenants,
    tenant_ids_from_json,
)


WORK_TENANT = "11111111-1111-1111-1111-111111111111"


class JoinLoginDecisions(unittest.TestCase):
    def test_personal_gmail_and_outlook(self):
        self.assertTrue(is_personal_microsoft_account("gaurav@gmail.com"))
        self.assertTrue(is_personal_microsoft_account("user@outlook.com"))
        self.assertTrue(is_personal_microsoft_account("user@hotmail.com"))

    def test_unknown_identity_is_personal(self):
        self.assertTrue(is_personal_microsoft_account(""))
        self.assertTrue(is_personal_microsoft_account(None))

    def test_work_email_is_not_personal(self):
        self.assertFalse(is_personal_microsoft_account("owner@contoso.com", WORK_TENANT))

    def test_msa_guest_ext_alias(self):
        self.assertTrue(
            is_personal_microsoft_account("alice_gmail.com#EXT#@contoso.onmicrosoft.com")
        )

    def test_msa_home_tenant_dropped(self):
        self.assertIsNone(normalize_tenant_id("9188040d-6c67-4c5b-b112-36a304b66dad"))
        self.assertEqual(normalize_tenant_id(WORK_TENANT), WORK_TENANT)

    def test_access_denied_from_microsoft_page(self):
        blob = "AADSTS50020: User account does not exist in tenant and You don't have access to this resource"
        self.assertTrue(login_access_denied(blob))
        self.assertEqual(humanize_login_failure(blob), ACCESS_DENIED_MESSAGE)

    def test_interactive_retry_from_az_cli_security_defaults_warning(self):
        buffer = (
            "WARNING: Authentication failed against tenant "
            f"{WORK_TENANT} 'Default Directory': AADSTS530035: Access has been blocked "
            "by security defaults.\n"
            "WARNING: If you need to access subscriptions in the following tenants, "
            "please use `az login --tenant TENANT_ID`.\n"
            f"WARNING: {WORK_TENANT} 'Default Directory'\n"
            "ERROR: No subscriptions found for user@outlook.com."
        )
        self.assertIsNone(interactive_retry_tenant_id(personal=True, buffer=buffer))
        self.assertEqual(interactive_retry_tenant_id(personal=False, buffer=buffer), WORK_TENANT)

    def test_interactive_retry_never_uses_tenant_for_personal(self):
        buffer = (
            f"AADSTS530035 Blocked by security defaults. "
            f"Authentication failed against tenant {WORK_TENANT} 'Default Directory'"
        )
        self.assertTrue(needs_second_interactive_login(buffer))
        self.assertIsNone(interactive_retry_tenant_id(personal=True, buffer=buffer))

    def test_interactive_retry_skips_tenant_on_access_denied(self):
        buffer = (
            f"AADSTS50020 You don't have access to this resource. "
            f"Authentication failed against tenant {WORK_TENANT}"
        )
        self.assertIsNone(interactive_retry_tenant_id(personal=True, buffer=buffer))

    def test_interactive_retry_tenant_only_for_work_defaults_one_tenant(self):
        buffer = (
            f"AADSTS530035 Blocked by security defaults. "
            f"Authentication failed against tenant {WORK_TENANT}"
        )
        self.assertEqual(interactive_retry_tenant_id(personal=False, buffer=buffer), WORK_TENANT)

    def test_interactive_retry_skips_tenant_when_many_failed(self):
        other = "22222222-2222-2222-2222-222222222222"
        buffer = (
            f"AADSTS530035 Blocked by security defaults. "
            f"Authentication failed against tenant {WORK_TENANT}\n"
            f"Authentication failed against tenant {other}"
        )
        self.assertIsNone(interactive_retry_tenant_id(personal=False, buffer=buffer))
        self.assertEqual(parse_failed_against_tenants(buffer), [WORK_TENANT, other])

    def test_no_second_login_when_just_empty_subscriptions(self):
        self.assertFalse(needs_second_interactive_login("No subscriptions found for user"))
        self.assertEqual(humanize_login_failure("No subscriptions found"), NO_SUBSCRIPTION_MESSAGE)

    def test_security_defaults_after_retry_message(self):
        self.assertEqual(
            humanize_login_failure("AADSTS530035 blocked by security defaults"),
            SECURITY_DEFAULTS_STILL_BLOCKED,
        )

    def test_enabled_subscription_filter(self):
        rows = [
            {"id": "sub-on", "state": "Enabled"},
            {"id": "sub-warn", "state": "Warned"},
            {"id": "sub-off", "state": "Disabled"},
            {"id": "", "state": "Enabled"},
            {"name": "no-id", "state": "Enabled"},
            {
                "id": WORK_TENANT,
                "tenantId": WORK_TENANT,
                "name": "N/A(tenant level account)",
                "state": "Enabled",
            },
        ]
        self.assertEqual(
            enabled_subscription_accounts(rows),
            [{"id": "sub-on", "state": "Enabled"}, {"id": "sub-warn", "state": "Warned"}],
        )

    def test_tenant_level_placeholder_is_not_a_subscription(self):
        self.assertTrue(
            is_tenant_level_account(
                {
                    "id": WORK_TENANT,
                    "tenantId": WORK_TENANT,
                    "name": "N/A(tenant level account)",
                }
            )
        )
        self.assertFalse(
            is_tenant_level_account(
                {"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "tenantId": WORK_TENANT, "name": "Pay-As-You-Go"}
            )
        )

    def test_no_subscription_message_includes_email_and_states(self):
        self.assertIn("user@outlook.com", no_subscription_message([], "user@outlook.com"))
        text = no_subscription_message([{"id": "abc", "name": "Azure subscription 1", "state": "Disabled"}], "a@b.com")
        self.assertIn("Disabled", text)
        self.assertIn("a@b.com", text)

    def test_arm_subscription_json(self):
        sub = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        payload = (
            '{"value":[{"subscriptionId":"%s","displayName":"Azure subscription 1",'
            '"state":"Enabled","tenantId":"%s"}]}' % (sub, WORK_TENANT)
        )
        rows = accounts_from_arm_subscriptions(payload)
        self.assertEqual(rows[0]["id"], sub)
        self.assertEqual(rows[0]["name"], "Azure subscription 1")
        self.assertEqual(enabled_subscription_accounts(rows)[0]["id"], sub)

    def test_no_subs_output_lists_directory(self):
        blob = (
            "No subscriptions found for user@outlook.com.\n"
            f"{WORK_TENANT} 'Default Directory'\n"
        )
        self.assertEqual(parse_blocked_tenants(blob), [WORK_TENANT])

    def test_tenant_ids_from_arm_json(self):
        payload = '{"value":[{"tenantId":"%s"},{"id":"9188040d-6c67-4c5b-b112-36a304b66dad"}]}' % WORK_TENANT
        self.assertEqual(tenant_ids_from_json(payload), [WORK_TENANT])

    def test_subscription_not_found_is_treated_as_no_leftover(self):
        from app.services.submit_service import _missing_subscription_error

        self.assertTrue(_missing_subscription_error('HTTP 404: {"error": {"code": "SubscriptionNotFound"}}'))
        self.assertTrue(_missing_subscription_error("The subscription 'abc' could not be found."))
        self.assertFalse(_missing_subscription_error("Role assignment already exists"))

    def test_device_code_still_parses(self):
        msg = (
            "To sign in, use a web browser to open the page https://www.microsoft.com/devicelogin "
            "and enter the code ABCD1234 to authenticate."
        )
        uri, code = parse_device_prompt(msg)
        self.assertTrue(uri and "devicelogin" in uri)
        self.assertEqual(code, "ABCD1234")


class JoinNamePicker(unittest.TestCase):
    def test_keeps_snig_and_real_names(self):
        from app.services.owner_tag import join_picker_name

        self.assertEqual(join_picker_name("Snig"), "Snig")
        self.assertEqual(join_picker_name("snig"), "Snig")
        self.assertEqual(join_picker_name(" SNIG "), "Snig")
        self.assertIsNone(join_picker_name(""))
        self.assertEqual(join_picker_name("ritesh"), "Ritesh")
        kept = {join_picker_name(item) for item in ["Ritesh", "Snig", "Gaurav", "snig"]}
        self.assertEqual(kept - {None}, {"Ritesh", "Snig", "Gaurav"})


if __name__ == "__main__":
    unittest.main()
