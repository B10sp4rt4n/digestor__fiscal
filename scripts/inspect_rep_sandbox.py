"""Inspect only local readiness. No network, secrets printed, activation or DB access."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.core.config import settings
from app.core.fiscal_sandbox import ppd_transport_is_sandbox
from app.services.rep_validation import schemas


def main():
    print(json.dumps({'environment':settings.timbracfdi_environment,
        'transport_is_sandbox':ppd_transport_is_sandbox(),
        'ppd_enabled':settings.FISCAL_PPD_ENABLED,'rep_enabled':settings.FISCAL_REP_STAMP_ENABLED,
        'sandbox_credentials_present':bool(settings.TIMBRACFDI_SANDBOX_TOKEN),'active_token_present':bool(settings.timbracfdi_active_token),
        'pinned_schema_bundle_sha256':schemas()[2], 'network_attempted':False},indent=2))


if __name__=='__main__':main()
