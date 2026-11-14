"""Verify responses cryptographically, including attempt binding and destination."""
from hashlib import sha256
from email.message import Message
from uuid import uuid4
import hmac
import json
import pytest

from app.services import sat_metadata_http as transport

REQUEST_KEY = b'synthetic-request-key-32-bytes!!!'
RESPONSE_KEY = b'synthetic-response-key-32-bytes!!'
DESTINATION = '44444444-4444-4444-8444-444444444444'


@pytest.mark.parametrize('mutation', ['', 'body', 'status', 'nonce', 'request', 'wrong_key',
                                    'signature', 'missing', 'duplicate', 'destination'])
def test_response_authentication_and_destination(monkeypatch, mutation):
    class Connection:
        def __init__(self, *args, **kwargs): pass
        def request(self, method, path, body, headers):
            self.raw, self.headers = body, headers
        def getresponse(self):
            ack = {'cima_empresa_id': DESTINATION, 'cima_tenant_id': DESTINATION, 'authorization_sha256':'a'*64}
            if mutation == 'destination': ack['cima_empresa_id'] = str(uuid4())
            payload = json.dumps(ack).encode()
            nonce = '0'*32 if mutation == 'nonce' else self.headers['X-Nonce']
            request = b'altered' if mutation == 'request' else self.raw
            secret = REQUEST_KEY if mutation == 'wrong_key' else RESPONSE_KEY
            message = transport.signed_response_message('digestor', self.headers['X-Timestamp'], nonce, request, 200, payload)
            signature = hmac.new(secret, message, sha256).hexdigest()
            if mutation == 'signature': signature = '0'*64
            if mutation == 'body': payload += b' '
            class Response:
                status = 201 if mutation == 'status' else 200
                headers = Message()
                def read(self, limit): return payload
            response = Response()
            if mutation != 'missing': response.headers['X-Response-Signature'] = signature
            if mutation == 'duplicate': response.headers['X-Response-Signature'] = signature
            return response
        def close(self): pass
    monkeypatch.setattr(transport, 'HTTPConnection', Connection)
    send = transport.hardened_lab_http_transport('http://127.0.0.1:1234','digestor', REQUEST_KEY,
        response_secret=RESPONSE_KEY, expected_cima_company=DESTINATION)
    if mutation:
        with pytest.raises(ValueError): send({'synthetic':True})
    else:
        assert send({'synthetic':True})['cima_empresa_id'] == DESTINATION


def test_response_key_must_be_independent_and_destination_explicit():
    for key, destination in [(REQUEST_KEY,DESTINATION),(b'short',DESTINATION),(RESPONSE_KEY,None),(None,None)]:
        with pytest.raises(ValueError):
            transport.hardened_lab_http_transport('http://127.0.0.1:1234','digestor', REQUEST_KEY,
                response_secret=key, expected_cima_company=destination)


def test_recorded_response_is_not_valid_for_a_new_attempt(monkeypatch):
    saved = {}
    class Connection:
        def __init__(self,*args,**kwargs): pass
        def request(self,method,path,body,headers):
            if not saved:
                payload = json.dumps({'cima_empresa_id':DESTINATION,'cima_tenant_id':DESTINATION,
                                      'authorization_sha256':'b'*64}).encode()
                saved.update(payload=payload,signature=hmac.new(RESPONSE_KEY,
                    transport.signed_response_message('digestor',headers['X-Timestamp'],headers['X-Nonce'],
                        body,200,payload),sha256).hexdigest())
        def getresponse(self):
            class Response:
                status=200
                headers=Message()
                def read(self,limit):return saved['payload']
            response=Response();response.headers['X-Response-Signature']=saved['signature'];return response
        def close(self): pass
    monkeypatch.setattr(transport,'HTTPConnection',Connection)
    send=transport.hardened_lab_http_transport('http://127.0.0.1:1234','digestor',REQUEST_KEY,
        response_secret=RESPONSE_KEY,expected_cima_company=DESTINATION)
    send({'synthetic':True})
    with pytest.raises(ValueError,match='Unauthenticated'): send({'synthetic':True})
