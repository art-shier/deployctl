"""Real Go/PostgreSQL/Python update protocol; host mutations are simulated."""
import json
from pathlib import Path
import sys
import tempfile
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deployctl.platform_client import PlatformClient, PlatformError
from deployctl.platform_credentials import Credentials
from deployctl.updater import Updater


class HostFixture:
    def __init__(self):
        self.version = 'v1.15.0'
        self.installs = 0
        self.restores = 0
        self.bad = set()

    def readiness(self): return self.version, True, ''
    def prepare(self, job, directory): pass
    def backup(self, job, directory): pass
    def install(self, job, directory, stage):
        self.installs += 1
        stage('restarting')
        self.version = job['version']
    def verify(self, version, directory): return self.version == version and version not in self.bad
    def restore(self, job, directory, stage):
        self.restores += 1
        self.version = job['from_version']
    def reconcile(self, job, directory): return self.verify(job['version'], directory)


class DropResponse:
    def __init__(self, client, suffix, terminal_only=False):
        self.client, self.suffix, self.terminal_only = client, suffix, terminal_only
        self.dropped = False

    def json(self, method, path, body=None):
        response = self.client.json(method, path, body)
        if (not self.dropped and path.endswith(self.suffix)
                and (not self.terminal_only or body.get('status') == 'succeeded')):
            self.dropped = True
            raise RuntimeError('fixture dropped acknowledged response')
        return response


def denied(client, method, path, body=None, statuses=(401,403)):
    try: client.json(method, path, body)
    except PlatformError as error:
        assert error.status in statuses, error.status
    else: raise AssertionError('identity/operation unexpectedly accepted')


def main():
    credentials = json.load(sys.stdin)
    owner = PlatformClient(Credentials(credentials['origin'], credentials['token']))
    node_invitation = owner.json('POST','/api/v1/node-enrollments',{})
    node_invitee = PlatformClient(Credentials(credentials['origin'],node_invitation['enrollment_token']))
    node = node_invitee.json('POST','/api/v1/node/enroll',{'name':'updater-fixture','agent_version':'1.15.0','platform':'linux'})
    node_id = node['node_id']
    owner.json('PATCH',f'/api/v1/nodes/{node_id}',{'name':'updater-fixture','enabled':True,'groups':['default'],'projects':[],'excluded_projects':[],'environments':['prod']})

    def register(kind,target):
        if kind=='node':
            node_client=PlatformClient(Credentials(credentials['origin'],node['token']))
            denied(node_client,'POST','/api/v1/node/updater-enrollment',{'kind':'center','target_id':'center'},statuses=(400,))
            invitation=node_client.json('POST','/api/v1/node/updater-enrollment',{})
        else:
            invitation=owner.json('POST','/api/v1/system-updates/enrollments',{'kind':kind,'target_id':target})
        invitee=PlatformClient(Credentials(credentials['origin'],invitation['enrollment_token']))
        payload={'helper_version':'v1.15.0','protocol':1,'platform':'linux'}
        identity=invitee.json('POST','/api/v1/updater/enroll',payload)
        denied(invitee,'POST','/api/v1/updater/enroll',payload,statuses=(401,))
        client=PlatformClient(Credentials(credentials['origin'],identity['token']))
        denied(client,'GET','/api/v1/system-updates')
        denied(client,'POST','/api/v1/node/poll',{})
        return identity,client

    def submit(identity,version,backup=False):
        body={'request_id':uuid.uuid4().hex,'updater_ids':[identity['updater_id']], 'version':version,'parallelism':1,'backup_confirmed':backup}
        first=owner.json('POST','/api/v1/system-updates/jobs',body)
        second=owner.json('POST','/api/v1/system-updates/jobs',body)
        assert len(first)==1 and second[0]['id']==first[0]['id']
        return first[0]

    def listed(job):
        return next(item for item in owner.json('GET','/api/v1/system-updates')['jobs'] if item['id']==job['id'])

    with tempfile.TemporaryDirectory() as temp:
        home=Path(temp)
        identity,client=register('node',node_id)
        host=HostFixture()
        worker=Updater(home/'node',identity,client,host)
        worker.run_once()
        job=submit(identity,'v1.16.0')
        worker.run_once()
        assert listed(job)['status']=='succeeded' and host.installs==1
        worker.run_once()
        assert host.installs==1
        host.bad.add('v1.17.0')
        job=submit(identity,'v1.17.0')
        worker.run_once()
        assert listed(job)['status']=='rolled_back' and host.version=='v1.16.0' and host.restores==1
        job=submit(identity,'v1.17.0')
        owner.json('POST',f"/api/v1/system-updates/jobs/{job['id']}/cancel",{})
        worker.run_once()
        assert host.installs==2 and listed(job)['status']=='cancelled'

        center,center_client=register('center','center')
        center_host=HostFixture()
        center_worker=Updater(home/'center',center,center_client,center_host)
        center_worker.run_once()
        job=submit(center,'v1.16.0',True)
        center_worker.client=DropResponse(center_client,'/start')
        center_worker.run_once()
        assert listed(job)['status']=='needs_attention' and center_host.installs==0
        center_worker.client=center_client
        center_worker.run_once()  # fresh ready evidence after terminal report
        recovered=owner.json('POST',f"/api/v1/system-updates/jobs/{job['id']}/resolve",{})
        assert recovered['resolved_at']
        job=submit(center,'v1.16.0',True)
        center_worker.client=DropResponse(center_client,'/report',terminal_only=True)
        center_worker.run_once()
        assert center_host.installs==1
        # The center returned after replacement but response was lost: restart
        # must replay the terminal report, never the installation.
        restarted=Updater(home/'center',center,center_client,center_host)
        restarted.run_once()
        assert listed(job)['status']=='succeeded' and center_host.installs==1
        denied(client,'POST',f"/api/v1/updater/jobs/{job['id']}/start",{},statuses=(403,404))
    print('PASS: real updater identity, discovery, pinned jobs, retries, rollback, cancel, ambiguous start recovery and center report replay; host/systemd operations simulated')


if __name__=='__main__': main()
