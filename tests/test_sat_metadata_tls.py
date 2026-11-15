"""The TLS pilot never downgrades to HTTP or accepts an unverified SSL context."""
import ssl
import pytest
from app.services.sat_metadata_http import hardened_lab_https_transport, lab_http_transport


@pytest.mark.parametrize('url', ['http://127.0.0.1:1234','ftp://127.0.0.1:1234'])
def test_tls_pilot_rejects_plaintext_before_loading_files(url):
    with pytest.raises(ValueError):
        hardened_lab_https_transport(url,'synthetic-digestor',b'a'*32,response_secret=b'b'*32,
            expected_cima_company='44444444-4444-4444-8444-444444444444',
            ca_file='absent',client_cert='absent',client_key='absent')


@pytest.mark.parametrize('mutation', ['hostname','verification','version'])
def test_internal_tls_context_cannot_disable_required_verification(mutation):
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version=ssl.TLSVersion.TLSv1_3
    if mutation=='hostname':context.check_hostname=False
    if mutation=='verification':
        context.check_hostname=False;context.verify_mode=ssl.CERT_NONE
    if mutation=='version':context.minimum_version=ssl.TLSVersion.TLSv1_2
    with pytest.raises(ValueError):
        lab_http_transport('https://127.0.0.1:1234','synthetic-digestor',b'a'*32,_tls_context=context)


def test_tls_pilot_cannot_omit_response_authentication():
    with pytest.raises(ValueError):
        hardened_lab_https_transport('https://127.0.0.1:1234','synthetic-digestor',b'a'*32,
            response_secret=None,expected_cima_company=None,ca_file='absent',client_cert='absent',client_key='absent')
