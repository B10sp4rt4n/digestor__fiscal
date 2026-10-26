"""Refresh public SAT schemas deliberately; review hashes before committing. Not used at runtime."""
import requests,json,hashlib,gzip
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as E
from datetime import datetime,timezone
roots=['http://www.sat.gob.mx/sitio_internet/cfd/TimbreFiscalDigital/TimbreFiscalDigitalv11.xsd','http://www.sat.gob.mx/sitio_internet/cfd/Pagos/Pagos20.xsd','http://www.sat.gob.mx/sitio_internet/cfd/4/cfdv40.xsd']
files={};queue=roots[:]
while queue:
 url=queue.pop(0)
 if url in files:continue
 assert urlparse(url).hostname=='www.sat.gob.mx'
 r=requests.get(url.replace('http:','https:',1),timeout=35);r.raise_for_status()
 raw=r.content
 assert len(raw)<15000000
 node=E.fromstring(raw)
 assert node.tag=='{http://www.w3.org/2001/XMLSchema}schema'
 files[url]=raw.decode('utf-8')
 print(url,len(raw),flush=True)
 for row in node.findall('{http://www.w3.org/2001/XMLSchema}import'):
  if row.get('schemaLocation'):queue.append(row.get('schemaLocation'))
encoded=json.dumps(files,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
compressed=gzip.compress(encoded,mtime=0)
directory=Path(__file__).resolve().parents[1]/'app/resources/sat';directory.mkdir(parents=True,exist_ok=True)
path=directory/'rep20-schemas.json.gz';path.write_bytes(compressed)
manifest={'retrieved_at_utc':datetime.now(timezone.utc).isoformat(),'bundle_sha256':hashlib.sha256(compressed).hexdigest(),'sources':[{'url':k,'sha256':hashlib.sha256(v.encode()).hexdigest(),'bytes':len(v.encode())} for k,v in sorted(files.items())]}
(directory/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print('bundle bytes',path.stat().st_size,flush=True)
