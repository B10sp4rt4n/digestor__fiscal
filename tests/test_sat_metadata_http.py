"""The lab transport cannot target arbitrary services or production URLs."""
import pytest
from app.services.sat_metadata_http import lab_http_transport


@pytest.mark.parametrize('url', ['https://127.0.0.1:1234', 'http://example.com:1234',
    'http://127.0.0.1:1234/wrong', 'http://user:password@127.0.0.1:1234',
    'http://127.0.0.1:1234?host=external', 'http://127.0.0.1:1234#fragment', 'http://127.0.0.1'])
def test_non_lab_destinations_rejected_before_connection(url):
    with pytest.raises(ValueError): lab_http_transport(url, 'digestor', b'x'*32)


def test_short_key_is_rejected():
    with pytest.raises(ValueError): lab_http_transport('http://127.0.0.1:1234', 'digestor', b'short')
