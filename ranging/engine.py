"""One sequential run; no database and no external upload of the forum list."""
from __future__ import annotations

import json
from pathlib import Path
from threading import Event
from urllib.parse import urlsplit

from .core import add_growth, rank_results
from .sources import Client, SourceError, Cancelled, collect_page, load_dataset, utcnow, atomic_json


def origin(url: str) -> str:
    p = urlsplit(url)
    return f'{p.scheme}://{p.netloc}'


def domain(url: str) -> str:
    host = urlsplit(url).hostname or ''
    return host[4:] if host.startswith('www.') else host


def add_note(row: dict, message: str):
    row['notes'] = '; '.join(filter(None, [row.get('notes', ''), message]))


def load_history(path: Path, warnings: list[str]) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('version') != 1 or not isinstance(data.get('forums'), dict):
            raise ValueError('Unknown history schema')
        if not all(isinstance(v, list) and all(isinstance(item, dict) for item in v)
                   for v in data['forums'].values()):
            raise ValueError('Invalid history')
        return data['forums']
    except (OSError, ValueError, TypeError, AttributeError):
        warnings.append('История не читается: прирост не рассчитан; проверьте history.json.')
        return {}


def analyze(inputs: list[dict], cache_dir: Path, history: dict | None = None,
            inspect_pages: bool = True, cancel: Event | None = None, log=None) -> dict:
    rows = [dict(row) for row in inputs]
    history = history or {}
    result = {'rows': rows, 'sources': {}, 'warnings': [], 'checked_at': utcnow(), 'cancelled': False}
    client = Client(cancel=cancel, log=log)
    try:
        for i, row in enumerate(rows, start=1):
            row['checked_at'] = utcnow()
            row['status'] = 'Не проверен'
            row['rank_scope'] = 'exact_host'
            if not row['valid']:
                row['status'] = 'Некорректная ссылка'
                continue
            row['final_url'] = row['url']
            if client.cancel.is_set():
                result['cancelled'] = True
                continue
            client.log(f'Форум {i}/{len(rows)}: {row["url"]}')
            if inspect_pages:
                try:
                    row.update(collect_page(row['url'], client))
                except Cancelled:
                    result['cancelled'] = True
                    row['status'] = 'Проверка прервана'
                except (SourceError, ValueError) as exc:
                    row['status'] = 'Страница не получена'
                    add_note(row, str(exc))
            else:
                row['status'] = 'Страница не запрашивалась'
            p = urlsplit(row['url'])
            if p.path not in ('/', '/index.php', '/index.html') or p.query:
                row['rank_scope'] = 'whole_site'
                add_note(row, 'Ссылка на раздел: внешние рейтинги всего сайта не включены в балл форума')
            if domain(row['url']) != domain(row['final_url']):
                row['rank_scope'] = 'redirected_host'
                add_note(row, 'Редирект на другой домен: проверьте, что это тот же форум; его рейтинг не засчитан')
            add_growth(row, history.get(row['url'], []))
            if row.get('posts_approximate'):
                add_note(row, 'Счётчик сообщений округлён сайтом; прирост не рассчитывается')
        for kind in ('crux', 'majestic'):
            wanted = {(origin if kind == 'crux' else domain)(r.get('final_url', r['url']))
                      for r in rows if r['valid']}
            found = {}
            meta = {'status': 'error', 'warning': 'Проверка остановлена'}
            if not client.cancel.is_set() and wanted:
                try:
                    found, meta = load_dataset(kind, wanted, cache_dir, client)
                    meta['status'] = 'ok'
                except Cancelled:
                    result['cancelled'] = True
                except (SourceError, OSError) as exc:
                    meta = {'status': 'error', 'warning': str(exc)}
                    client.log(f'{kind}: {exc}. Остальная проверка продолжается.')
            result['sources'][kind] = meta
            for row in rows:
                if not row['valid']:
                    continue
                key = (origin if kind == 'crux' else domain)(row.get('final_url', row['url']))
                row[kind + '_lookup'] = key
                item = found.get(key)
                row[kind + '_rank'] = item['rank'] if item else None
                row[kind + '_status'] = ('Найден' if item else 'В выгрузке Top 1M не найден'
                                         if meta['status'] == 'ok' else 'Источник недоступен')
                if kind == 'majestic' and item:
                    row['ref_subnets'] = item.get('ref_subnets')
                    row['ref_ips'] = item.get('ref_ips')
                if meta.get('stale'):
                    add_note(row, f'{kind}: старый кэш, обновление не удалось')
        rank_results(rows)
        for row in rows:
            if row['coverage'] < 60:
                add_note(row, 'Недостаточно показателей: запросите статистику у администрации. Это не низкая посещаемость')
            if row.get('score') is not None:
                add_note(row, 'Баллы относительные: порядок проверки, не рекомендация купить рекламу')
        result['cancelled'] = result['cancelled'] or client.cancel.is_set()
        return result
    finally:
        client.close()


def save_history(path: Path, history: dict, result: dict) -> None:
    for row in result['rows']:
        if not row.get('valid') or row.get('posts') is None:
            continue
        observations = history.setdefault(row['url'], [])
        observation = {key: row.get(key) for key in
                       ('checked_at', 'posts', 'posts_approximate', 'engine', 'final_url')}
        # Keep one observation per UTC date; repeated button clicks do not fill history.
        observations[:] = [r for r in observations if str(r.get('checked_at', ''))[:10] != row['checked_at'][:10]]
        observations.append(observation)
        observations[:] = observations[-90:]
    atomic_json(path, {'version': 1, 'forums': history})
