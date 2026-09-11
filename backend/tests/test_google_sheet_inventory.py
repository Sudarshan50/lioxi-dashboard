import unittest

from app.services.google_sheet_inventory import (
    _a1_col,
    _ensure_headers,
    _is_rate_limit,
    _plan_writes,
)


class SheetHelpers(unittest.TestCase):
    def test_a1_columns(self):
        self.assertEqual(_a1_col(0), "A")
        self.assertEqual(_a1_col(25), "Z")
        self.assertEqual(_a1_col(26), "AA")

    def test_rate_limit_text(self):
        self.assertTrue(_is_rate_limit(RuntimeError("429 Quota exceeded for quota metric")))
        self.assertFalse(_is_rate_limit(RuntimeError("403 forbidden")))

    def test_plan_updates_and_appends(self):
        mapping = _ensure_headers(["Sno", "Name", "Email", "Endpoint", "TPM", "Proxy_Name", "Pool"])
        existing = [
            ["1", "Alex", "a@x.com", "https://one.openai.azure.com/", "10k", "p1", "10k"],
        ]
        writes, appends = _plan_writes(
            mapping,
            existing,
            [
                {
                    "Person": "Alex",
                    "Email": "a@x.com",
                    "Endpoint": "https://one.openai.azure.com/",
                    "TPM": "50k",
                    "Proxy_Name": "p1",
                    "Pool": "10k",
                },
                {
                    "Person": "Sam",
                    "Email": "s@x.com",
                    "Endpoint": "https://two.openai.azure.com/",
                    "TPM": "500k",
                    "Proxy_Name": "p2",
                    "Pool": "10k",
                },
            ],
        )
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][0], 2)
        self.assertEqual(writes[0][1][mapping["TPM"]], "50k")
        self.assertEqual(len(appends), 1)
        self.assertEqual(appends[0][mapping["Sno"]], "2")
        self.assertEqual(appends[0][mapping["Person"]], "Sam")

    def test_plan_skips_unchanged(self):
        mapping = _ensure_headers(["Sno", "Name", "Email", "Endpoint", "TPM", "Proxy_Name", "Pool"])
        existing = [
            ["1", "Alex", "a@x.com", "https://one.openai.azure.com/", "10k", "p1", "10k"],
        ]
        writes, appends = _plan_writes(
            mapping,
            existing,
            [
                {
                    "Person": "Alex",
                    "Email": "a@x.com",
                    "Endpoint": "https://one.openai.azure.com/",
                    "TPM": "10k",
                    "Proxy_Name": "p1",
                    "Pool": "10k",
                }
            ],
        )
        self.assertEqual(writes, [])
        self.assertEqual(appends, [])


if __name__ == "__main__":
    unittest.main()
