import math
import unittest
from datetime import datetime, timezone
from ranging.core import normalize_url, parse_count, parse_forum, rank_results, add_growth

NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


class CoreTests(unittest.TestCase):
    def test_url_keeps_forum_path_removes_tracking(self):
        self.assertEqual(normalize_url(' example.com/forum/?utm_source=x#top'),
                         'https://example.com/forum/')

    def test_cyrillic_domain(self):
        self.assertIn('xn--', normalize_url('пример.рф'))

    def test_rejects_unsafe_inputs(self):
        for value in ['file:///etc/passwd', 'https://user:pass@example.com',
                      'http://127.0.0.1', 'http://localhost', 'ftp://example.com',
                      'http://[::1]', 'https://example.com:8080', 'no domain']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_url(value)

    def test_keeps_meaningful_query(self):
        self.assertEqual(normalize_url('https://example.com/index.php?forums/&utm_source=x'),
                         'https://example.com/index.php?forums/')

    def test_counts(self):
        for text, expected in [('1,234', 1234), ('1 234', 1234), ('1.234', 1234),
                               ('1.2K', 1200), ('2,5 млн', 2500000), ('0', 0),
                               ('1\u202f234', 1234), ('8 тыс.', 8000)]:
            with self.subTest(text=text):
                self.assertEqual(parse_count(text), expected)
        self.assertIsNone(parse_count('Yesterday'))
        self.assertIsNone(parse_count('-5'))

    def test_xenforo_counters_and_activity(self):
        html = '''<html id="XF"><title>Forum</title><div class="block" data-widget-key="forum_statistics">
        <dl class="pairs"><dt>Messages</dt><dd>12,345</dd></dl>
        <dl class="pairs"><dt>Members</dt><dd>300</dd></dl></div>
        <div class="node-extra"><time datetime="2026-10-08T13:00:00Z"></time></div></html>'''
        data = parse_forum(html, NOW)
        self.assertEqual(data['posts'], 12345)
        self.assertEqual(data['members'], 300)
        self.assertEqual(data['engine'], 'XenForo')
        self.assertTrue(data['last_activity'].startswith('2026-10-08'))

    def test_does_not_sum_category_counters_or_use_footer_date(self):
        data = parse_forum('''<html id="XF"><div class="node-stats"><dl class="pairs">
        <dt>Messages</dt><dd>99</dd></dl></div>
        <footer><time datetime="2026-10-09"></time></footer></html>''', NOW)
        self.assertIsNone(data['posts'])
        self.assertIsNone(data['last_activity'])

    def test_phpbb_russian(self):
        data = parse_forum('''<meta name="copyright" content="phpBB"><div class="statistics">
        <p>Всего сообщений <strong>45 000</strong> • Всего тем <strong>90</strong>
        • Всего пользователей <strong>2 001</strong></p></div>''', NOW)
        self.assertEqual((data['posts'], data['topics'], data['members']), (45000, 90, 2001))

    def test_detects_challenge_without_false_login_from_nav(self):
        self.assertTrue(parse_forum('<title>Just a moment...</title><div id="cf-chl-widget"></div>', NOW)['challenge'])
        self.assertFalse(parse_forum('<a href="login">Log in</a><h1>Forum</h1>', NOW)['login_wall'])
        self.assertTrue(parse_forum('<title>Sign in</title><form><input type="password"></form>', NOW)['login_wall'])

    def test_missing_stays_unknown(self):
        rows = [{'url': 'https://closed.example', 'crux_rank': None, 'majestic_rank': None}]
        rank_results(rows)
        self.assertIsNone(rows[0]['score'])
        self.assertEqual(rows[0]['coverage'], 0)
        self.assertEqual(rows[0]['group'], 'Нужны данные')

    def test_one_metric_does_not_get_priority_a(self):
        rows = [{'url': 'https://a.example', 'crux_rank': 10},
                {'url': 'https://b.example', 'crux_rank': 100}]
        rank_results(rows)
        self.assertEqual(rows[0]['coverage'], 50)
        self.assertEqual(rows[0]['group'], 'Нужны данные')

    def test_rank_and_ties(self):
        rows = [dict(url=f'https://{name}.example', crux_rank=rank, majestic_rank=rank)
                for name, rank in [('a', 10), ('b', 10), ('c', 1000)]]
        rank_results(rows)
        self.assertEqual(rows[0]['score'], rows[1]['score'])
        self.assertGreater(rows[0]['score'], rows[2]['score'])
        self.assertEqual(rows[0]['coverage'], 80)

    def test_parent_or_subpath_ranks_are_context_only(self):
        row = dict(url='https://site.example/forum/', crux_rank=10,
                   majestic_rank=20, rank_scope='whole_site')
        rank_results([row])
        self.assertIsNone(row['score'])

    def test_counter_growth_needs_day_and_same_page(self):
        old = dict(checked_at='2026-10-07T12:00:00+00:00', posts=100,
                   final_url='https://a.example/', engine='XenForo')
        row = dict(checked_at=NOW.isoformat(), posts=160,
                   final_url='https://a.example/', engine='XenForo')
        add_growth(row, [old])
        self.assertEqual(row['posts_per_day'], 30)
        row['posts'] = 90
        add_growth(row, [old])
        self.assertIsNone(row['posts_per_day'])

    def test_malformed_history_does_not_crash_growth(self):
        row = dict(checked_at=NOW.isoformat(), posts=160,
                   final_url='https://a.example/', engine='XenForo')
        old = dict(checked_at='2026-10-07T12:00:00+00:00', posts='bad',
                   final_url='https://a.example/', engine='XenForo')
        add_growth(row, [old])
        self.assertIsNone(row['posts_per_day'])

    def test_huge_counter_is_ignored(self):
        self.assertIsNone(parse_count('9' * 5000))

    def test_singleton_scores_neutral(self):
        row = dict(url='https://a.example/', crux_rank=1, majestic_rank=1)
        rank_results([row])
        self.assertEqual(row['score'], 50)

if __name__ == '__main__':
    unittest.main()
