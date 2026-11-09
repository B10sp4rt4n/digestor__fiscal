"""Local SAT snapshot, deterministic retrieval and validity checks. No LLM/PAC calls."""
from datetime import date, datetime
from functools import lru_cache
from contextlib import contextmanager
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import unicodedata
from zoneinfo import ZoneInfo

DATA = Path(__file__).resolve().parents[1] / 'data'
STOP = {'de','del','la','el','las','los','y','o','para','por','en','con','sin','un','una','mes','meses','ano','anos','anual','mensual','durante'}
RELATIONS = {
    'edr': ('software','seguridad','antivirus'), 'threatdown': ('software','seguridad','proteccion'),
    'antivirus': ('software','seguridad','proteccion'), 'endpoint': ('software','seguridad'),
    'licencia': ('licenc','software'), 'licencias': ('licenc','software'),
    'suscripcion': ('licenc','software'), 'ciberseguridad': ('seguridad','software'),
    'computadora': ('comput','equipo'), 'laptop': ('comput','portatil'),
    'pieza': ('pieza',), 'servicio': ('servicio',), 'soporte': ('soporte','mantenimiento'),
}
NATURE = {'license': ('licenc','software'), 'managed_service': ('servicio','seguridad'),
          'implementation': ('implement','consultoria'), 'support': ('soporte','mantenimiento'),
          'goods': ('equipo',), 'other': ()}


class CatalogUnavailable(RuntimeError):
    pass


def normalize(value):
    return re.sub(r'[^a-z0-9]+', ' ', ''.join(c for c in unicodedata.normalize('NFD', str(value).lower()) if unicodedata.category(c) != 'Mn')).strip()


def today():
    return datetime.now(ZoneInfo('America/Mexico_City')).date()


@lru_cache(maxsize=1)
def snapshot():
    try:
        manifest = json.loads((DATA / 'sat-search-manifest.json').read_text())
        compressed = (DATA / 'sat-search.sqlite.gz').read_bytes()
        if hashlib.sha256(compressed).hexdigest() != manifest['bundleSha256']:
            raise ValueError('Snapshot integrity')
        raw = gzip.decompress(compressed)
        if len(raw) > 80 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != manifest['databaseSha256']:
            raise ValueError('Database integrity')
        target = Path(tempfile.gettempdir()) / ('digestor-sat-' + manifest['databaseSha256'] + '.sqlite')
        if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != manifest['databaseSha256']:
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as file:
                file.write(raw); temporary = file.name
            os.replace(temporary, target)
        return manifest, target
    except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
        raise CatalogUnavailable('El catálogo SAT no está disponible; intenta nuevamente más tarde.') from exc


def metadata():
    manifest, _ = snapshot()
    return {key: manifest[key] for key in ('cfdiVersion','source','sourceUrl','sourceFile','publishedAt','checkedAt','sourceSha256','counts','search')}


@contextmanager
def connect():
    _, path = snapshot()
    connection = None
    try:
        connection = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        yield connection
    except sqlite3.Error as exc:
        raise CatalogUnavailable('No se pudo consultar el catálogo SAT.') from exc
    finally:
        if connection is not None:
            connection.close()


def entry(row, on=None):
    if row is None: return None
    on = (on or today()).isoformat()
    valid = (not row['valid_from'] or row['valid_from'] <= on) and (not row['valid_to'] or on <= row['valid_to'])
    return {'code': row['code'], 'description': row['description'], 'details': row['details'], 'notes': row['notes'],
            'validFrom': row['valid_from'], 'validTo': row['valid_to'], 'valid': valid}


def lookup(kind, code, on=None):
    with connect() as connection:
        return entry(connection.execute('SELECT * FROM entries WHERE kind=? AND code=?', (kind, code)).fetchone(), on)


def search(kind, query, nature='other', limit=8, on=None):
    if kind not in ('product','unit') or nature not in NATURE:
        raise ValueError('Tipo de búsqueda no válido')
    query = str(query).strip()[:200]
    exact = lookup(kind, query, on) or lookup(kind, query.upper(), on)
    if exact:
        return {'catalog': metadata(), 'results': [{**exact, 'rankScore': 100, 'matchedTerms': [query], 'domainRelations': [], 'method': 'exact_code'}]}
    original = list(dict.fromkeys(word for word in normalize(query).split() if len(word) >= 3 and not word.isdigit() and word not in STOP))[:16]
    if not original: return {'catalog': metadata(), 'results': []}
    relations = [(word, RELATIONS[word]) for word in original if word in RELATIONS] if kind == 'product' else []
    if kind == 'unit' and any(word in original for word in ('licencia','licencias','suscripcion','servicio')):
        relations = [('licencia/servicio', ('unidad','servicio'))]
    inferred = list(dict.fromkeys(term for _, terms in relations for term in terms))
    if kind == 'product': inferred += list(NATURE[nature])
    terms = list(dict.fromkeys(original + inferred))[:32]
    expression = ' OR '.join('"' + term + '"*' for term in terms)
    day = (on or today()).isoformat()
    try:
        with connect() as connection:
            candidates = connection.execute('''SELECT e.* FROM search JOIN entries e ON e.id=search.rowid
              WHERE search MATCH ? AND e.kind=? AND (e.valid_from IS NULL OR e.valid_from<=?)
              AND (e.valid_to IS NULL OR e.valid_to>=?) ORDER BY bm25(search),e.code LIMIT 120''', (expression,kind,day,day)).fetchall()
    except sqlite3.Error as exc:
        raise CatalogUnavailable('No se pudo consultar el catálogo SAT.') from exc
    ranked = []
    for row in candidates:
        tokens = normalize(row['description'] + ' ' + row['details']).split()
        matches = [term for term in terms if any(token.startswith(term) for token in tokens)]
        direct = sum(term in matches for term in original)
        domain = sum(term in matches for term in inferred)
        score = direct * 12 + domain * 4
        description_tokens = normalize(row['description']).split()
        score += sum(16 for term in original if any(token.startswith(term) for token in description_tokens))
        if normalize(query) == normalize(row['description']): score += 100
        if kind == 'product':
            score += sum(16 for source, values in relations if source in ('edr','threatdown','antivirus','ciberseguridad') for term in values if term in matches)
        reasons = [f'{source} → {", ".join(values)}' for source, values in relations if any(term in matches for term in values)]
        ranked.append({**entry(row,on), 'rankScore': score, 'matchedTerms': matches[:10], 'domainRelations': reasons[:3], 'method': 'text_and_domain_relations'})
    ranked.sort(key=lambda result: (-result['rankScore'], result['code']))
    return {'catalog': metadata(), 'results': ranked[:limit]}


def validate_pairs(items, expected_version=None, on=None):
    catalog = metadata()
    if expected_version and expected_version != catalog['sourceSha256']:
        return {'valid': False, 'catalog': catalog, 'items': [], 'error': 'El catálogo cambió. Revisa y confirma otra vez la clasificación.'}
    output = []
    for item in items:
        product = lookup('product', item['satProductCode'], on)
        unit = lookup('unit', item['unitCode'], on)
        valid = bool(product and product['valid'] and unit and unit['valid'])
        output.append({'itemNumber': item['itemNumber'], 'valid': valid, 'product': product, 'unit': unit,
                       'error': None if valid else 'La clave de producto o unidad no existe o no está vigente en la versión consultada.'})
    return {'valid': bool(output) and all(item['valid'] for item in output), 'catalog': catalog, 'items': output}
