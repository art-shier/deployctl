"""Cold and cached pulls from an isolated Registry using actual Compose events."""
import io
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.request import Request,urlopen
from urllib.parse import urljoin
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(Path(sys.argv[1]).resolve()) if len(sys.argv)>1 else str(ROOT))
from deployctl.progress import Progress
from deployctl.runtime import DockerDriver

def run(*args,raw=None,check=True):
    result=subprocess.run(list(map(str,args)),input=raw,capture_output=True,timeout=60)
    if check and result.returncode: raise RuntimeError('disposable Docker progress fixture failed')
    return result.stdout.decode().strip()

def main():
    if sys.platform!='linux':raise SystemExit('real Docker progress smoke requires Linux')
    name='ctl-pull-progress-'+uuid.uuid4().hex[:12]
    image=None
    proxy=name+'-proxy'
    try:
        run('docker','run','-d','--name',name,'-p','127.0.0.1::5000','registry:2.8.3')
        host=run('docker','port',name,'5000/tcp')
        for attempt in range(50):
            try:
                with urlopen('http://'+host+'/v2/',timeout=1):break
            except OSError:time.sleep(.1)
        else:raise RuntimeError('fixture Registry did not start')
        image=host+'/'+name+':fixture'
        # Upload a unique layer directly: local build/import leaves cached content
        # even after image removal on Docker's containerd image store.
        archive=io.BytesIO()
        with tarfile.open(fileobj=archive,mode='w') as package:
            data=os.urandom(4*1024*1024)
            entry=tarfile.TarInfo('fixture');entry.size=len(data)
            package.addfile(entry,io.BytesIO(data))
        layer=gzip.compress(archive.getvalue())
        config=json.dumps({'architecture':'amd64','os':'linux','config':{},
            'rootfs':{'type':'layers','diff_ids':['sha256:'+hashlib.sha256(archive.getvalue()).hexdigest()]}}).encode()
        def blob(raw):
            digest='sha256:'+hashlib.sha256(raw).hexdigest()
            with urlopen(Request('http://'+host+'/v2/'+name+'/blobs/uploads/',data=b'',method='POST'),timeout=10) as response:
                location=urljoin('http://'+host,response.headers['Location'])
            with urlopen(Request(location+('&' if '?' in location else '?')+'digest='+digest,data=raw,method='PUT',
                headers={'Content-Type':'application/octet-stream'}),timeout=15):pass
            return {'digest':digest,'size':len(raw)}
        config_info=blob(config);layer_info=blob(layer)
        manifest=json.dumps({'schemaVersion':2,'mediaType':'application/vnd.docker.distribution.manifest.v2+json',
            'config':{**config_info,'mediaType':'application/vnd.docker.container.image.v1+json'},
            'layers':[{**layer_info,'mediaType':'application/vnd.docker.image.rootfs.diff.tar.gzip'}]}).encode()
        with urlopen(Request('http://'+host+'/v2/'+name+'/manifests/fixture',data=manifest,method='PUT',
            headers={'Content-Type':'application/vnd.docker.distribution.manifest.v2+json'}),timeout=10):pass
        run('docker','run','-d','--name',proxy,'--link',name+':fixture-registry','-p','127.0.0.1::8080',
            '-v',str(ROOT/'scripts/docker_progress_proxy.py')+':/proxy.py:ro','python:3.12-alpine','python','/proxy.py')
        proxy_host=run('docker','port',proxy,'8080/tcp')
        for attempt in range(50):
            try:
                with urlopen('http://'+proxy_host+'/v2/',timeout=1):break
            except OSError:time.sleep(.1)
        else:raise RuntimeError('fixture blob proxy did not start')
        digest=proxy_host+'/'+name+'@sha256:'+hashlib.sha256(manifest).hexdigest()
        with tempfile.TemporaryDirectory(prefix=name) as folder:
            base=Path(folder)
            (base/'compose.yaml').write_text('services:\n  app:\n    image: '+digest+'\n')
            output=io.StringIO();progress=Progress(stream=output,interval=1)
            driver=DockerDriver();driver.progress=progress
            with progress.stage('Pulling image'):
                driver.pull(base,name,{**os.environ})
            cold=output.getvalue()
            print(cold.strip())
            assert 'Image layer ' in cold and 'Downloading' in cold and '%' in cold, 'actual layer bytes missing'
            # Tiny extraction may finish between Docker's progress sampling ticks.
            assert 'Pull complete' in cold and 'Image: Pulled' in cold, 'actual layer/image finish missing'
            output.seek(0);output.truncate()
            with progress.stage('Pulling cached image'):
                driver.pull(base,name,{**os.environ})
            assert 'Image: Pulled' in output.getvalue()
            quiet=io.StringIO();driver.progress=Progress(stream=quiet,enabled=False)
            driver.pull(base,name,{**os.environ});assert quiet.getvalue()==''
            driver.progress=progress
            (base/'compose.yaml').write_text('services:\n  app:\n    image: '+digest.split('@')[0]+'@sha256:'+'0'*64+'\n')
            output.seek(0);output.truncate()
            try:
                with progress.stage('Pulling missing image'):driver.pull(base,name,{**os.environ})
            except RuntimeError:pass
            else:raise AssertionError('missing digest reported success')
            assert '[failed]' in output.getvalue() and '[done]' not in output.getvalue()
        print('PASS: real cold/cached Compose pulls, layer counters/completion, failure, quiet and bounded subprocess cleanup')
    finally:
        run('docker','rm','-f','-v',proxy,check=False)
        if image:run('docker','image','rm',image,check=False)
        if 'digest' in locals():run('docker','image','rm',digest,check=False)
        run('docker','rm','-f','-v',name,check=False)

if __name__=='__main__':main()
