from __future__ import annotations

import unittest

from trp_tool.validation import eligibility_reason, validate_translation


class ValidationTests(unittest.TestCase):
    def assertValid(self, source: str, target: str):
        result = validate_translation(source, target, ["SureCookie", "WordPress"])
        self.assertTrue(result.valid, result.failures)

    def assertInvalid(self, source: str, target: str):
        result = validate_translation(source, target, ["SureCookie", "WordPress"])
        self.assertFalse(result.valid)

    def test_accepts_german_unicode_quotes_apostrophes_and_newlines(self):
        self.assertValid(
            "It's useful.\nSecond line.",
            "Es ist nützlich für Äpfel, Öl und Grüße.\nZweite Zeile mit ß.",
        )

    def test_preserves_html_and_entities(self):
        self.assertValid(
            'Accept <strong class="choice">Cookies</strong>&nbsp;',
            'Akzeptieren Sie <strong class="choice">Cookies</strong>&nbsp;',
        )
        self.assertInvalid("Accept <strong>Cookies</strong>", "Cookies akzeptieren")
        self.assertInvalid("Copyright &copy;", "Copyright ©")

    def test_preserves_printf_templates_and_shortcodes(self):
        source = '[code id="7"]Hello %1$s, {site}, {{name}}[/code]'
        target = '[code id="7"]Hallo %1$s, {site}, {{name}}[/code]'
        self.assertValid(source, target)
        self.assertInvalid(source, "Hallo")
        self.assertEqual(eligibility_reason("%title", []), "protected_placeholder")

    def test_preserves_urls_emails_phone_paths_code_and_brands(self):
        source = "SureCookie on WordPress: https://example.com/a?b=1 support@example.com +49 30 123456 `/wp/a.php`"
        target = "SureCookie auf WordPress: https://example.com/a?b=1 support@example.com +49 30 123456 `/wp/a.php`"
        self.assertValid(source, target)
        self.assertInvalid(source, target.replace("SureCookie", "SicherCookie"))

    def test_preserves_css_selectors_and_json_keys(self):
        self.assertValid(
            'Use .cookie-banner, #modal and "cookie_name": now.',
            'Verwenden Sie .cookie-banner, #modal und "cookie_name": jetzt.',
        )
        self.assertInvalid(
            'Use .cookie-banner and "cookie_name": now.',
            'Verwenden Sie .banner und "name": jetzt.',
        )

    def test_rejects_whitespace_and_malformed_html_changes(self):
        self.assertInvalid("  Hello\nworld  ", "Hallo Welt")
        self.assertInvalid("<strong>Hello</strong>", "<strong>Hallo</em>")

    def test_generic_eligibility_protects_non_prose(self):
        self.assertEqual(eligibility_reason("https://example.com", []), "protected_url")
        self.assertEqual(
            eligibility_reason("support@example.com", []), "protected_email"
        )
        self.assertEqual(eligibility_reason("cookie-policy", []), "slug_deferred")
        self.assertEqual(eligibility_reason('{"name":"_ga"}', []), "protected_code")
        self.assertIsNone(eligibility_reason("Cookie consent for everyone", []))


if __name__ == "__main__":
    unittest.main()
