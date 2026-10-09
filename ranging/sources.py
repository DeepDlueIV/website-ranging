"""Free datasets and a small, polite HTTP client. No authentication bypasses."""
from __future__ import annotations

import csv
import gzip
import ipaddress
import json
import re
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests

from .core import normalize_url, parse_forum

USER_AGENT = 'WebsiteRanging/0.1 (+https://github.com/DeepDlueIV/website-ranging)'
SOURCES = {
    'crux': {
        'url': 'https://raw.githubusercontent.com/zakird/crux-top-lists/main/data/global/current.csv.gz',
        'name': 'Google CrUX (снимок zakird/crux-top-lists)',
        'about': 'https://developer.chrome.com/docs/crux/methodology',
        'license': 'Google CrUX — CC BY 4.0; CSV snapshot by Zakir Durumeric',
        'filename': 'crux.csv.gz',
    },
    'majestic': {
        'url': 'https://downloads.majestic.com/majestic_million.csv',
        'name': 'Majestic Million',
        'about': 'https://majestic.com/reports/majestic-million',
        'license': 'Majestic Million CSV by Majestic 12 — CC BY 3.0',
        'filename': 'majestic.csv',
    },
}


class SourceError(Exception):
    pass


class Cancelled(Exception):
    pass


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def atomic_json(path: Path, data) -> None:
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


class Client:
    def __init__(self, cancel: Event | None = None, log=None, delay: float = 1.0):
        self.cancel = cancel or Event()
        self.log = log or (lambda message: None)
        self.delay = delay
        self.session = requests.Session()
        self.session.headers['User-Agent'] = USER_AGENT
        # A local research tool should never pick credentials up from ~/.netrc.
        self.session.trust_env = False
        self.last_request = {}
        self.host_delays = {}
        self.robots = {}

    def check_cancel(self):
        if self.cancel.is_set():
            raise Cancelled('Проверка остановлена')

    def wait(self, seconds: float):
        if self.cancel.wait(max(0, seconds)):
            raise Cancelled('Проверка остановлена')

    def get(self, url: str, redirects: bool = True):
        for _ in range(6):
            self.check_cancel()
            url = normalize_url(url)
            p = urlsplit(url)
            try:
                addresses = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == 'https' else 80), type=socket.SOCK_STREAM)
            except OSError as exc:
                raise SourceError('Не удалось определить IP домена') from exc
            if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
                raise SourceError('Запрос к частному или служебному IP запрещён')
            delay = max(self.delay, self.host_delays.get(p.hostname, 0))
            self.wait(delay - (time.monotonic() - self.last_request.get(p.hostname, 0)))
            self.last_request[p.hostname] = time.monotonic()
            try:
                response = self.session.get(url, stream=True, timeout=(8, 20), allow_redirects=False)
            except requests.RequestException as exc:
                raise SourceError(f'Сетевая ошибка: {type(exc).__name__}') from exc
            if redirects and response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get('Location')
                response.close()
                if not location:
                    raise SourceError('Редирект без адреса назначения')
                url = urljoin(url, location)
                continue
            return response
        raise SourceError('Слишком много перенаправлений')

    def chunks(self, response, max_bytes: int, deadline: float = 180):
        total = 0
        started = time.monotonic()
        try:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                self.check_cancel()
                total += len(chunk)
                if total > max_bytes or time.monotonic() - started > deadline:
                    raise SourceError('Превышен лимит размера или времени ответа')
                yield chunk
        except requests.RequestException as exc:
            raise SourceError(f'Ошибка загрузки: {type(exc).__name__}') from exc
        finally:
            response.close()

    def read(self, response, max_bytes: int = 2_000_000) -> bytes:
        return b''.join(self.chunks(response, max_bytes, deadline=40))

    def close(self):
        self.session.close()


def parse_dataset(stream, kind: str, wanted: set[str]) -> tuple[dict, int]:
    reader = csv.DictReader(stream)
    required = {'origin', 'rank'} if kind == 'crux' else {'GlobalRank', 'Domain', 'RefSubNets', 'RefIPs'}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise SourceError(f'{kind}: неизвестный формат выгрузки')
    found = {}
    count = 0
    try:
        for row in reader:
            count += 1
            if count > 3_000_000:
                raise SourceError('Слишком много строк в выгрузке')
            key = row['origin'].rstrip('/').lower() if kind == 'crux' else row['Domain'].lower()
            rank = int(row['rank'] if kind == 'crux' else row['GlobalRank'])
            if rank < 1:
                raise ValueError('Rank must be positive')
            if key in wanted:
                item = {'rank': rank}
                if kind == 'majestic':
                    item.update(ref_subnets=int(row['RefSubNets']), ref_ips=int(row['RefIPs']))
                found[key] = item
    except (ValueError, TypeError, KeyError, csv.Error) as exc:
        raise SourceError(f'{kind}: повреждённая выгрузка') from exc
    if count == 0:
        raise SourceError(f'{kind}: пустая выгрузка')
    return found, count


def _parse_file(path: Path, kind: str, wanted: set[str]):
    opener = gzip.open if kind == 'crux' else open
    try:
        with opener(path, 'rt', encoding='utf-8-sig', newline='') as stream:
            return parse_dataset(stream, kind, wanted)
    except (OSError, EOFError, UnicodeError) as exc:
        raise SourceError(f'{kind}: файл не читается') from exc


def load_dataset(kind: str, wanted: set[str], cache: Path, client: Client) -> tuple[dict, dict]:
    source = SOURCES[kind]
    cache.mkdir(parents=True, exist_ok=True)
    file = cache / source['filename']
    metadata_file = cache / (kind + '.json')
    old_meta = {}
    age = float('inf')
    try:
        old_meta = json.loads(metadata_file.read_text(encoding='utf-8'))
        age = (datetime.fromisoformat(utcnow()) - datetime.fromisoformat(old_meta['downloaded_at'])).total_seconds()
        if file.exists() and 0 <= age < 86400:
            found, total = _parse_file(file, kind, wanted)
            return found, {**source, **old_meta, 'cached': True, 'rows': total, 'stale': False}
    except (OSError, ValueError, KeyError, TypeError, SourceError):
        pass
    client.log(f'Загрузка {source["name"]} (кэш обновляется не чаще раза в сутки)…')
    temp = file.with_name(file.name + '.part')
    try:
        url = source['url']
        snapshot = ''
        # Pin the CrUX mirror to the commit that last changed the data, when available.
        if kind == 'crux':
            try:
                r = client.get('https://api.github.com/repos/zakird/crux-top-lists/commits?path=data/global/current.csv.gz&per_page=1')
                status = r.status_code
                raw = client.read(r, 1_000_000)
                if status == 200:
                    entry = json.loads(raw)[0]
                    sha = entry['sha']
                    if re.fullmatch('[a-f0-9]{40}', sha):
                        url = f'https://raw.githubusercontent.com/zakird/crux-top-lists/{sha}/data/global/current.csv.gz'
                        snapshot = entry['commit']['committer']['date']
            except (SourceError, ValueError, KeyError, TypeError, IndexError):
                pass
        r = client.get(url)
        if r.status_code != 200:
            status = r.status_code
            r.close()
            raise SourceError(f'{kind}: источник вернул HTTP {status}')
        meta = {**source, 'source_url': url, 'downloaded_at': utcnow(),
                'last_modified': r.headers.get('Last-Modified', ''),
                'snapshot_updated': snapshot, 'cached': False, 'stale': False}
        written = 0
        last_notice = 0
        with temp.open('wb') as out:
            for chunk in client.chunks(r, 160_000_000):
                out.write(chunk)
                written += len(chunk)
                if written - last_notice >= 10_000_000:
                    client.log(f'{source["name"]}: загружено {written // 1_000_000} МБ')
                    last_notice = written
        found, total = _parse_file(temp, kind, wanted)
        temp.replace(file)
        meta['rows'] = total
        atomic_json(metadata_file, meta)
        return found, meta
    except (SourceError, OSError, ValueError) as exc:
        if file.exists() and 0 <= age < 7 * 86400:
            found, total = _parse_file(file, kind, wanted)
            client.log(f'{kind}: обновление не удалось; используется старый кэш с явной отметкой.')
            return found, {**source, **old_meta, 'cached': True, 'stale': True, 'rows': total,
                           'warning': str(exc)}
        raise SourceError(str(exc)) from exc
    finally:
        temp.unlink(missing_ok=True)


def _robots(url: str, client: Client) -> tuple[bool, str]:
    p = urlsplit(url)
    origin = f'{p.scheme}://{p.netloc}'
    if origin not in client.robots:
        r = client.get(origin + '/robots.txt')
        status = r.status_code
        if status in (401, 403):
            r.close()
            client.robots[origin] = (None, 'robots.txt запрещает сбор')
        elif status == 404:
            r.close()
            client.robots[origin] = (None, '')
        elif status != 200:
            r.close()
            client.robots[origin] = (None, 'robots.txt недоступен — сбор пропущен')
        else:
            body = client.read(r, 500_000).decode('utf-8', errors='replace')
            if re.search(r'<html|<!doctype|cf-chl-', body, re.I):
                client.robots[origin] = (None, 'robots.txt недоступен — сбор пропущен')
            else:
                parser = RobotFileParser()
                parser.parse(body.splitlines())
                delay = parser.crawl_delay('WebsiteRanging') or 0
                rate = parser.request_rate('WebsiteRanging')
                if rate and rate.requests:
                    delay = max(delay, rate.seconds / rate.requests)
                if delay > 30:
                    client.robots[origin] = (None, 'robots.txt требует большую паузу — сбор пропущен')
                else:
                    client.host_delays[p.hostname] = max(delay, client.host_delays.get(p.hostname, 0))
                    client.robots[origin] = (parser, '')
    parser, error = client.robots[origin]
    if error:
        return False, error
    if parser and not parser.can_fetch('WebsiteRanging', url):
        return False, 'robots.txt запрещает сбор'
    return True, ''


def collect_page(url: str, client: Client) -> dict:
    current = url
    for _ in range(6):
        current = normalize_url(current)
        allowed, reason = _robots(current, client)
        if not allowed:
            return {'final_url': current, 'status': reason}
        r = client.get(current, redirects=False)
        status = r.status_code
        if status in (301, 302, 303, 307, 308):
            location = r.headers.get('Location')
            r.close()
            if not location:
                raise SourceError('Редирект без адреса')
            current = urljoin(current, location)
            continue
        result = {'final_url': current, 'http_status': status}
        if status != 200:
            r.close()
            result['status'] = ('Доступ ограничен (401/403)' if status in (401, 403)
                                else 'Лимит запросов (429)' if status == 429 else f'HTTP {status}')
            return result
        content_type = r.headers.get('Content-Type', '')
        if content_type and 'html' not in content_type.lower():
            r.close()
            return {**result, 'status': 'Не HTML-страница'}
        body = client.read(r)
        charset = re.search(r'charset\s*=\s*["\']?([\w-]+)', content_type, re.I)
        if not charset:
            charset = re.search(r'charset\s*=\s*["\']?([\w-]+)', body[:2048].decode('ascii', errors='ignore'), re.I)
        encoding = charset.group(1) if charset else 'utf-8'
        try:
            html = body.decode(encoding, errors='replace')
        except LookupError:
            html = body.decode('utf-8', errors='replace')
        result.update(parse_forum(html))
        result['status'] = ('CAPTCHA / защита от ботов' if result['challenge'] else
                            'Нужна авторизация' if result['login_wall'] else 'Публичная страница прочитана')
        if result['challenge'] or result['login_wall']:
            for key in ('posts', 'topics', 'members', 'last_activity'):
                result[key] = None
        return result
    raise SourceError('Слишком много перенаправлений')
