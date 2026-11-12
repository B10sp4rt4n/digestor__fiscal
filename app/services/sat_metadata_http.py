"""Signed loopback HTTP lab transport. No redirects, discovery or production URL."""
from hashlib import sha256
import hmac
from http.client import HTTPConnection
import json
import time
from uuid import uuid4
from urllib.parse import urlparse
from app.services.sat_metadata_import import encoded

PATH = '/v1/staging/sat-metadata'


def lab_http_transport(base_url, service_id, secret):
    url = urlparse(base_url)
    if (url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port
            or url.path not in ('', '/') or url.query or url.fragment or url.username or url.password
            or len(secret) < 32):
        raise ValueError('Explicit loopback lab URL and service key required')
    def send(body):
        raw = encoded(body).encode()
        stamp, nonce = str(time.time()), uuid4().hex
        message = ('cima-metadata-lab-v1\n'+service_id+'\nPOST\n'+PATH+'\n'+stamp+'\n'+nonce+'\n'+sha256(raw).hexdigest()).encode()
        signature = hmac.new(secret, message, sha256).hexdigest()
        connection = HTTPConnection('127.0.0.1', url.port, timeout=3)
        try:
            connection.request('POST', PATH, raw, {'Content-Type': 'application/json',
                'X-Service-Id': service_id, 'X-Timestamp': stamp, 'X-Nonce': nonce, 'X-Signature': signature})
            response = connection.getresponse()
            payload = response.read(65537)
            if response.status != 200 or len(payload) > 65536:
                raise ValueError('HTTP delivery not acknowledged')
            return json.loads(payload)
        finally:
            connection.close()
    return send
