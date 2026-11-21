"""
live_prices.py - живые цены скинов: медиана трёх рынков (Buff163, CSFloat, Skinport) по всем скинам игры.

Источник: бесплатные ежедневные файлы prices.csgotrader.app/latest/<рынок>.json (проект расширения CSGO Trader,
обновляются раз в сутки, цены в USD). Doppler/Gamma Doppler по фазам: у Buff и CSFloat фазы в поле "doppler",
у Skinport - из его API (/v1/items, поле "version"), если он отвечает.

Список скинов и их оригинальные названия берутся из комментариев PRICES в scripts/data/PricesDB.gd и
scripts/DropData.gd (o_<id> = обычная версия сувенирного скина <id>). Сувенирные скины (кейсы с "souvenir": true)
ищутся как "Souvenir ...", ножи и перчатки как "★ ...".

Запуск (из корня проекта):
    PYTHONUTF8=1 python tools/live_prices.py --out <папка>          # скачать, посчитать, отчёт + prices.json
    PYTHONUTF8=1 python tools/live_prices.py --out <папка> --cache   # взять уже скачанные файлы из <папка>/src
    PYTHONUTF8=1 python tools/live_prices.py --export-list <репозиторий цен>/skins.json   # после новых скинов
В репозитории цен (GitHub Actions раз в сутки, docs/live-prices.md):
    python live_prices.py --list skins.json --out . --history
prices.json: {"date", "markets", "p": {id: [FN, MW, FT, WW, BS]}}; history.json: {"days", "w": {id: износ}, "p": {id: [цены]}}.

Монеты = USD*100. Износы вне диапазона Float = 0 (как в PricesDB), грязный износ не дороже чистого,
нет цены ни на одном рынке - остаётся вшитая цена (в отчёте "нет на рынках").
"""
import argparse
import gzip
import json
import os
import re
import statistics
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRICES_DB = os.path.join(ROOT, 'scripts', 'data', 'PricesDB.gd')
SKINS_DB = os.path.join(ROOT, 'scripts', 'data', 'SkinsDB.gd')
DROP_DB = os.path.join(ROOT, 'scripts', 'DropData.gd')

CSGOTRADER = 'https://prices.csgotrader.app/latest/%s.json'
SKINPORT = 'https://api.skinport.com/v1/items?app_id=730&currency=USD'
MARKETS = ['buff163', 'csfloat', 'skinport']
WEARS = ['Factory New', 'Minimal Wear', 'Field-Tested', 'Well-Worn', 'Battle-Scarred']
UA = {'User-Agent': 'igra-prices/1.0', 'Accept-Encoding': 'gzip'}

# фаза из нашего названия -> как её пишут рынки
PHASES = {'phase 1': 'Phase 1', 'phase 2': 'Phase 2', 'phase 3': 'Phase 3', 'phase 4': 'Phase 4',
          'ruby': 'Ruby', 'sapphire': 'Sapphire', 'black pearl': 'Black Pearl', 'emerald': 'Emerald'}
# наше название -> название на рынках (иероглифы в оригинале)
ALIASES = {'M4A4 | Dragon King': 'M4A4 | 龍王 (Dragon King)'}


def fetch(url: str) -> bytes:
    headers = dict(UA)
    if 'skinport.com' in url:  # Skinport без br отвечает 406 (нужен модуль brotli: pip install brotli)
        headers['Accept-Encoding'] = 'br, gzip'
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as r:
        b = r.read()
        enc = r.headers.get('Content-Encoding', '')
    if b[:2] == b'\x1f\x8b':
        b = gzip.decompress(b)
    elif enc == 'br':
        import brotli
        b = brotli.decompress(b)
    return b


def load_sources(src_dir: str, use_cache: bool) -> dict:
    os.makedirs(src_dir, exist_ok=True)
    data = {}
    for m in MARKETS + ['skinport_api']:
        path = os.path.join(src_dir, m + '.json')
        if not (use_cache and os.path.exists(path)):
            url = SKINPORT if m == 'skinport_api' else CSGOTRADER % m
            try:
                b = fetch(url)
                open(path, 'wb').write(b)
            except Exception as e:  # Skinport API может не пустить - тогда фазы Doppler только по двум рынкам
                print('  не скачалось %s: %s' % (m, e))
                continue
        data[m] = json.loads(open(path, 'rb').read())
    if 'skinport_api' in data:  # список -> {(имя, фаза): цена}
        sp = {}
        for x in data['skinport_api']:
            if x.get('min_price') is not None:
                sp[(x['market_hash_name'], x.get('version'))] = float(x['min_price'])
        data['skinport_api'] = sp
    return data


def parse_prices(path: str) -> dict:
    """{id: (цены [5], название из комментария)}"""
    text = open(path, encoding='utf-8').read()
    body = text[text.index('const PRICES := {'):]
    res = {}
    for m in re.finditer(r'^\t"([^"]+)": \[([^\]]*)\],(?:\s*#\s*(.*))?$', body, re.M):
        prices = [int(v) for v in m.group(2).split(',')]
        # пометки по-русски после названия: «(пропуски: ...)», «; WW не дороже чистого»
        name = re.split(r'\s*[(;](?=[^)]*[а-яА-ЯёЁ])', m.group(3) or '')[0].strip()
        name = ALIASES.get(name, name)
        res[m.group(1)] = (prices, name)
    return res


def parse_floats(path: str) -> dict:
    text = open(path, encoding='utf-8').read()
    body = text[text.index('const FLOATS := {'):text.index('const PRICES := {')]
    return {m.group(1): (float(m.group(2)), float(m.group(3)))
            for m in re.finditer(r'^\t"([^"]+)": \[([\d.]+), ([\d.]+)\]', body, re.M)}


def souvenir_ids() -> set:
    text = open(SKINS_DB, encoding='utf-8').read()
    ids = set()
    for m in re.finditer(r'"souvenir": true[^\n]*\n(?:[^\n]*\n){0,3}?\s*"skins": \[([^\]]*)\]', text):
        ids.update(re.findall(r'"([^"]+)"', m.group(1)))
    return ids


def split_phase(name: str):
    """'Karambit | Doppler (Phase 3)' -> ('Karambit | Doppler', 'Phase 3'); 'X | Gamma Doppler Emerald' -> (..., 'Emerald')"""
    m = re.match(r'(.+?) \(([^)]+)\)$', name)
    if m and m.group(2).lower() in PHASES:
        return m.group(1), PHASES[m.group(2).lower()]
    m = re.match(r'(.+? Doppler) (phase \d|ruby|sapphire|black pearl|emerald)$', name, re.I)
    if m:
        return m.group(1), PHASES[m.group(2).lower()]
    return name, None


def market_price(data: dict, market: str, hash_name: str, phase):
    """Цена предмета на рынке в USD или None"""
    if market == 'skinport' and phase:
        sp = data.get('skinport_api')
        return sp.get((hash_name, phase)) if sp else None
    row = data.get(market, {}).get(hash_name)
    if not row:
        return None
    if market == 'skinport':
        return row.get('starting_at')
    start = row.get('starting_at') or {}
    if phase:
        return (start.get('doppler') or {}).get(phase)
    if market == 'csfloat':
        return row.get('price') or start.get('price')
    return start.get('price')


def candidates(base: str, souvenir: bool) -> list:
    if souvenir:
        return [base if base.startswith('Souvenir ') else 'Souvenir ' + base]
    return [base, '★ ' + base]


def game_list() -> dict:
    """Список скинов из файлов игры: {id: {"n": название, "s": сувенир, "f": [мин, макс Float], "p": вшитые цены}}"""
    entries = parse_prices(PRICES_DB)
    floats = parse_floats(PRICES_DB)
    drop = parse_prices(DROP_DB)
    floats.update(parse_floats(DROP_DB))
    souv = souvenir_ids()
    for sid, (pr, _) in drop.items():  # o_mi1 -> название mi1 без "Souvenir"
        base_id = sid[2:]
        if base_id in entries:
            entries[sid] = (pr, entries[base_id][1].removeprefix('Souvenir '))
    res = {}
    for sid, (old, name) in entries.items():
        lo, hi = floats.get(sid, (0.0, 1.0))
        res[sid] = {'n': name, 's': sid in souv and not sid.startswith('o_'), 'f': [lo, hi], 'p': old}
    return res


def build(data: dict, lst: dict) -> tuple:
    out, report = {}, []
    bounds = [(0.0, 0.07), (0.07, 0.15), (0.15, 0.38), (0.38, 0.45), (0.45, 1.0)]
    for sid, e in lst.items():
        old, name = e['p'], e['n']
        if not name:
            report.append((sid, name, 'без названия', old, old, {}))
            out[sid] = old
            continue
        base, phase = split_phase(name)
        vanilla = base.endswith('(Vanilla)')
        if vanilla:
            base = base[:-len('(Vanilla)')].strip()
        lo, hi = e['f']
        new, per_market = [], {}
        for w, (a, b) in enumerate(bounds):
            if old[w] == 0 or hi <= a or lo >= b:
                new.append(0)
                continue
            got = None
            for cand in candidates(base, e['s']):
                hn = cand if vanilla else '%s (%s)' % (cand, WEARS[w])
                vals = {mk: market_price(data, mk, hn, phase) for mk in MARKETS}
                vals = {k: v for k, v in vals.items() if v}
                if vals:
                    got = vals
                    break
            if not got:
                new.append(None)
                continue
            per_market[WEARS[w]] = got
            new.append(max(1, round(statistics.median(got.values()) * 100)))
        # износа нет ни на одном рынке: вшитая цена, сдвинутая так же, как найденные износы этого скина;
        # скина нет совсем - вшитая цена целиком
        if all(v in (None, 0) for v in new) and any(v is None for v in new):
            out[sid] = old
            report.append((sid, name, 'нет на рынках', old, old, {}))
            continue
        found = [new[w] / old[w] for w in range(5) if new[w] and old[w]]
        k = statistics.median(found) if found else 1.0
        for w in range(5):
            if new[w] is None:
                new[w] = max(1, round(old[w] * k))
        for w in range(1, 5):  # грязный не дороже чистого
            if new[w] and new[w - 1] and new[w] > new[w - 1]:
                new[w] = new[w - 1]
        out[sid] = new
        report.append((sid, name, 'ok', old, new, per_market))
    return out, report


## износ, по которому ведётся история: первый существующий из FT, MW, FN, WW, BS (по вшитым ценам, не меняется)
REF_ORDER = [2, 1, 0, 3, 4]


def ref_wear(old: list) -> int:
    for w in REF_ORDER:
        if old[w]:
            return w
    return 0


def update_history(path: str, day: str, prices: dict, lst: dict, keep: int) -> dict:
    """history.json: {"days": [даты], "w": {id: износ}, "p": {id: [цена за каждый день, 0 = нет данных]}}"""
    h = {'days': [], 'w': {}, 'p': {}}
    if os.path.exists(path):
        h = json.load(open(path, encoding='utf-8'))
    if h['days'] and h['days'][-1] == day:  # повторный запуск в тот же день - заменить последний
        h['days'].pop()
        for sid in h['p']:
            h['p'][sid] = h['p'][sid][:len(h['days'])]
    n = len(h['days'])
    h['days'].append(day)
    for sid, pr in prices.items():
        w = h['w'].setdefault(sid, ref_wear(lst[sid]['p']))
        row = h['p'].setdefault(sid, [])
        row.extend([0] * (n - len(row)))  # новый скин: 0 в прошлые дни
        row.append(int(pr[w]))
    for row in h['p'].values():  # скин пропал из списка - тоже 0
        row.extend([0] * (n + 1 - len(row)))
    if len(h['days']) > keep:
        cut = len(h['days']) - keep
        h['days'] = h['days'][cut:]
        for sid in h['p']:
            h['p'][sid] = h['p'][sid][cut:]
    return h


def main() -> int:
    import datetime
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', help='куда положить prices.json (и history.json с --history)')
    ap.add_argument('--cache', action='store_true', help='не качать, взять <out>/src')
    ap.add_argument('--list', help='skins.json вместо файлов игры (для репозитория цен)')
    ap.add_argument('--export-list', help='записать skins.json из файлов игры и выйти')
    ap.add_argument('--history', action='store_true', help='дописать день в <out>/history.json')
    ap.add_argument('--keep', type=int, default=90, help='сколько дней хранить в истории')
    a = ap.parse_args()
    if a.export_list:
        lst = game_list()
        json.dump(lst, open(a.export_list, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
        print('скинов в списке:', len(lst), '->', a.export_list)
        return 0
    if not a.out:
        ap.error('нужен --out')
    lst = json.load(open(a.list, encoding='utf-8')) if a.list else game_list()
    data = load_sources(os.path.join(a.out, 'src'), a.cache)
    print('рынки:', ', '.join('%s %d' % (k, len(v)) for k, v in data.items()))
    if any(m not in data for m in MARKETS):
        print('ОШИБКА: не все рынки скачались, файлы не меняю')
        return 1
    out, report = build(data, lst)
    day = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')
    doc = {'date': day, 'markets': MARKETS, 'p': out}
    json.dump(doc, open(os.path.join(a.out, 'prices.json'), 'w', encoding='utf-8'), separators=(',', ':'))
    if a.history:
        hp = os.path.join(a.out, 'history.json')
        h = update_history(hp, day, out, lst, a.keep)
        json.dump(h, open(hp, 'w', encoding='utf-8'), separators=(',', ':'))
        print('история: %d дней' % len(h['days']))
    status = {}
    for r in report:
        status[r[2]] = status.get(r[2], 0) + 1
    print('скинов:', len(report), status)
    n_mk = {1: 0, 2: 0, 3: 0}
    ratios = []
    lines = []
    for sid, name, st, old, new, pm in report:
        for vals in pm.values():
            n_mk[len(vals)] += 1
        if st != 'ok':
            lines.append('%-8s %-45s %s' % (sid, name, st))
            continue
        o, n = max(old), max(new)
        if o and n:
            ratios.append(n / o)
            if n / o > 2 or n / o < 0.5:
                lines.append('%-8s %-45s было %s стало %s' % (sid, name, old, new))
    print('износов с ценой на 3/2/1 рынках:', n_mk[3], n_mk[2], n_mk[1])
    if ratios:
        ratios.sort()
        print('новая/вшитая цена (по самому дорогому износу): медиана %.2f, 10%% %.2f, 90%% %.2f' % (
            statistics.median(ratios), ratios[len(ratios) // 10], ratios[len(ratios) * 9 // 10]))
    open(os.path.join(a.out, 'report.txt'), 'w', encoding='utf-8').write('\n'.join(lines))
    print('отчёт: %d строк (изменение больше 2 раз и не найденные) -> report.txt' % len(lines))
    return 0


if __name__ == '__main__':
    sys.exit(main())
