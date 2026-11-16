"""ctl owns service lifecycle; backup semantics remain in project programs."""
import pytest
from deployctl.cli import parser
from deployctl.stack_jobs import select_jobs
from test_stack_jobs import with_restore
from test_service_plan import values

@pytest.mark.parametrize('args',[
 ['restore','lifelog','--prod'],
 ['upgrade','lifelog','--prod','--job-source','restore='+'a'*64],
 ['maintain','lifelog','--prod','--service','postgres','--backup-sha256','a'*64],
])
def test_no_database_recovery_protocol_in_ctl_cli(args):
 with pytest.raises(SystemExit):parser().parse_args(args)

def test_project_job_does_not_need_ctl_backup_identity():
 v=values();v['secrets']['RESTORE_TOKEN']='project-input'
 result=select_jobs(with_restore(),{},v)
 assert result['active']==['restore']

def test_service_maintenance_has_no_business_backup_parameter():
 args=parser().parse_args(['maintain','lifelog','--prod','--service','postgres','--plan'])
 assert args.service==['postgres']


def test_volumeless_job_can_run_with_default_rollback_barrier():
 from test_stack_snapshot import full_release
 from test_multiservice_contract import IMAGE
 from deployctl.multiservice_release import validate_multiservice_release
 release=full_release()
 release['deployment']['jobs']={'notify':{'image':IMAGE,'trigger':'explicit','command':['notify']}}
 release=validate_multiservice_release(release)
 assert select_jobs(release,{},values(),['notify'])['active']==['notify']


def test_project_can_select_job_for_existing_service_resources():
 from test_stack_snapshot import full_release
 from test_multiservice_contract import IMAGE
 from deployctl.multiservice_release import validate_multiservice_release
 release=full_release()
 release['deployment']['jobs']={'migrate':{'image':IMAGE,'trigger':'explicit','command':['migrate'],'depends_on':{'postgres':'healthy'}}}
 release=validate_multiservice_release(release)
 state={'volumes':{'data':{'name':'already-managed-data','identity':'existing-volume'}}}
 assert select_jobs(release,state,values(),['migrate'])['active']==['migrate']
