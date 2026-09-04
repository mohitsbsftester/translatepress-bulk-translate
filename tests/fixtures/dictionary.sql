SET NAMES utf8mb4;

CREATE TABLE `acme_trp_dictionary_en_us_de_de` (
  `id` bigint unsigned NOT NULL,
  `original` longtext NOT NULL,
  `translated` longtext,
  `status` int NOT NULL DEFAULT 0,
  `block_type` int NOT NULL DEFAULT 0,
  `original_id` bigint unsigned DEFAULT NULL
);

INSERT INTO `acme_trp_dictionary_en_us_de_de` (`id`, `original`, `translated`, `status`, `block_type`, `original_id`) VALUES
(1, 'Get Started', '', 0, 0, 101),
(2, 'Privacy Policy', 'Datenschutzerklärung', 2, 0, 102),
(3, 'Cookie consent', 'Cookie-Einwilligung', 1, 0, 103),
(4, 'Accept <strong class="choice">Marketing Cookies</strong> to continue.', '', 0, 0, 104),
(5, 'Hello %1$s, scan {site} for {{cookie_count}} cookies.', '', 0, 0, 105),
(6, '[surecookie id="42"]Open preferences[/surecookie]', '', 0, 0, 106),
(7, 'https://example.com/privacy/', '', 0, 0, 107),
(8, 'support@example.com', '', 0, 0, 108),
(9, 'cookie-policy', '', 0, 0, 109),
(10, 'SureCookie works with WordPress and Google Consent Mode.', '', 0, 0, 110),
(11, 'It''s safe to use “quotes”, apostrophes, and newlines.\nSecond line.', NULL, 0, 0, 111),
(12, '{"cookie_name":"_ga","duration":30}', '', 0, 0, 112),
(13, 'GDPR consent records are stored in WordPress.', '', 0, 0, 113),
(14, 'Internal block metadata', '', 0, 2, 114),
(15, 'Already similar', 'Ähnlich übersetzt', 3, 0, 115),
(16, 'German characters ä ö ü Ä Ö Ü ß remain valid UTF-8.', '', 0, 0, 116),
(17, 'Styled .cookie-banner and #consent-modal elements.', '', 0, 0, 117),
(18, 'Copyright &copy; SureCookie. All rights reserved.', '', 0, 0, 118);

CREATE TABLE `acme_trp_gettext_de_de` (`id` bigint unsigned NOT NULL);
