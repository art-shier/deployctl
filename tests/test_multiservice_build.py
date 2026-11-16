import hashlib
import json
import pytest
from test_multiservice_contract import source, IMAGE


def test_build_plan_scopes_every_component_to_one_repository(tmp_path):
    from deployctl.multiservice_build import delivery_plan
    (tmp_path/'Dockerfile').write_text('FROM scratch\n')
    config = source(); config['jobs'] = {'restore':{'image':IMAGE,'mirror':'project','trigger':'bootstrap','command':['restore']}}
    plan = delivery_plan(config,tmp_path,'registry.example/team/project','v1.2.3')
    assert set(plan['nodes']) == {'api','postgres','restore'}
    assert all(n['tag'].startswith('registry.example/team/project:') for n in plan['nodes'].values())
    assert plan['nodes']['api']['kind'] == 'build'
    assert plan['nodes']['postgres']['source'] == IMAGE
    assert len({n['tag'] for n in plan['nodes'].values()}) == 3


def test_delivery_rejects_external_image_without_mirror_and_path_escape(tmp_path):
    from deployctl.multiservice_build import delivery_plan
    (tmp_path/'Dockerfile').write_text('FROM scratch\n')
    config = source(); config['services']['postgres'].pop('mirror')
    with pytest.raises(ValueError,match='mirror'): delivery_plan(config,tmp_path,'registry.example/team/project','v1.0.0')
    config = source(); config['services']['api']['build']['context'] = '../outside'
    with pytest.raises(ValueError): delivery_plan(config,tmp_path,'registry.example/team/project','v1.0.0')


def manifest(layers=10):
    return json.dumps({'schemaVersion':2,'mediaType':'application/vnd.oci.image.manifest.v1+json',
        'config':{'digest':'sha256:'+'a'*64,'size':20},
        'layers':[{'digest':'sha256:'+'b'*64,'size':layers}]}).encode()


def reference(raw): return 'registry.example/source@sha256:'+hashlib.sha256(raw).hexdigest()


def test_manifest_tree_checks_digest_and_declared_blob_budget():
    from deployctl.multiservice_build import verify_manifest_tree
    raw = manifest()
    assert verify_manifest_tree(reference(raw),lambda ref:raw,max_bytes=100)['bytes'] == 30
    with pytest.raises(ValueError,match='digest'): verify_manifest_tree(reference(raw),lambda ref:raw+b' ')
    with pytest.raises(ValueError,match='size'): verify_manifest_tree(reference(raw),lambda ref:raw,max_bytes=25)


def test_manifest_tree_rejects_foreign_urls_and_bounds_indexes():
    from deployctl.multiservice_build import verify_manifest_tree
    body=json.loads(manifest()); body['layers'][0]['urls']=['https://unrelated.example/blob']
    raw=json.dumps(body).encode()
    with pytest.raises(ValueError,match='external'): verify_manifest_tree(reference(raw),lambda ref:raw)
    body={'schemaVersion':2,'mediaType':'application/vnd.oci.image.index.v1+json',
          'manifests':[{'digest':'sha256:'+'c'*64,'size':100}]*33}
    raw=json.dumps(body).encode()
    with pytest.raises(ValueError,match='manifest'): verify_manifest_tree(reference(raw),lambda ref:raw)


def test_mirror_preserves_digest_and_uses_separate_source_destination_auth(tmp_path):
    from deployctl.multiservice_build import ImageTools
    raw = manifest(); src = reference(raw); target='registry.example/project:release'
    calls=[]
    def run(args, **kwargs):
        calls.append(args)
        if 'copy' in args:
            Path(args[args.index('--digestfile')+1]).write_text(src.split('@')[1])
            return b''
        if '--raw' in args: return raw
        if '--config' in args: return b'{"os":"linux","architecture":"amd64"}'
        raise AssertionError(args)
    from pathlib import Path
    tool=ImageTools(tmp_path, tmp_path/'auth.json', runner=run)
    assert tool.mirror(src,target,'linux/amd64') == target.split(':')[0]+'@'+src.split('@')[1]
    copying=next(c for c in calls if 'copy' in c)
    assert '--all' in copying and '--preserve-digests' in copying
    assert '--src-authfile' in copying and '--dest-authfile' in copying
    assert copying[-2:] == ['docker://'+src,'docker://'+target]
    assert not any('--creds' in arg for c in calls for arg in c)


def test_mirror_refuses_wrong_architecture_before_copy(tmp_path):
    from deployctl.multiservice_build import ImageTools
    raw=manifest(); calls=[]
    def run(args, **kwargs):
        calls.append(args)
        return raw if '--raw' in args else b'{"os":"linux","architecture":"arm64"}'
    with pytest.raises(ValueError,match='platform'):
        ImageTools(tmp_path,tmp_path/'auth.json',runner=run).mirror(reference(raw),'registry.example/project:release','linux/amd64')
    assert not any('copy' in call for call in calls)


def test_workflow_preparation_accepts_multiservice_without_changing_legacy_inputs(tmp_path):
    import os,subprocess,sys,yaml
    from pathlib import Path
    (tmp_path/'deployment.yaml').write_text(yaml.safe_dump(source()))
    (tmp_path/'Dockerfile').write_text('FROM scratch\n')
    output=tmp_path/'outputs'
    env=dict(os.environ,DEPLOYMENT_FILE='deployment.yaml',RELEASE_VERSION='v1.0.0',REGISTRY='registry.example',
             IMAGE_NAME='team/project',GITHUB_OUTPUT=str(output),BUILD_ARGS='')
    script=Path(__file__).resolve().parents[1]/'scripts/prepare_build.py'
    result=subprocess.run([sys.executable,str(script)],cwd=tmp_path,env=env,text=True,capture_output=True)
    assert result.returncode == 0, result.stderr
    assert 'schema_version=2' in output.read_text()
    assert json.loads((tmp_path/'image-plan.json').read_text())['nodes']['api']['kind']=='build'


def test_delivery_builds_then_mirrors_and_reuses_identical_sources(tmp_path):
    from deployctl.multiservice_build import deliver_images
    (tmp_path/'Dockerfile').write_text('FROM scratch\n'); config=source()
    config['jobs']={'restore':{'image':IMAGE,'mirror':'project','trigger':'bootstrap','command':['restore']}}
    calls=[]
    class Tools:
        def build(self,spec,target,platform):
            calls.append(('build',spec)); return target.rsplit(':',1)[0]+'@sha256:'+'c'*64
        def mirror(self,source,target,platform):
            calls.append(('mirror',source)); return target.rsplit(':',1)[0]+'@'+source.split('@')[1]
    images=deliver_images(config,tmp_path,'registry.example/project','v1.0.0',Tools())
    assert [call[0] for call in calls] == ['build','mirror']
    assert images['restore'] == images['postgres']


def test_copy_digest_mismatch_is_not_published(tmp_path):
    from pathlib import Path
    from deployctl.multiservice_build import ImageTools
    raw=manifest()
    def run(args,**kwargs):
        if 'copy' in args:
            Path(args[args.index('--digestfile')+1]).write_text('sha256:'+'f'*64)
            return b''
        return raw if '--raw' in args else b'{"os":"linux","architecture":"amd64"}'
    with pytest.raises(ValueError,match='digest changed'):
        ImageTools(tmp_path,tmp_path/'auth',runner=run).mirror(reference(raw),'registry.example/project:release','linux/amd64')


def test_multi_build_args_override_rejected_before_workflow_outputs(tmp_path,monkeypatch):
    import yaml
    from scripts.prepare_build import main
    monkeypatch.chdir(tmp_path)
    (tmp_path/'deployment.yaml').write_text(yaml.safe_dump(source())); (tmp_path/'Dockerfile').write_text('FROM scratch\n')
    for key,value in {'DEPLOYMENT_FILE':'deployment.yaml','RELEASE_VERSION':'v1.0.0','REGISTRY':'registry.example',
                      'IMAGE_NAME':'project','GITHUB_OUTPUT':str(tmp_path/'outputs'),'BUILD_ARGS':'SECRET=unscoped'}.items():
        monkeypatch.setenv(key,value)
    assert main()==1
    assert not (tmp_path/'outputs').exists()


@pytest.mark.parametrize('fail',[False,True])
def test_workflow_entrypoint_publishes_only_complete_inventory(tmp_path,monkeypatch,fail):
    import types,yaml
    from scripts import build_multiservice as entry
    from deployctl.release import unpack_release
    monkeypatch.chdir(tmp_path)
    (tmp_path/'Dockerfile').write_text('FROM scratch\n')
    (tmp_path/'deployment.yaml').write_text(yaml.safe_dump(source()))
    for key,value in {'DEPLOYMENT_FILE':'deployment.yaml','RELEASE_VERSION':'v1.0.0',
                      'IMAGE_REPOSITORY':'registry.example/project','GITHUB_OUTPUT':str(tmp_path/'outputs')}.items():
        monkeypatch.setenv(key,value)
    class Tools:
        def build(self,*args): return 'registry.example/project@sha256:'+'c'*64
        def mirror(self,*args):
            if fail: raise RuntimeError('registry unavailable')
            return 'registry.example/project@sha256:'+'a'*64
    monkeypatch.setattr(entry,'ImageTools',lambda *args:Tools())
    monkeypatch.setattr(entry.subprocess,'run',lambda *args,**kwargs:types.SimpleNamespace(stdout='f'*40))
    assert entry.main() == (1 if fail else 0)
    if fail:
        assert not (tmp_path/'dist').exists() and not (tmp_path/'outputs').exists()
    else:
        images=json.loads((tmp_path/'outputs').read_text().split('=',1)[1])
        package=next((tmp_path/'dist').glob('*.tar.gz'))
        release=unpack_release(package,tmp_path/'unpack')
        assert set(images)=={'api','postgres'}
        assert release['deployment']['services']['api']['image']==images['api']
        assert release['commit']=='f'*40
