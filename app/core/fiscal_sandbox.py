"""An environment label must not route PPD tests to a production endpoint."""
from urllib.parse import urlparse
from app.core.config import settings


def ppd_transport_is_sandbox():
    if settings.TIMBRACFDI_ENV.strip().lower() != 'sandbox': return False
    url=settings.TIMBRACFDI_SOAP_URL if settings.TIMBRACFDI_USE_SOAP else settings.timbracfdi_active_base_url
    try:
        parsed=urlparse(url)
        return (parsed.scheme=='https' and parsed.hostname=='pruebas.timbracfdi33.mx'
                and parsed.port in (None,443,1444) and parsed.username is None and parsed.password is None)
    except ValueError:
        return False


def rest_transport_is_sandbox():
    if settings.TIMBRACFDI_ENV.strip().lower() != 'sandbox': return False
    try:
        parsed = urlparse(settings.timbracfdi_active_base_url)
        return (parsed.scheme == 'https' and parsed.hostname == 'pruebas.timbracfdi33.mx'
                and parsed.port in (None, 443, 1444) and parsed.username is None and parsed.password is None)
    except ValueError:
        return False


def ppd_production_configured():
    """Separate authorization and exact destination; no change to PAC credentials."""
    if (settings.TIMBRACFDI_ENV.strip().lower() != 'production'
            or not settings.FISCAL_PPD_PRODUCTION_ENABLED
            or not settings.FISCAL_PPD_PRODUCTION_EMITTER_RFC
            or not settings.timbracfdi_active_token):
        return False
    try:
        url = settings.TIMBRACFDI_SOAP_URL if settings.TIMBRACFDI_USE_SOAP else settings.timbracfdi_active_base_url
        parsed = urlparse(url)
        return (parsed.scheme == 'https' and parsed.hostname == 'timbracfdi33.mx'
            and parsed.username is None and parsed.password is None and not parsed.query and not parsed.fragment
            and ((settings.TIMBRACFDI_USE_SOAP and parsed.port == 1443 and parsed.path == '/Timbrado.asmx')
                 or (not settings.TIMBRACFDI_USE_SOAP and parsed.port == 1444 and parsed.path.rstrip('/') == '/api/v2')))
    except ValueError:
        return False


def ppd_transport_is_allowed(emitter_rfc=None):
    if ppd_transport_is_sandbox(): return True
    return ppd_production_configured() and (emitter_rfc is None or
        emitter_rfc == settings.FISCAL_PPD_PRODUCTION_EMITTER_RFC)


def fiscal_operation_is_production(kind):
    flag = {'cancellation': settings.FISCAL_CANCELLATION_PRODUCTION_ENABLED,
            'correction': settings.FISCAL_CORRECTIONS_PRODUCTION_ENABLED}.get(kind, False)
    if (not flag or settings.AUTH_CONNECTOR.strip().lower() != 'jwt' or settings.AUTH_ALLOW_ANONYMOUS
            or settings.TIMBRACFDI_ENV.strip().lower() != 'production'
            or not settings.FISCAL_OPERATIONS_PRODUCTION_EMITTER_RFC or not settings.timbracfdi_active_token
            or not settings.TIMBRACFDI_USE_SOAP): return False
    try:
        parsed = urlparse(settings.TIMBRACFDI_SOAP_URL)
        return (parsed.scheme == 'https' and parsed.hostname == 'timbracfdi33.mx'
            and parsed.port == 1443 and parsed.path == '/Timbrado.asmx'
            and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment)
    except ValueError: return False


def fiscal_operation_allowed(kind, emitter_rfc=None):
    if rest_transport_is_sandbox() and (kind != 'correction' or ppd_transport_is_sandbox()): return True
    return fiscal_operation_is_production(kind) and (emitter_rfc is None or
        emitter_rfc == settings.FISCAL_OPERATIONS_PRODUCTION_EMITTER_RFC)
