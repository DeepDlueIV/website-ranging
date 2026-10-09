"""Pure parsing and scoring functions; no network or filesystem access."""
from __future__ import annotations

import ipaddress
import math
import re
from datetime import datetime, timezone
from urllib.parse import unquote_plus, urlsplit, urlunsplit

from bs4 import BeautifulSoup

WEIGHTS = {'crux_rank': 50, 'majestic_rank': 30, 'posts_per_day': 20}


def normalize_url(value: str) -> str:
    value = str(value).strip().strip('<>')
    if not value or any(ch.isspace() for ch in value):
        raise ValueError('Нужна ссылка без пробелов')
    if '://' not in value:
        value = 'https://' + value
    try:
        p = urlsplit(value)
        host = (p.hostname or '').rstrip('.').encode('idna').decode('ascii').lower()
        port = p.port
    except (ValueError, UnicodeError) as exc:
        raise ValueError('Некорректная ссылка') from exc
    if p.scheme not in ('http', 'https') or p.username is not None or p.password is not None:
        raise ValueError('Допустимы только HTTP/HTTPS без логина и пароля в ссылке')
    if port not in (None, 80, 443):
        raise ValueError('Нестандартный порт не поддерживается')
    if len(host) > 253 or '.' not in host or host.endswith(('.local', '.localhost', '.internal', '.onion')):
        raise ValueError('Нужен публичный домен, а не локальный или onion-адрес')
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError('Укажите домен, а не IP-адрес')
    if any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
           for label in host.split('.')):
        raise ValueError('Некорректное имя домена')
    query = '&'.join(part for part in p.query.split('&') if part and
                     not unquote_plus(part.split('=', 1)[0]).lower().startswith('utm_') and
                     unquote_plus(part.split('=', 1)[0]).lower() not in ('fbclid', 'gclid'))
    # Keep route-style queries such as index.php?forums/ byte-for-byte.
    netloc = host + (f':{port}' if port and port != (443 if p.scheme == 'https' else 80) else '')
    return urlunsplit((p.scheme, netloc, p.path or '/', query, ''))


def parse_count(text: str) -> int | None:
    text = re.sub(r'[\s\u00a0\u202f\u2009\'’]', '', str(text).lower())
    if len(text) > 40:
        return None
    m = re.fullmatch(r'(\d[\d.,]*)(k|m|b|тыс\.?|млн\.?|млрд\.?)?', text)
    if not m:
        return None
    number, suffix = m.groups()
    scale = {'k': 1000, 'тыс': 1000, 'm': 1000000, 'млн': 1000000,
             'b': 1000000000, 'млрд': 1000000000}
    if suffix:
        try:
            value = float(number.replace(',', '.')) * scale[suffix.rstrip('.')]
        except (ValueError, KeyError):
            return None
    elif re.fullmatch(r'\d{1,3}([.,]\d{3})+', number):
        value = int(number.replace(',', '').replace('.', ''))
    elif number.isdigit():
        value = int(number)
    else:
        return None
    return int(value) if math.isfinite(value) and 0 <= value <= 10**13 else None


LABELS = {
    'posts': r'(?:total\s+)?(?:messages|posts)|(?:всего\s+)?сообщени[йяе]',
    'topics': r'(?:total\s+)?(?:topics|threads|discussions)|(?:всего\s+)?тем(?:ы)?',
    'members': r'(?:total\s+)?(?:members|registered users|users)|(?:всего\s+)?(?:пользователей|участников)',
}
STAT_SELECTORS = ('.statistics, #board_statistics, #board_stats, #forum_stats, #forumstats, '
                  '#wgo_stats, [data-widget-key*="forum_statistics"], '
                  '[data-widget-definition="forum_statistics"], '
                  '[data-blockid*="forumStatistics"], [data-blockid*="_stats"]')
TIME_SELECTORS = ('.node-extra time, .structItem-latestDate time, .ipsDataItem_lastPoster time, '
                  '.lastpost time, .topic-list time, .ipsDataItem_lastPoster [data-time]')


def _datetime(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def parse_forum(html: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    soup = BeautifulSoup(html, 'html.parser')
    title = soup.title.get_text(' ', strip=True)[:300] if soup.title else ''
    lower = html.lower()
    engine = 'Не определён'
    for name, marker in [('XenForo', 'xenforo'), ('phpBB', 'phpbb'),
                         ('Invision', 'ipsapp'), ('vBulletin', 'vbulletin'), ('Discourse', 'discourse')]:
        if marker in lower:
            engine = name
            break
    if soup.select_one('html#XF, html[data-app="public"]'):
        engine = 'XenForo'
    data = dict(title=title, engine=engine, posts=None, topics=None, members=None,
                posts_approximate=False, last_activity=None, sampled_dates=0,
                challenge=False, login_wall=False)
    data['challenge'] = bool(re.search(r'just a moment|attention required|verify.*human|проверка браузера', title, re.I)
                             or soup.select_one('[id^="cf-chl-"], #challenge-form'))
    stats = soup.select(STAT_SELECTORS)
    for block in stats:
        for dt in block.select('dt'):
            dd = dt.find_next_sibling('dd')
            if dd is None:
                continue
            label = dt.get_text(' ', strip=True).strip(': ').lower()
            for key, pattern in LABELS.items():
                if re.fullmatch(pattern, label, re.I):
                    raw = dd.get_text(' ', strip=True)
                    count = parse_count(raw)
                    if count is not None and data[key] is None:
                        data[key] = count
                        if key == 'posts':
                            data['posts_approximate'] = bool(re.search(r'[kmbкм]|тыс', raw, re.I))
        text = block.get_text(' ', strip=True)
        for key, pattern in LABELS.items():
            if data[key] is not None:
                continue
            m = re.search(r'(?<!\w)(?:' + pattern + r')\s*:?\s*(\d[\d\s.,\u00a0\u202f]*\d|\d)'
                          r'\s*(k|m|b|тыс\.?|млн\.?|млрд\.?)?(?!\w)', text, re.I)
            if m:
                data[key] = parse_count(m.group(1) + (m.group(2) or ''))
                if key == 'posts':
                    data['posts_approximate'] = bool(m.group(2))
    dates = set()
    for node in soup.select(TIME_SELECTORS):
        dt = _datetime(node.get('datetime', ''))
        if dt is None and node.get('data-time'):
            try:
                dt = datetime.fromtimestamp(float(node['data-time']), tz=timezone.utc)
            except (ValueError, OverflowError, OSError):
                pass
        if dt is not None and dt.year >= 2000 and -300 <= (now - dt).total_seconds():
            dates.add(dt)
    if dates:
        data['last_activity'] = max(dates).isoformat()
        data['sampled_dates'] = len(dates)
    data['login_wall'] = bool(soup.select_one('input[type="password"]') and
                              re.search(r'log\s*in|sign\s*in|вход|авторизац', title, re.I) and
                              not soup.select_one('.node--forum, .topiclist, .topic-list') and
                              data['posts'] is None)
    return data


def add_growth(row: dict, previous: list[dict]) -> None:
    """Counter delta is a net increase, not unique users or gross new posts."""
    row['posts_per_day'] = None
    row['growth_days'] = None
    row['growth_since'] = None
    now = _datetime(row.get('checked_at', ''))
    if now is None or type(row.get('posts')) is not int or row.get('posts_approximate'):
        return
    candidates = []
    for old in previous:
        before = _datetime(old.get('checked_at', ''))
        if (before is None or type(old.get('posts')) is not int or old.get('posts', -1) < 0 or old.get('posts_approximate')
                or old.get('final_url') != row.get('final_url')
                or old.get('engine') != row.get('engine')):
            continue
        days = (now - before).total_seconds() / 86400
        if 1 <= days <= 90 and row['posts'] >= old['posts']:
            candidates.append((abs(days - 7), days, old))
    if candidates:
        _, days, old = min(candidates, key=lambda item: item[0])
        row['posts_per_day'] = round((row['posts'] - old['posts']) / days, 2)
        row['growth_days'] = round(days, 2)
        row['growth_since'] = old['checked_at']


def rank_results(rows: list[dict]) -> None:
    """Relative percentiles, available-weight mean, and SEPARATE evidence coverage."""
    groups = {}
    for key in WEIGHTS:
        pool = []
        for i, row in enumerate(rows):
            value = row.get(key)
            if key != 'posts_per_day' and row.get('rank_scope', 'exact_host') != 'exact_host':
                continue
            if isinstance(value, (int, float)) and math.isfinite(value) and value >= (0 if key == 'posts_per_day' else 1):
                pool.append((i, value))
        scores = {}
        for i, value in pool:
            if len(pool) == 1:
                scores[i] = 50.0
            else:
                worse = sum(other < value if key == 'posts_per_day' else other > value for _, other in pool)
                ties = sum(other == value for _, other in pool)
                scores[i] = 100 * (worse + (ties - 1) / 2) / (len(pool) - 1)
        groups[key] = scores
    for i, row in enumerate(rows):
        available = [(WEIGHTS[key], scores[i]) for key, scores in groups.items() if i in scores]
        coverage = sum(weight for weight, _ in available)
        row['coverage'] = coverage
        row['score'] = round(sum(weight * value for weight, value in available) / coverage, 1) if coverage else None
        row['group'] = ('Нужны данные' if coverage < 60 else
                        'A — проверить первым' if row['score'] >= 70 else
                        'B — второй приоритет' if row['score'] >= 35 else 'C — позже')
        row['components'] = '; '.join(f'{key}: {scores[i]:.1f}/100 × {WEIGHTS[key]}%'
                                     for key, scores in groups.items() if i in scores)
    rows.sort(key=lambda r: (r['coverage'] < 60, -(r['score'] if r['score'] is not None else -1),
                             -r['coverage'], r.get('url', '')))
