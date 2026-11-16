"""Explicit, reviewed persistent-service maintenance; no in-place image migration."""
import copy
import hashlib
import json
import re
from .contract import validate_name


def maintenance_plan(plan, release, observed, old_drivers, request):
    if not isinstance(request,dict) or set(request)-{'services','backup_sha256','plan_id'}:
        raise ValueError('invalid maintenance request')
    names=request.get('services'); backup=request.get('backup_sha256')
    if not isinstance(names,list) or not names or any(not isinstance(n,str) for n in names) or len(set(names))!=len(names):
        raise ValueError('maintenance requires unique service names')
    if not isinstance(backup,str) or not re.fullmatch('[a-f0-9]{64}',backup): raise ValueError('maintenance requires verified backup SHA256')
    result=copy.deepcopy(plan)
    for name in names:
        validate_name(name,'service')
        old=old_drivers.get(name); spec=release['deployment']['services'].get(name)
        if not old or not spec or spec['lifecycle']!='persistent': raise ValueError('maintenance requires established persistent service')
        previous=old.config['services'][name]
        if previous['image']!=spec['image'] or previous['volumes']!=spec['volumes']:
            raise ValueError('persistent image/volume changes require isolated restore')
        result['services'][name]='update'
    for name,spec in release['deployment']['services'].items():
        if spec['lifecycle']=='application' and result['services'][name] in ('unchanged','unhealthy'):
            result['services'][name]='start'
    result['applicable']=(all(v in ('create','update','start','unchanged') for v in result['services'].values())
                          and all(v in ('reuse','retained-unreferenced') for v in result['volumes'].values()))
    content={'target':release,'fingerprints':result['fingerprints'],'observed':observed,
             'services':sorted(names),'backup_sha256':backup}
    identity=hashlib.sha256(json.dumps(content,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    result['maintenance']={'services':sorted(names),'backup_sha256':backup,'plan_id':identity}
    return result
