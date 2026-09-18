import os
import unittest
from unittest.mock import patch

from product.settings import Settings


BASE = {
    "PRODUCT_DATABASE_URL": "postgresql://user:pass@db:5432/copilot",
    "PRODUCT_DATA_DIR": ".local/settings-test",
    "PRODUCT_MODE": "live",
    "PRODUCT_APP_ENV": "production",
    "PRODUCT_COOKIE_SECURE": "true",
    "PRODUCT_ALLOW_SETUP": "false",
    "PRODUCT_ALLOWED_HOSTS": "research.example.com",
    "PRODUCT_APP_VERSION": "0.1.0",
}


class SettingsTest(unittest.TestCase):
    def load(self, **updates):
        values = BASE | updates
        with patch.dict(os.environ, values, clear=False):
            return Settings.load()

    def test_production_contract(self):
        settings = self.load()
        self.assertEqual(settings.app_env, "production")
        self.assertEqual(settings.app_version, "0.1.0")
        self.assertTrue(settings.cookie_secure)
        self.assertFalse(settings.allow_setup)

    def test_production_rejects_insecure_cookie_setup_and_wildcard_host(self):
        for updates, message in (
            ({"PRODUCT_COOKIE_SECURE": "false"}, "secure cookies"),
            ({"PRODUCT_ALLOW_SETUP": "true"}, "setup endpoint"),
            ({"PRODUCT_ALLOWED_HOSTS": "*"}, "host allowlist"),
            ({"PRODUCT_MODE": "mock"}, "PRODUCT_MODE=live"),
        ):
            with self.subTest(updates=updates), self.assertRaisesRegex(ValueError, message):
                self.load(**updates)


if __name__ == "__main__":
    unittest.main()
