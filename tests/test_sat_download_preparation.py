import base64
from datetime import date, datetime, timedelta, timezone
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
import zipfile
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

spec=importlib.util.spec_from_file_location('sat_download',Path(__file__).parents[1]/'scripts/sat_download.py')
s=importlib.util.module_from_spec(spec); spec.loader.exec_module(s)


def package(text='demo'):
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w') as z: z.writestr('synthetic.xml',text)
    return base64.b64encode(buf.getvalue()).decode()


class Client:
    def __init__(self): self.calls=[]; self.status={'CodEstatus':'5000','EstadoSolicitud':3,'NumeroCFDIs':1,'IdsPaquetes':['package-1']}
    def recover_comprobante_emitted_request(self,**kw):
        self.calls.append(kw); return {'CodEstatus':'5000','IdSolicitud':'request-1'}
    def recover_comprobante_status(self,request_id): return self.status
    def recover_comprobante_download(self,**kw): return {'CodEstatus':'5000'},package()


class Acquisition(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name); self.root.chmod(0o700)
        self.db=s.open_store(self.root); self.client=Client()
    def tearDown(self): self.db.close(); self.tmp.cleanup()
    def plan(self): s.plan(self.db,date(2026,6,1),date(2026,6,1))
    def test_plan_repeat_does_not_duplicate(self):
        self.plan(); self.plan(); self.assertEqual(self.db.execute('SELECT count(*) FROM jobs').fetchone()[0],2)
    def test_reject_future(self):
        with self.assertRaises(s.SafeError): s.plan(self.db,date(2099,1,1),date(2099,1,2))
    def test_roundtrip_restart_and_metadata_scope(self):
        self.plan(); self.assertEqual(s.tick(self.db,self.root,self.client,at=1),'submitted')
        self.assertEqual(self.client.calls[0]['rfc_emisor'],s.RFC)
        self.assertEqual(self.client.calls[0]['estado_comprobante'],'Vigente')
        self.db.close(); self.db=s.open_store(self.root)
        self.assertEqual(s.tick(self.db,self.root,self.client,at=902),'acquired')
        self.assertEqual(s.tick(self.db,self.root,self.client,at=903),'submitted')
        self.assertEqual(self.client.calls[1]['tipo_solicitud'],'Metadata')
        self.assertEqual(self.client.calls[1]['estado_comprobante'],'Todos')
    def test_ambiguous_submit_not_repeated(self):
        self.plan()
        def fail(**kwargs): raise RuntimeError('secret must not escape')
        self.client.recover_comprobante_emitted_request=fail
        self.assertEqual(s.tick(self.db,self.root,self.client,1),'submission_uncertain')
        self.assertEqual(self.db.execute("SELECT count(*) FROM jobs WHERE state='submission_uncertain'").fetchone()[0],1)
    def test_crash_during_submit_is_uncertain(self):
        self.plan(); self.db.execute("UPDATE jobs SET state='submitting'"); self.db.commit()
        self.assertEqual(s.tick(self.db,self.root,self.client,1),'idle')
        self.assertEqual(self.db.execute("SELECT count(*) FROM jobs WHERE state='submission_uncertain'").fetchone()[0],2)
    def test_waiting_does_not_resubmit(self):
        self.plan(); s.tick(self.db,self.root,self.client,1)
        self.client.status['EstadoSolicitud']=2
        self.assertEqual(s.tick(self.db,self.root,self.client,902),'waiting')
        self.assertEqual(len(self.client.calls),1)
    def test_missing_packages_not_marked_acquired(self):
        self.plan(); s.tick(self.db,self.root,self.client,1); self.client.status['IdsPaquetes']=[]
        self.assertEqual(s.tick(self.db,self.root,self.client,902),'waiting')
    def test_rejected_state_blocked(self):
        self.plan(); s.tick(self.db,self.root,self.client,1); self.client.status['EstadoSolicitud']=5
        self.assertEqual(s.tick(self.db,self.root,self.client,902),'blocked')
    def test_hash_conflict_rejected(self):
        s.save_package(self.db,self.root,'p1',package('first'))
        with self.assertRaises(s.SafeError): s.save_package(self.db,self.root,'p1',package('different'))
    def test_bad_base64_rejected(self):
        with self.assertRaises(s.SafeError): s.save_package(self.db,self.root,'p1','!!!')
    def test_path_package_id_rejected(self):
        with self.assertRaises(s.SafeError): s.save_package(self.db,self.root,'../escape',package())
    def test_zip_not_extracted(self):
        s.save_package(self.db,self.root,'p1',package())
        self.assertFalse((self.root/'synthetic.xml').exists())
    def test_worker_lock_excludes_second_process_handle(self):
        with s.worker_lock(self.root):
            with self.assertRaises(s.SafeError):
                with s.worker_lock(self.root): pass


class Credentials(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        self.env={}
        for suffix,name in [('CER','certificate'),('KEY','encrypted-key'),('PASSWORD','password')]:
            self.env['SAT_EFIRMA_'+suffix+'_FILE']=str(self.root/name)
        self.write('PASSWORD',b'only-synthetic-password')
        self.write('KEY',self.key.private_bytes(serialization.Encoding.DER,serialization.PrivateFormat.PKCS8,serialization.BestAvailableEncryption(b'only-synthetic-password')))
        self.certificate(s.RFC)
    def tearDown(self): self.tmp.cleanup()
    def write(self,kind,data):
        p=Path(self.env['SAT_EFIRMA_'+kind+'_FILE']); p.write_bytes(data); p.chmod(0o600)
    def certificate(self,rfc,expired=False):
        name=x509.Name([x509.NameAttribute(x509.ObjectIdentifier('2.5.4.45'),rfc)])
        now=datetime.now(timezone.utc)
        cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(self.key.public_key())
              .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=2))
              .not_valid_after(now+timedelta(days=-1 if expired else 30)).sign(self.key,hashes.SHA256()))
        self.write('CER',cert.public_bytes(serialization.Encoding.DER))
    def test_valid_pair_preflight_without_network(self):
        _,status=s.credentials(self.env)
        self.assertEqual(status['rfc'],s.RFC); self.assertTrue(status['key_matches'])
        self.assertEqual(status['sat_authentication'],'not_attempted')
    def test_wrong_rfc_rejected(self):
        self.certificate('AAA010101AAA')
        with self.assertRaisesRegex(s.SafeError,'rfc_outside_pilot'): s.credentials(self.env)
    def test_expired_rejected(self):
        self.certificate(s.RFC,True)
        with self.assertRaisesRegex(s.SafeError,'certificate_not_current'): s.credentials(self.env)
    def test_public_permissions_rejected(self):
        Path(self.env['SAT_EFIRMA_KEY_FILE']).chmod(0o644)
        with self.assertRaisesRegex(s.SafeError,'secret_file_permissions'): s.credentials(self.env)
    def test_wrong_password_sanitized(self):
        self.write('PASSWORD',b'wrong-password')
        with self.assertRaisesRegex(s.SafeError,'^credential_validation_failed$'): s.credentials(self.env)
    def test_different_private_key_rejected(self):
        other=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        self.write('KEY',other.private_bytes(serialization.Encoding.DER,serialization.PrivateFormat.PKCS8,serialization.BestAvailableEncryption(b'only-synthetic-password')))
        with self.assertRaisesRegex(s.SafeError,'certificate_key_mismatch'): s.credentials(self.env)


if __name__=='__main__': unittest.main()
