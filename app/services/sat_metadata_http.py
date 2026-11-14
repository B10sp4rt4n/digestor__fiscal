"""Signed loopback HTTP lab transport. No redirects, discovery or production URL."""
from hashlib import sha256
import hmac
from http.client import HTTPConnection
import json
import re
import time
from uuid import uuid4
from urllib.parse import urlparse
from app.services.sat_metadata_import import encoded

PATH = '/v1/staging/sat-metadata'


class VerifiedLabAcknowledgment(dict):
    """Stable receipt body plus separately retained proof of this verified attempt."""
    def __init__(self, body, proof):
        super().__init__(body)
        self.transport_proof = proof


def signed_response_message(service, timestamp, nonce, request, status, response):
    """Response domain binds exact bytes and status to one authenticated attempt."""
    return ('cima-metadata-lab-response-v1\n'+service+'\nPOST\n'+PATH+'\n'+timestamp+'\n'
            +nonce+'\n'+str(status)+'\n'+sha256(request).hexdigest()+'\n'+sha256(response).hexdigest()).encode()


def lab_http_transport(base_url, service_id, secret, *, response_secret=None, expected_cima_company=None):
    url = urlparse(base_url)
    if (url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port
            or url.path not in ('', '/') or url.query or url.fragment or url.username or url.password
            or len(secret) < 32):
        raise ValueError('Explicit loopback lab URL and service key required')
    hardened = response_secret is not None or expected_cima_company is not None
    if hardened:
        from uuid import UUID
        if (not isinstance(response_secret, bytes) or len(response_secret) < 32
                or hmac.compare_digest(response_secret, secret)
                or not isinstance(expected_cima_company, str)):
            raise ValueError('Independent response key and explicit CIMA destination required')
        if str(UUID(expected_cima_company)) != expected_cima_company:
            raise ValueError('Canonical CIMA destination UUID required')
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
            if hardened:
                signatures = response.headers.get_all('X-Response-Signature', [])
                expected = hmac.new(response_secret,
                    signed_response_message(service_id, stamp, nonce, raw, response.status, payload), sha256).hexdigest()
                if (len(signatures) != 1 or not re.fullmatch(r'[a-f0-9]{64}', signatures[0])
                        or not hmac.compare_digest(expected, signatures[0])):
                    raise ValueError('Unauthenticated acknowledgment')
            ack = json.loads(payload)
            if hardened and (not isinstance(ack, dict)
                    or ack.get('cima_empresa_id') != expected_cima_company
                    or ack.get('cima_tenant_id') != expected_cima_company
                    or not isinstance(ack.get('authorization_sha256'), str)
                    or not re.fullmatch(r'[a-f0-9]{64}', ack['authorization_sha256'])):
                raise ValueError('Acknowledgment destination mismatch')
            if hardened:
                return VerifiedLabAcknowledgment(ack, {
                    'protocol':'cima-metadata-lab-response-v1', 'service_id':service_id,
                    'timestamp':stamp, 'nonce':nonce, 'status':response.status,
                    'request_sha256':sha256(raw).hexdigest(),
                    'response_sha256':sha256(payload).hexdigest(),
                    'response_body':payload.decode('utf-8'), 'signature':signatures[0],
                })
            return ack
        finally:
            connection.close()
    return send


def hardened_lab_http_transport(base_url, service_id, secret, *, response_secret, expected_cima_company):
    """Synthetic-pilot client: reject unsigned, altered, replayed or misdirected receipts."""
    if response_secret is None or expected_cima_company is None:
        raise ValueError('Hardened transport cannot fall back to unsigned receipts')
    return lab_http_transport(base_url, service_id, secret, response_secret=response_secret,
                              expected_cima_company=expected_cima_company)
