"""Real build and authenticated digest-preserving mirror on disposable Linux CI."""
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from deployctl.multiservice_build import ImageTools, deliver_images
from deployctl.release import build_release, unpack_release


def run(*args,input=None):
    result=subprocess.run(list(map(str,args)),input=input,capture_output=True,text=True,timeout=600)
    if result.returncode: raise RuntimeError(f'{args[0]} failed: {result.stderr[-3000:]}')
    return result.stdout.strip()


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); return sock.getsockname()[1]


def main():
    if sys.platform != 'linux': raise SystemExit('Requires disposable Linux Docker runner')
    names=[]
    with tempfile.TemporaryDirectory(prefix='ctl-build-ci-') as folder:
        root=Path(folder); authroot=root/'auth'; authroot.mkdir(mode=0o700)
        os.environ['DOCKER_CONFIG']=str(authroot)
        os.environ['BUILDX_BUILDER']='default'
        try:
            auths={}; hosts=[]
            for role in ('source','target'):
                username=role; password=uuid.uuid4().hex; host='127.0.0.1:'+str(port())
                htpasswd=run('docker','run','--rm','-i','--entrypoint','htpasswd','httpd:2-alpine','-Bni',username,input=password+'\n')
                password_file=root/(role+'.htpasswd'); password_file.write_text(htpasswd+'\n'); password_file.chmod(0o600)
                name='ctl-build-'+role+'-'+uuid.uuid4().hex[:12]; names.append(name)
                run('docker','run','-d','--name',name,'-p',host+':5000',
                    '-v',str(password_file)+':/auth/htpasswd:ro',
                    '-e','REGISTRY_AUTH=htpasswd','-e','REGISTRY_AUTH_HTPASSWD_REALM=fixture',
                    '-e','REGISTRY_AUTH_HTPASSWD_PATH=/auth/htpasswd','registry:2')
                auths[host]={'auth':base64.b64encode((username+':'+password).encode()).decode()}; hosts.append(host)
            authfile=authroot/'config.json'; authfile.write_text(json.dumps({'auths':auths})); authfile.chmod(0o600)
            for _ in range(40):
                import urllib.request,urllib.error
                try:
                    urllib.request.urlopen('http://'+hosts[-1]+'/v2/',timeout=1)
                except urllib.error.HTTPError as exc:
                    if exc.code == 401: break
                except OSError: pass
                time.sleep(.25)
            else: raise AssertionError('registry not ready')
            (root/'Dockerfile').write_text('FROM scratch\nARG MARKER\nENV MARKER=$MARKER\nCOPY payload /payload\n')
            (root/'payload').write_text('immutable-layer-fixture\n')
            tools=ImageTools(root,authfile,allow_loopback_http=True)
            source=tools.build({'context':'.','dockerfile':'Dockerfile','args':{'MARKER':'source'}},hosts[0]+'/source:fixture','linux/amd64')
            literal='a,b "quoted" $literal $(not-executed)'
            config={'schema_version':2,'application':'delivery-ci','platform':'linux/amd64','services':{
                'api':{'lifecycle':'application','build':{'context':'.','dockerfile':'Dockerfile','args':{'MARKER':literal}},'health':{'exec':['true']}},
                'database':{'lifecycle':'persistent','image':source,'mirror':'project','health':{'exec':['true']}}},
                'jobs':{'restore':{'image':source,'mirror':'project','trigger':'bootstrap','command':['restore']}}}
            repository=hosts[1]+'/project'
            images=deliver_images(config,root,repository,'v1.0.0',tools)
            assert images['database'].split('@')[1] == source.split('@')[1]
            assert images['restore'] == images['database']
            actual=json.loads(tools.inspect(images['api'],'linux/amd64'))
            assert 'MARKER='+literal in actual['config']['Env'], 'build argument was not literal'
            package=build_release(config,images,'v1.0.0',root/'dist')
            release=unpack_release(package,root/'unpacked')
            assert release['schema_version']==3
            assert {k:v['image'] for k,v in {**release['deployment']['services'],**release['deployment']['jobs']}.items()}==images
            export=root/'export'
            run('skopeo','copy','--quiet','--preserve-digests','--src-authfile',authfile,'--src-tls-verify=false',
                'docker://'+images['database'],'dir:'+str(export))
            assert hashlib.sha256((export/'manifest.json').read_bytes()).hexdigest()==source.split('sha256:')[1]
            import re
            blobs=[path for path in export.iterdir() if re.fullmatch('[a-f0-9]{64}',path.name)]
            assert len(blobs)>=2
            for blob in blobs:
                assert hashlib.sha256(blob.read_bytes()).hexdigest()==blob.name, 'copied blob digest mismatch'
            print('PASS: real application build with literal arguments, distinct registry credentials, preserved mirror digest/blobs, complete schema-3 release')
        finally:
            for name in names:
                subprocess.run(['docker','rm','-f','-v',name],capture_output=True)


if __name__=='__main__': main()
