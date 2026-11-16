"""Bounded public diagnostics: never return configuration, paths or secret values."""


def observe_stack(stack, state):
    tx = state['transaction']
    journal = tx['journal'] if tx else {}
    result = {'services':{}, 'jobs':{name:item['status'] for name,item in state['jobs'].items()},
              'operation_id':journal.get('id',''), 'phase':journal.get('phase',''),
              'recovery_required':bool(tx)}
    for name, item in state['services'].items():
        if item.get('retired'): continue
        snapshot = stack.load(item['snapshot'])
        spec = snapshot.release['deployment']['services'][name]
        result['services'][name] = {'version':snapshot.ref['version'], 'image':spec['image'],
                                   'lifecycle':spec['lifecycle'], 'health':'unknown'}
    if tx: return result
    try:
        observed = stack.status(state)
        for name, item in observed['services'].items():
            if item.get('retired'): continue
            result['services'][name]['health'] = ('healthy' if item['healthy'] else
                                                 'unhealthy' if item['running'] else 'stopped')
    except (ValueError,RuntimeError,OSError,KeyError,TypeError):
        result['recovery_required'] = True
    return result
