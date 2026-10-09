"""Scoped platform metadata/configuration commands; secrets are opt-in output."""
import json
import os
from pathlib import Path
import re
import stat

from .contract import integer, validate_name, version
from .platform_client import PlatformClient, PlatformError, validate_defaults as protocol_defaults
from .platform_credentials import Credentials
from .runtime_config import MAX_INPUT, validate_key, validate_values
from .runtime_snapshot import reject_links, validate_id

PROJECT_FIELDS = ('name','description','repository','image_repository','default_environment')
GROUP_FIELDS = ('name','description')
IMAGE_REPOSITORY = re.compile(r'[a-z0-9][a-z0-9.-]*(?::[0-9]{1,5})?/[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*\Z')


def add_commands(sub):
    for scope in ('project','group'):
        command = sub.add_parser(scope, help=f'list, create and manage platform {scope} metadata')
        operations = command.add_subparsers(dest='management_operation', required=True)
        for name in ('list','show','create','update','delete',*(['move'] if scope=='project' else [])):
            operation = operations.add_parser(name)
            operation.add_argument('--client-config', help='private credentials; default: ~/.ctl/client.json')
            if name != 'list': operation.add_argument('slug')
            if name in ('create','update'):
                for field in PROJECT_FIELDS if scope=='project' else GROUP_FIELDS:
                    flag = '--default-env' if field=='default_environment' else '--'+field.replace('_','-')
                    operation.add_argument(flag, dest=field)
            if scope=='project' and name in ('create','move'):
                operation.add_argument('--group', required=True)
            if name=='delete': operation.add_argument('--confirm', required=True, metavar='SLUG', help='must exactly match the archived slug')
        config = sub.add_parser(scope+'-config', help=f'read or edit platform {scope} configuration')
        operations = config.add_subparsers(dest='management_operation', required=True)
        for name in ('list','get','set','unset','apply'):
            operation = operations.add_parser(name)
            operation.add_argument('slug')
            operation.add_argument('--client-config', help='private credentials; default: ~/.ctl/client.json')
            if name=='list': continue
            environment = operation.add_mutually_exclusive_group(required=scope=='group')
            environment.add_argument('--env', help='environment; project default when omitted')
            environment.add_argument('--prod', action='store_true', help='alias for --env prod')
            if name=='get': operation.add_argument('--reveal', action='store_true', help='explicitly include secret values')
            else: operation.add_argument('--expected-revision', type=int, help='optimistic revision; default: fetched current revision')
            if name in ('set','unset'):
                operation.add_argument('key')
                operation.add_argument('--kind', choices=('runtime','install'), default='runtime')
            if name=='set':
                operation.add_argument('value', nargs='?')
                operation.add_argument('--value-file', help='private UTF-8 single-line file; keeps secrets out of argv')
                secrecy = operation.add_mutually_exclusive_group()
                secrecy.add_argument('--secret', dest='secret', action='store_const', const=True)
                secrecy.add_argument('--public', dest='secret', action='store_const', const=False)
                operation.set_defaults(secret=None)
            if name=='apply': operation.add_argument('--file', required=True, help='private UTF-8 JSON changes, up to 64 KiB')


def text(value, field, maximum, nonempty=False):
    if not isinstance(value,str) or (nonempty and not value) or '\x00' in value:
        raise ValueError(f'invalid {field}')
    try: size=len(value.encode('utf-8'))
    except UnicodeError: raise ValueError(f'invalid {field}') from None
    if size>maximum: raise ValueError(f'invalid {field}')
    return value


def metadata(value, scope, slug=None):
    if not isinstance(value,dict): raise ValueError('invalid platform metadata')
    validate_name(value.get('slug'),scope)
    if slug is not None and value['slug']!=slug: raise ValueError('platform metadata target mismatch')
    result={'slug':value['slug']}
    fields=PROJECT_FIELDS if scope=='project' else GROUP_FIELDS
    limits={'name':512,'description':8192,'repository':1024,'image_repository':1024}
    for field in fields:
        if field=='default_environment': result[field]=validate_name(value.get(field),'environment',32)
        else: result[field]=text(value.get(field),field,limits[field],field in ('name','image_repository'))
    if scope=='project':
        result['group']=validate_name(value.get('group'),'group')
        if not IMAGE_REPOSITORY.fullmatch(result['image_repository']): raise ValueError('invalid image repository')
    if 'created_at' in value: result['created_at']=text(value['created_at'],'created_at',96)
    return result


def variable_rows(rows, runtime, reveal=False):
    if not isinstance(rows,list) or len(rows)>128: raise ValueError('invalid platform configuration variables')
    result=[]; seen=set(); values={}
    for row in rows:
        if not isinstance(row,dict): raise ValueError('invalid platform configuration variable')
        key=row.get('key');validate_key(key,'configuration',runtime)
        if runtime and key in ('BASH_ENV','ENV','BASHOPTS','SHELLOPTS'): raise ValueError('configuration contains startup controls')
        if key in seen or type(row.get('secret')) is not bool or type(row.get('configured')) is not bool:
            raise ValueError('invalid platform configuration variable')
        seen.add(key)
        item={k:row[k] for k in ('key','secret','configured')}
        if 'value' in row:
            values[key]=row['value']
            if reveal or not row['secret']: item['value']=row['value']
        result.append(item)
    validate_values(values,'configuration',runtime)
    return result


def defaults(value):
    # The management API uses zero/empty fields to clear defaults and caps CPU
    # at 64. Deployment manifests have a different resource range.
    try:
        if not isinstance(value,dict) or set(value)-{'host_port','bind_address','memory_limit','cpus'}: raise ValueError()
        check=dict(value)
        for field,empty,types in [('host_port',0,(int,)),('cpus',0,(int,float)),('bind_address','',(str,)),('memory_limit','',(str,))]:
            if field in check:
                if type(check[field]) not in types: raise ValueError()
                if check[field]==empty: check.pop(field)
        if 'cpus' in check and not 0.1<=check['cpus']<=64: raise ValueError()
        protocol_defaults(check)
        return dict(value)
    except ValueError: raise ValueError('invalid deployment defaults') from None


def configuration(value, scope, environment, reveal=False):
    if not isinstance(value,dict) or value.get('environment')!=environment:
        raise ValueError('invalid or mismatched platform environment')
    validate_id(value.get('id')); integer(value.get('revision'),1,2**63-1,'configuration revision')
    result={k:value[k] for k in ('id','environment','revision')}
    for field in ('runtime_env','install_params'):
        result[field]=variable_rows(value.get(field),field=='runtime_env',reveal)
    if 'created_at' in value: result['created_at']=text(value['created_at'],'created_at',96)
    if scope=='project':
        target=value.get('target_version')
        if target!='stable': version(target)
        result['target_version']=target
        result['deployment_defaults']=defaults(value.get('deployment_defaults'))
        for field in ('inherited_runtime_env','inherited_install_params'):
            result[field]=variable_rows(value.get(field,[]),field=='inherited_runtime_env',reveal)
        source=value.get('group_source')
        if source is not None:
            if not isinstance(source,dict): raise ValueError('invalid group configuration source')
            validate_name(source.get('slug'),'group');validate_id(source.get('id'))
            integer(source.get('revision'),1,2**63-1,'group configuration revision')
            source={k:source[k] for k in ('slug','id','revision')}
        result['group_source']=source
    return result


def private_input(path):
    path=Path(path).expanduser().absolute();reject_links(path)
    flags=os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_BINARY',0)
    with os.fdopen(os.open(path,flags),'rb') as handle:
        info=os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size>MAX_INPUT:
            raise ValueError('configuration input must be a bounded regular file')
        if os.name!='nt' and (info.st_uid!=os.geteuid() or info.st_mode&0o077):
            raise ValueError('configuration input requires current ownership and private permissions')
        raw=handle.read(MAX_INPUT+1)
    if len(raw)>MAX_INPUT: raise ValueError('configuration input exceeds 64 KiB')
    try: return raw.decode('utf-8')
    except UnicodeError: raise ValueError('configuration input must be UTF-8') from None


def changes_payload(value, scope):
    allowed={'expected_revision','runtime_env','install_params'}
    if scope=='project': allowed.update(('deployment_defaults','target_version'))
    if not isinstance(value,dict) or set(value)-allowed or not set(value).intersection(allowed-{'expected_revision'}):
        raise ValueError('invalid configuration changes shape')
    result={}
    if 'expected_revision' in value:
        result['expected_revision']=integer(value['expected_revision'],0,2**63-1,'expected revision')
    for field in ('runtime_env','install_params'):
        if field not in value: continue
        rows=value[field];seen=set();values={}
        if not isinstance(rows,list) or len(rows)>128: raise ValueError('invalid configuration changes')
        result[field]=[]
        for row in rows:
            if not isinstance(row,dict) or set(row)-{'key','operation','secret','value'} or not {'key','operation'}<=set(row):
                raise ValueError('invalid configuration change shape')
            key=row['key'];validate_key(key,'configuration',field=='runtime_env')
            if field=='runtime_env' and key in ('BASH_ENV','ENV','BASHOPTS','SHELLOPTS'): raise ValueError('configuration contains startup controls')
            if key in seen: raise ValueError('duplicate configuration change')
            seen.add(key)
            if 'secret' in row and type(row['secret']) is not bool: raise ValueError('invalid secret classification')
            operation=row['operation']
            if operation=='set' and 'value' in row: values[key]=row['value']
            elif operation=='keep' and 'value' not in row: pass
            elif operation=='remove' and not {'value','secret'}.intersection(row): pass
            else: raise ValueError('invalid configuration change operation')
            result[field].append(dict(row))
        validate_values(values,'configuration',field=='runtime_env')
    if 'deployment_defaults' in value: result['deployment_defaults']=defaults(value['deployment_defaults'])
    if 'target_version' in value:
        if value['target_version']!='stable': version(value['target_version'])
        result['target_version']=value['target_version']
    return result


def apply_input(path, scope):
    def unique(pairs):
        value={}
        for key,item in pairs:
            if key in value: raise ValueError('configuration JSON contains duplicate fields')
            value[key]=item
        return value
    try: value=json.loads(private_input(path),object_pairs_hook=unique)
    except json.JSONDecodeError: raise ValueError('invalid configuration JSON') from None
    return changes_payload(value,scope)


def group_metadata(client,slug):
    groups=client.json('GET','/api/v1/groups')
    if not isinstance(groups,list): raise ValueError('invalid group listing')
    groups=[metadata(item,'group') for item in groups]
    matches=[item for item in groups if item['slug']==slug]
    if len(matches)!=1: raise ValueError('group not found in accessible listing')
    return matches[0]


def handle(args):
    scope=args.command.split('-')[0];operation=args.management_operation
    is_config=args.command.endswith('-config')
    if operation!='list' or is_config: validate_name(args.slug,scope)
    if operation=='delete' and args.confirm!=args.slug: raise ValueError('--confirm must exactly match the slug')
    if hasattr(args,'group'): validate_name(args.group,'group')
    payload=None
    if is_config and operation in ('set','unset','apply'):
        if args.expected_revision is not None: integer(args.expected_revision,0,2**63-1,'expected revision')
        if operation=='apply': payload=apply_input(args.file,scope)
        else:
            row={'key':args.key,'operation':'set' if operation=='set' else 'remove'}
            if operation=='set':
                if (args.value is None)==(args.value_file is None): raise ValueError('set requires exactly one VALUE or --value-file')
                raw=private_input(args.value_file) if args.value_file is not None else args.value
                if args.value_file is not None:
                    if raw.endswith('\r\n'): raw=raw[:-2]
                    elif raw.endswith('\n'): raw=raw[:-1]
                row['value']=raw
                if args.secret is not None: row['secret']=args.secret
            payload=changes_payload({'runtime_env' if args.kind=='runtime' else 'install_params':[row]},scope)
    client=PlatformClient(Credentials.load(args.client_config))
    base='/api/v1/'+scope+'s'
    if not is_config:
        if operation=='list':
            value=client.json('GET',base)
            if not isinstance(value,list): raise ValueError('invalid metadata listing')
            return [metadata(item,scope) for item in value]
        path=base+'/'+args.slug
        if operation=='delete':
            raw=client.request('DELETE',path,expected_status=204)
            if raw: raise ValueError('unexpected deletion response')
            return {'slug':args.slug,'archived':True}
        if operation in ('show','update','move'):
            current=metadata(client.json('GET',path),scope,args.slug) if scope=='project' else group_metadata(client,args.slug)
            if operation=='show': return current
            if operation=='move':
                return metadata(client.json('PATCH',path+'/group',{'group':args.group,'expected_group':current['group']}),scope,args.slug)
            fields=PROJECT_FIELDS if scope=='project' else GROUP_FIELDS
            selected={field:getattr(args,field) for field in fields}
            selected={field:value for field,value in selected.items() if value is not None}
            if not selected: raise ValueError('update requires at least one metadata field')
            body={key:current[key] for key in ('slug',*(PROJECT_FIELDS if scope=='project' else GROUP_FIELDS))}
            body.update(selected)
        else:
            body={'slug':args.slug,'name':args.name if args.name is not None else args.slug,'description':args.description or ''}
            if scope=='project':
                body.update(group=args.group,repository=args.repository or '',default_environment=args.default_environment or 'prod')
                if args.image_repository is not None: body['image_repository']=args.image_repository
        # Validate outgoing metadata without requiring a server-default repository.
        check=dict(body)
        if scope=='project': check.setdefault('group',current['group'] if operation=='update' else args.group);check.setdefault('image_repository','check.test/check')
        metadata(check,scope,args.slug)
        return metadata(client.json('POST' if operation=='create' else 'PATCH',base if operation=='create' else path,body),scope,args.slug)
    path=base+'/'+args.slug+'/environments'
    if operation=='list':
        value=client.json('GET',path)
        if not isinstance(value,list) or len(value)>1024: raise ValueError('invalid environment listing')
        for item in value: validate_name(item,'environment',32)
        return value
    environment='prod' if args.prod else args.env
    if environment is None:
        environment=metadata(client.json('GET',base+'/'+args.slug),scope,args.slug)['default_environment']
    validate_name(environment,'environment',32)
    path+='/'+environment
    if operation=='get':
        return configuration(client.json('GET',path+('?reveal=true' if args.reveal else '')),scope,environment,args.reveal)
    try: current=configuration(client.json('GET',path),scope,environment)['revision']
    except PlatformError as exc:
        if exc.status!=404: raise
        current=0
    payload.setdefault('expected_revision',current)
    if args.expected_revision is not None: payload['expected_revision']=args.expected_revision
    saved=configuration(client.json('PUT',path,payload),scope,environment)
    return {key:saved[key] for key in ('id','environment','revision')}
