"""An environment label must not route PPD tests to a production endpoint."""
from urllib.parse import urlparse
from app.core.config import settings


def ppd_transport_is_sandbox():
    if settings.timbracfdi_environment != 'sandbox': return False
    url=settings.TIMBRACFDI_SOAP_URL if settings.TIMBRACFDI_USE_SOAP else settings.timbracfdi_active_base_url
    try:
        parsed=urlparse(url)
        return (parsed.scheme=='https' and parsed.hostname=='pruebas.timbracfdi33.mx'
                and parsed.port in (None,443,1444) and parsed.username is None and parsed.password is None)
    except ValueError:
        return False
