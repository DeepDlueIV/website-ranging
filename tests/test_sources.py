import csv
import gzip
import io
import json
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

import requests
from ranging.sources import Client, SourceError, Cancelled, parse_dataset, collect_page, load_dataset


class FakeResponse:
    def __init__(self, body=b'', status=200, headers=None, url='https://forum.example/'):
        self.body = body
        self.status_code = status
        self.headers = headers or {}
        self.url = url
        self.encoding = 'utf-8'
        self.closed = False

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self.body), chunk_size):
            yield self.body[i:i+chunk_size]

    def close(self):
        self.closed = True


class SourceTests(unittest.TestCase):
    def test_crux_exact_origin_not_parent(self):
        data = io.StringIO('origin,rank\nhttps://forum.example.com,10000\nhttps://example.com,1000\n')
        found, count = parse_dataset(data, 'crux', {'https://forum.example.com'})
        self.assertEqual(found['https://forum.example.com']['rank'], 10000)
        self.assertEqual(count, 2)
        self.assertNotIn('https://example.com', found)

    def test_majestic_keeps_subnets_distinct_from_backlinks(self):
        data = io.StringIO('GlobalRank,Domain,RefSubNets,RefIPs\n42,example.com,20,80\n')
        found, _ = parse_dataset(data, 'majestic', {'example.com'})
        self.assertEqual(found['example.com'], {'rank': 42, 'ref_subnets': 20, 'ref_ips': 80})

    def test_bad_source_is_error_not_empty_list(self):
        with self.assertRaises(SourceError):
            parse_dataset(io.StringIO('<html>Blocked</html>'), 'majestic', {'example.com'})
        with self.assertRaises(SourceError):
            parse_dataset(io.StringIO('origin,rank\n'), 'crux', {'https://example.com'})

    def test_redirect_to_private_is_blocked(self):
        client = Client(delay=0)
        with patch('ranging.sources.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443))]), \
             patch.object(client.session, 'get', return_value=FakeResponse(status=302, headers={'Location':'http://127.0.0.1'})):
            with self.assertRaises((SourceError, ValueError)):
                client.get('https://forum.example/')

    def test_private_dns_blocked(self):
        client = Client(delay=0)
        with patch('ranging.sources.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('10.0.0.1', 443))]):
            with self.assertRaises(SourceError):
                client.get('https://forum.example/')

    def test_robots_denied(self):
        client = Client(delay=0)
        with patch.object(client, 'get', return_value=FakeResponse(b'User-agent: *\nDisallow: /\n')):
            row = collect_page('https://forum.example/', client)
        self.assertEqual(row['status'], 'robots.txt запрещает сбор')
        self.assertNotIn('posts', row)

    def test_login_html_is_not_public_activity(self):
        client = Client(delay=0)
        responses = [FakeResponse(status=404),
                     FakeResponse(b'<title>Sign in</title><input type="password">', headers={'Content-Type':'text/html'})]
        with patch.object(client, 'get', side_effect=responses):
            row = collect_page('https://forum.example/', client)
        self.assertEqual(row['status'], 'Нужна авторизация')

    def test_robots_failure_not_mistaken_for_site_down(self):
        client = Client(delay=0)
        with patch.object(client, 'get', return_value=FakeResponse(status=503)):
            row = collect_page('https://forum.example/', client)
        self.assertEqual(row['status'], 'robots.txt недоступен — сбор пропущен')

    def test_redirect_checks_destination_robots(self):
        client = Client(delay=0)
        responses = [FakeResponse(status=404),
                     FakeResponse(status=302, headers={'Location':'https://other.example/'}),
                     FakeResponse(b'User-agent: *\nDisallow: /\n')]
        with patch.object(client, 'get', side_effect=responses):
            row = collect_page('https://forum.example/', client)
        self.assertEqual(row['status'], 'robots.txt запрещает сбор')

    def test_cancellation(self):
        event = Event(); event.set()
        with self.assertRaises(Cancelled):
            Client(cancel=event).get('https://forum.example/')

    def test_size_limit(self):
        client = Client(delay=0)
        with self.assertRaises(SourceError):
            client.read(FakeResponse(b'x' * 101), 100)

    def test_cache_reused_without_network(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root/'majestic.csv').write_text('GlobalRank,Domain,RefSubNets,RefIPs\n42,example.com,20,80\n')
            (root/'majestic.json').write_text(json.dumps({'downloaded_at':'2026-10-09T12:00:00+00:00','source_url':'test'}))
            with patch('ranging.sources.utcnow', return_value='2026-10-09T13:00:00+00:00'):
                found, meta = load_dataset('majestic', {'example.com'}, root, Client(delay=0))
            self.assertEqual(found['example.com']['rank'], 42)
            self.assertTrue(meta['cached'])

if __name__ == '__main__':
    unittest.main()
