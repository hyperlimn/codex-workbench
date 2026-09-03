import unittest

from codex_workbench.codex import parse_status


class CodexStatusTests(unittest.TestCase):
    def test_plain_launcher_status_is_structured(self):
        status = parse_status(
            "fallback",
            "dir: ~/code/demo | account: account-a • Plus | "
            "model: gpt-5 | 5h: 4% left | week: 83% left | "
            "reset: Tomorrow 2:00 PM",
        )

        self.assertEqual(status.account, "account-a")
        self.assertEqual(status.five_hour_remaining, 4)
        self.assertEqual(status.weekly_remaining, 83)
        self.assertEqual(status.availability, "warning")

    def test_json_used_percent_is_converted_to_remaining(self):
        status = parse_status(
            "account-a",
            '{"model":"gpt-5","primary":{"usedPercent":96},'
            '"secondary":{"used_percent":17}}',
        )

        self.assertEqual(status.five_hour_remaining, 4)
        self.assertEqual(status.weekly_remaining, 83)


if __name__ == "__main__":
    unittest.main()
