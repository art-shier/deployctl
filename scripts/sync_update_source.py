"""Publish verified release files to an S3-compatible update source, index last."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from urllib.error import HTTPError
from urllib.request import Request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from deployctl.bootstrap import update_source, parse_release_index, fetch, MAX_INDEX_SIZE
from deployctl.release_catalog import build_index, encode_index, artifact_digest


def sync(version, artifacts, public_url, destination, endpoint='', runner=subprocess.run, image_repository=None):
    base=update_source(public_url)
    if not base: raise ValueError('mirror public URL is required')
    if not destination.startswith('s3://') or len(destination)<=5 or any(c.isspace() for c in destination):
        raise ValueError('destination must be an S3 bucket/prefix')
    canonical=parse_release_index((artifacts/'update-index.json').read_bytes())
    candidate=next((item for item in canonical['releases'] if item['version']==version),None)
    if candidate is None: raise ValueError('published version is absent from catalog')
    if image_repository:
        from deployctl.server_bundle import read_bundle
        manifest,_=read_bundle(artifacts/f'ctl-platform-{version}.tar.gz')
        if manifest['image'].split('@')[0]!=image_repository:
            raise ValueError('server bundle does not reference configured mirror; publish a new mirror-enabled version')
    names=['deployctl.pyz',f'ctl-platform-{version}.tar.gz','install.sh',f'team-deploy-skill-{version}.zip']
    names += [p.name for p in artifacts.glob(f'team_deployctl-{version[1:]}-*.whl')]
    for name in names: artifact_digest(artifacts/name)
    if candidate['cli_sha256']!=artifact_digest(artifacts/'deployctl.pyz') or candidate['server_sha256']!=artifact_digest(artifacts/f'ctl-platform-{version}.tar.gz'):
        raise ValueError('catalog digest differs from release assets')
    try:
        history=parse_release_index(fetch(Request(base+'/update-index.json'),MAX_INDEX_SIZE))
    except RuntimeError as error:
        if not isinstance(error.__cause__,HTTPError) or error.__cause__.code!=404: raise
        history={'schema_version':1,'generated_at':'1970-01-01T00:00:00Z','releases':[]}
    if history['releases'] and tuple(map(int,history['releases'][0]['version'][1:].split('.'))) > tuple(map(int,version[1:].split('.'))):
        raise ValueError('refusing to move mirror latest to an older version')
    index=build_index(history,canonical['generated_at'],version,candidate['published_at'],artifacts)
    def upload(path, key, cache):
        command=['aws']
        if endpoint: command+=['--endpoint-url',endpoint]
        command+=['s3','cp',str(path),destination.rstrip('/')+'/'+key,'--only-show-errors','--cache-control',cache]
        if path.name.endswith('.json'): command+=['--content-type','application/json']
        runner(command,check=True)
    for name in names:
        raw=(artifacts/name).read_bytes()
        # Versions are immutable. Refuse overwriting a different published artifact.
        try:
            previous=fetch(Request(base+'/'+version+'/'+name),max(len(raw),1))
        except RuntimeError as error:
            if not isinstance(error.__cause__,HTTPError) or error.__cause__.code!=404: raise
        else:
            if previous!=raw: raise ValueError('immutable mirror artifact conflict: '+name)
        for filename in (name,name+'.sha256'):
            upload(artifacts/filename,version+'/'+filename,'public,max-age=31536000,immutable')
        copied=fetch(Request(base+'/'+version+'/'+name),max(len(raw),1))
        if copied!=raw: raise ValueError('public mirror verification failed: '+name)
    # A single atomic object PUT exposes the version only after all downloads work.
    output=artifacts/'mirror-update-index.json';output.write_bytes(encode_index(index))
    upload(output,'update-index.json','no-cache,max-age=0')
    if parse_release_index(fetch(Request(base+'/update-index.json'),MAX_INDEX_SIZE))!=index:
        raise ValueError('public mirror index has not converged; retry sync')
    print('Verified update source:',base,version)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version',required=True)
    parser.add_argument('--artifacts',type=Path,required=True)
    parser.add_argument('--public-url',required=True)
    parser.add_argument('--destination',required=True)
    parser.add_argument('--endpoint',default='')
    parser.add_argument('--image-repository')
    args=parser.parse_args()
    sync(args.version,args.artifacts,args.public_url,args.destination,args.endpoint,image_repository=args.image_repository)
