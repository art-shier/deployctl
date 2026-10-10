"""Resume a static workflow without rebuilding an already registered version."""
import argparse
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deployctl.platform_client import PlatformClient
from deployctl.platform_credentials import Credentials
from deployctl.contract import validate_name, version as validate_version
import re

def recover_static_release(client,project,version,output,commit):
    validate_name(project);validate_version(version)
    if not isinstance(commit,str) or not re.fullmatch(r'[a-f0-9]{40,64}',commit):raise ValueError('invalid source commit')
    metadata=client.json('GET',f'/api/v1/projects/{project}')
    if metadata.get('slug')!=project or metadata.get('deployment_type')!='static':
        raise ValueError('static workflow requires a registered static project')
    record=client.recover_release(project,version,output,commit)
    if record is not None and record.get('deployment_type')!='static':
        raise ValueError('registered release is not static')
    return record

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',required=True);parser.add_argument('--version',required=True)
    parser.add_argument('--commit',required=True);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--client-config',required=True,type=Path);args=parser.parse_args()
    client=PlatformClient(Credentials.load(args.client_config))
    record=recover_static_release(client,args.project,args.version,args.output,args.commit)
    values={'recovered':'true' if record else 'false'}
    if record:
        values.update(application=args.project,version=args.version,sha256=record['sha256'],archive_format=record['archive_format'])
    if path:=os.environ.get('GITHUB_OUTPUT'):
        with open(path,'a',encoding='utf-8') as file:
            for key,value in values.items():file.write(f'{key}={value}\n')
    print('Recovered verified static release' if record else 'No registered release; build required')

if __name__=='__main__':main()
