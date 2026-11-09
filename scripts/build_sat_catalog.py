"""Build a reproducible, read-only search snapshot from the official SAT XLS.

Install requirements.catalog.txt. This command does not deploy or contact a PAC.
Keep the source XLS externally with the emitted SHA-256 for reproducibility.
"""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unicodedata
import xml.etree.ElementTree as ET

import xlrd


def normalize(value):
    return re.sub(r"[^a-z0-9]+", " ", "".join(c for c in unicodedata.normalize("NFD", str(value).lower()) if unicodedata.category(c) != "Mn")).strip()


def build(source, output, source_url, published, checked, xsd):
    raw = source.read_bytes()
    if len(raw) > 65 * 1024 * 1024:
        raise ValueError("Source too large")
    book = xlrd.open_workbook(file_contents=raw, on_demand=True)
    schema_bytes = xsd.read_bytes()
    if len(schema_bytes) > 12 * 1024 * 1024: raise ValueError('Schema too large')
    schema = ET.fromstring(schema_bytes)
    namespace = {'x': 'http://www.w3.org/2001/XMLSchema'}
    valid_codes = {kind: {node.attrib['value'] for node in schema.findall(f".//x:simpleType[@name='{name}']//x:enumeration", namespace)}
                   for kind, name in [('product','c_ClaveProdServ'),('unit','c_ClaveUnidad')]}
    aliases = {}
    rows = []
    for sheet_name, kind, start_column, end_column, details_columns in [
        ("c_ClaveProdServ", "product", 5, 6, [8]), ("c_ClaveUnidad", "unit", 4, 5, [2, 6]),
    ]:
        sheet = book.sheet_by_name(sheet_name)
        header = next(i for i in range(12) if sheet.cell_value(i, 0) == sheet_name)
        if sheet.nrows > 100_000:
            raise ValueError("Unexpected sheet size")
        def date_value(value):
            if value in (None, "", 0): return None
            if isinstance(value, (int, float)):
                return xlrd.xldate_as_datetime(value, book.datemode).date().isoformat()
            return datetime.fromisoformat(str(value)).date().isoformat()
        for index in range(header + 1, sheet.nrows):
            cells = sheet.row_values(index)
            if not str(cells[0]).strip(): continue
            code = str(int(cells[0])) if isinstance(cells[0], (int, float)) else str(cells[0]).strip()
            if kind == "product": code = code.zfill(8)
            if not re.fullmatch(r"\d{8}" if kind == "product" else r"[A-Za-z0-9]{2,3}", code):
                raise ValueError("Invalid catalog code")
            description = str(cells[1]).strip()
            details = " ".join(str(cells[c]).strip() for c in details_columns if str(cells[c]).strip())
            notes = f"IVA trasladado: {cells[2]}; IEPS trasladado: {cells[3]}. {str(cells[4]).strip()}" if kind == "product" else str(cells[3]).strip()
            if code not in valid_codes[kind]:
                if kind == 'unit' and code.upper() in valid_codes[kind]:
                    aliases[code] = code.upper()
                    notes += f' Clave original en XLS: {code}; representación XML del XSD SAT: {code.upper()}.'
                    code = code.upper()
                else: raise ValueError(f'Code absent from official XML schema: {kind}/{code}')
            rows.append((kind, code, description, details, notes, date_value(cells[start_column]), date_value(cells[end_column])))
    book.release_resources()
    with tempfile.TemporaryDirectory() as temp:
        database = Path(temp) / "catalog.sqlite"
        connection = sqlite3.connect(database)
        connection.executescript('''
          PRAGMA page_size=4096;
          CREATE TABLE entries (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, code TEXT NOT NULL, description TEXT NOT NULL,
            details TEXT NOT NULL, notes TEXT NOT NULL, valid_from TEXT, valid_to TEXT, UNIQUE(kind,code));
          CREATE VIRTUAL TABLE search USING fts5(terms, tokenize='unicode61 remove_diacritics 2');
        ''')
        connection.executemany("INSERT INTO entries(kind,code,description,details,notes,valid_from,valid_to) VALUES(?,?,?,?,?,?,?)", rows)
        for ident, description, details in connection.execute("SELECT id,description,details FROM entries"):
            connection.execute("INSERT INTO search(rowid,terms) VALUES(?,?)", (ident, normalize(description + " " + details)))
        connection.commit(); connection.execute("VACUUM"); connection.close()
        data = database.read_bytes()
    compressed = gzip.compress(data, compresslevel=9, mtime=0)
    output.mkdir(parents=True, exist_ok=True)
    (output / "sat-search.sqlite.gz").write_bytes(compressed)
    manifest = {
        "schemaVersion": 1, "cfdiVersion": "4.0", "source": "SAT", "sourceUrl": source_url,
        "sourceFile": source.name, "publishedAt": published, "checkedAt": checked,
        "sourceSha256": hashlib.sha256(raw).hexdigest(), "databaseSha256": hashlib.sha256(data).hexdigest(),
        "bundleSha256": hashlib.sha256(compressed).hexdigest(),
        "counts": {kind: sum(row[0] == kind for row in rows) for kind in ("product", "unit")},
        "search": "deterministic_text_and_domain_relations_v1", "automaticFiscalClassification": False,
        "xsdSourceUrl": "https://www.sat.gob.mx/sitio_internet/cfd/catalogos/catCFDI.xsd",
        "xsdSha256": hashlib.sha256(schema_bytes).hexdigest(), "unitCodeRepresentations": aliases,
    }
    (output / "sat-search-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({**manifest, "databaseBytes": len(data), "bundleBytes": len(compressed)}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=Path("app/data"))
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--published", required=True)
    parser.add_argument("--checked", required=True)
    parser.add_argument("--xsd", type=Path, required=True)
    args = parser.parse_args()
    build(args.source, args.output, args.source_url, args.published, args.checked, args.xsd)
