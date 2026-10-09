"""Explicitly synthetic offline demo, never mixed into real reports or history."""
from .core import parse_forum, rank_results
from .sources import utcnow


def demo_result() -> dict:
    rows = []
    examples = [('busy.example', 'Активный форум — пример', 10000, 12000, 250000),
                ('small.example', 'Небольшой форум — пример', 1000000, 800000, 15000),
                ('closed.example', 'Закрытый форум — пример', None, None, None),
                ('portal.example/forum/', 'Раздел портала — пример', 1000, 500, 9000)]
    for address, name, crux, majestic, posts in examples:
        html = (f'<html id="XF"><title>{name}</title><div data-widget-key="forum_statistics">'
                f'<dl><dt>Messages</dt><dd>{posts}</dd></dl></div></html>') if posts else '<title>Sign in</title><input type="password">'
        row = parse_forum(html)
        row.update(url='https://' + address.rstrip('/') + '/', label=name, valid=True,
                   checked_at=utcnow(), crux_rank=crux, majestic_rank=majestic,
                   status='Демонстрация: данные вымышлены', notes='Только пример, не реальный форум',
                   rank_scope='whole_site' if '/forum' in address else 'exact_host')
        row['final_url'] = row['url']
        rows.append(row)
    rank_results(rows)
    return {'rows': rows, 'sources': {}, 'warnings': ['Все данные вымышлены; сеть не используется.'],
            'checked_at': utcnow(), 'cancelled': False, 'demo': True}
