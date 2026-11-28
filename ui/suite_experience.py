"""Shared CDC presentation contract. No authentication or data access."""
from html import escape
import json
from pathlib import Path

EXPERIENCE = json.loads((Path(__file__).resolve().parents[1] / "config" / "suite_experience.json").read_text(encoding="utf-8"))


def suite_header_html(module, company_name=None, rfc=None, scope_reference=None):
    definition = EXPERIENCE["modules"][module]
    company = escape(str(company_name or "Empresa asignada"))
    reference = rfc or scope_reference
    reference_html = f'<div class="cdc-company-reference">{escape(str(reference))}</div>' if reference else ""
    return (
        '<section class="cdc-header" aria-label="CDC Suite y empresa activa">'
        '<div class="cdc-brand"><span class="cdc-mark" aria-hidden="true">C</span><div>'
        f'<div class="cdc-brand-title">{EXPERIENCE["brand"]}</div>'
        f'<div class="cdc-subtitle">{definition["section"]} · {definition["name"]}</div>'
        '</div></div><div class="cdc-company"><div class="cdc-label">Empresa activa</div>'
        f'<div class="cdc-company-name">{company}</div>{reference_html}</div></section>'
    )


def suite_css():
    variables = ";".join(f"--cdc-{key}:{value}" for key, value in EXPERIENCE["colors"].items())
    return "<style>:root{" + variables + """}
    .cdc-header{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:1rem;padding:1.25rem;background:var(--cdc-surface);border:1px solid var(--cdc-line);border-radius:16px;margin-bottom:.5rem;font-family:Arial,Helvetica,sans-serif}
    .cdc-brand{display:flex;align-items:center;gap:.875rem;min-width:0}
    .cdc-mark{display:grid;place-items:center;width:48px;height:48px;flex-shrink:0;border-radius:16px;background:var(--cdc-ink);color:white;font-size:1.5rem;font-weight:700;border-bottom:3px solid var(--cdc-accent)}
    .cdc-brand-title{font-size:1.2rem;font-weight:650;color:var(--cdc-ink);letter-spacing:-.02em}
    .cdc-subtitle,.cdc-company-reference{color:var(--cdc-muted);font-size:.8rem}
    .cdc-company{min-width:0;max-width:100%;overflow-wrap:anywhere}
    .cdc-label{font-size:.65rem;letter-spacing:.12em;text-transform:uppercase;color:var(--cdc-muted)}
    .cdc-company-name{font-size:.9rem;font-weight:600;color:var(--cdc-ink)}
    @media(max-width:640px){.cdc-header{padding:1rem}.cdc-company{width:100%;border-top:1px solid var(--cdc-line);padding-top:.75rem}}
    </style>"""

