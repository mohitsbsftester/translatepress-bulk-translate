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

    def test_allows_only_grammatical_english_apostrophe_entities_to_disappear(self):
        for source in (
            "SureCookie doesn&#8217;t guess cookies.",
            "You&#39;ve saved your site&apos;s settings.",
            "We&rsquo;re ready when it&#8217;s connected.",
            "Customers&#8217; consent choices remain available.",
        ):
            with self.subTest(source=source):
                result = validate_translation(
                    source,
                    "Die Einstellungen und Einwilligungsoptionen bleiben verfügbar.",
                    [],
                )
                self.assertTrue(result.valid, result.failures)
                self.assertTrue(
                    any("apostrophe entity" in warning for warning in result.warnings)
                )

    def test_keeps_quoted_names_and_non_apostrophe_entities_strict(self):
        self.assertValid(
            "Read &#8217;Privacy Policy&#8217; now.",
            "Lesen Sie jetzt &#8217;Datenschutzerklärung&#8217;.",
        )
        self.assertInvalid(
            "Read &#8217;Privacy Policy&#8217; now.",
            "Lesen Sie jetzt die Datenschutzerklärung.",
        )
        self.assertInvalid(
            "Open &#39;Users&#39; settings.",
            "Öffnen Sie die Benutzereinstellungen.",
        )
        self.assertInvalid("Read O&#8217;Reilly", "Lesen Sie OReilly")
        self.assertInvalid("You&#8217;ve connected.", "Sie&#8217;ve verbunden.")
        self.assertInvalid(
            "Accept&nbsp;&amp;&nbsp;continue", "Akzeptieren und fortfahren"
        )

    def test_preserves_printf_templates_and_shortcodes(self):
        source = '[code id="7"]Hello %1$s, {site}, {{name}}[/code]'
        target = '[code id="7"]Hallo %1$s, {site}, {{name}}[/code]'
        self.assertValid(source, target)
        self.assertInvalid(source, "Hallo")
        self.assertEqual(eligibility_reason("%title", []), "protected_placeholder")

    def test_ordinary_percentages_are_not_printf_placeholders(self):
        for source, target in (
            ("solves 80% of problems", "löst 80 % der Probleme"),
            ("100% complete", "zu 100 % abgeschlossen"),
            ("20% discount", "20 % Rabatt"),
        ):
            with self.subTest(source=source):
                self.assertValid(source, target)

    def test_printf_placeholders_still_require_exact_preservation(self):
        for placeholder in ("%s", "%1$s", "%2$d", "% d", "%.2f", "%08x"):
            with self.subTest(placeholder=placeholder):
                self.assertValid(f"Value: {placeholder}", f"Wert: {placeholder}")
                changed = "%d" if placeholder != "%d" else "%s"
                self.assertInvalid(f"Value: {placeholder}", f"Wert: {changed}")

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
