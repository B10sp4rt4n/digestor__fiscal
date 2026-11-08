"""Execute actual read-handler/auth helper AST with inert SQL doubles, no secrets."""
import ast
import base64
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from xml.etree import ElementTree
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]


def functions(path, names, ns):
    tree = ast.parse((ROOT / path).read_text())
    selected = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            node.decorator_list = []
            node.returns = None
            for arg in node.args.args + node.args.kwonlyargs:
                arg.annotation = None
            # Invoke explicitly; don't execute framework dependency defaults here.
            node.args.defaults = []
            selected.append(node)
    if len(selected) != len(names):
        raise AssertionError("Source functions missing")
    exec(compile(ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])), path, "exec"), ns)


class ReadsTests(unittest.TestCase):
    def auth(self):
        ns = {"HTTPException": HTTPException, "VALID_ROLES": {"viewer", "operator", "admin", "superadmin"},
              "settings": SimpleNamespace(AUTH_CONNECTOR="jwt")}
        functions("app/core/auth.py", {"is_auth_disabled", "enforce_tenant_scope", "_normalize_role"}, ns)
        return ns

    def handler(self, draft):
        ns = self.auth()
        functions("app/services/timbracfdi_client.py", {"extract_stamp_uuid"}, ns)
        ns.update({"ElementTree": ElementTree, "_re": re, "base64": base64,
                   "timbracfdi_client": SimpleNamespace(extract_stamp_uuid=ns["extract_stamp_uuid"])})
        # No PAC methods exist on this double: attempting one fails the test.
        accesses = []
        class Column:
            def __init__(self, name): self.name = name
            def __eq__(self, value): return self.name, value
        model = SimpleNamespace(id=Column("id"), company_id=Column("company_id"), items="items")
        class Query:
            def options(self, option): return self
            def filter(self, *filters): accesses.append(filters); return self
            def first(self): return draft
        class DB:
            def query(self, selected):
                if selected is not model: raise AssertionError("Unexpected model")
                return Query()
            def __getattr__(self, name): raise AssertionError("Unexpected database operation: " + name)
        ns.update({"BillingDraft": model, "joinedload": lambda x: x})
        functions("app/api/routers/commercial_v1.py", {"_draft_query", "_get_draft_or_404", "get_billing_draft_stamp_result"}, ns)
        return ns["get_billing_draft_stamp_result"], DB(), accesses

    def test_stamped_result_reads_only_and_filters_tenant_and_id(self):
        uuid = "12345678-1234-1234-1234-123456789abc"
        xml = ('<c:Comprobante xmlns:c="http://www.sat.gob.mx/cfd/4" xmlns:t="http://www.sat.gob.mx/TimbreFiscalDigital">'
               '<c:Complemento><t:TimbreFiscalDigital UUID="' + uuid + '"/></c:Complemento></c:Comprobante>')
        encoded = base64.b64encode(xml.encode()).decode()
        handler, db, accesses = self.handler(SimpleNamespace(stamp_status="stamped", stamped_xml_base64=encoded))
        result = handler("fixture", None, SimpleNamespace(role="operator", tenant_id="tenant-a"), db)
        self.assertEqual(result, {"stamp_status": "stamped", "uuid": uuid, "xml_base64": encoded})
        self.assertEqual(accesses, [(("id", "fixture"), ("company_id", "tenant-a"))])

    def test_wrong_tenant_denied_before_database_read(self):
        handler, db, accesses = self.handler(None)
        with self.assertRaises(HTTPException) as raised:
            handler("fixture", "tenant-b", SimpleNamespace(role="operator", tenant_id="tenant-a"), db)
        self.assertEqual(raised.exception.status_code, 403)
        self.assertFalse(accesses)

    def test_missing_record_remains_404_without_write_or_pac(self):
        handler, db, _ = self.handler(None)
        with self.assertRaises(HTTPException) as raised:
            handler("fixture", None, SimpleNamespace(role="operator", tenant_id="tenant-a"), db)
        self.assertEqual(raised.exception.status_code, 404)

    def test_unknown_evidence_not_reissued_or_revealed(self):
        handler, db, _ = self.handler(SimpleNamespace(stamp_status="unknown", stamped_xml_base64="not-returned"))
        result = handler("fixture", None, SimpleNamespace(role="operator", tenant_id="tenant-a"), db)
        self.assertEqual(result, {"stamp_status": "unknown", "uuid": None, "xml_base64": None})

    def test_health_and_version_need_no_database_object(self):
        ns = {"settings": SimpleNamespace(SERVICE_VERSION="synthetic")}
        functions("app/api/routers/health.py", {"health", "version"}, ns)
        self.assertEqual(ns["health"](), {"status": "ok"})
        self.assertEqual(ns["version"](), {"version": "synthetic"})


if __name__ == "__main__":
    unittest.main()
