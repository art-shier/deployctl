import pytest
from deployctl.cli import parser
from deployctl.application_logs import query_parameters

def test_app_log_filters_and_legacy_compatibility():
    a=parser().parse_args(['logs','notes','--source','app','--env','prod','--user-id','u','--since','30m','--json'])
    q=query_parameters(a)
    assert q['user_id']=='u' and q['limit']==100 and q['since'].endswith('+00:00')
    legacy=parser().parse_args(['logs','notes'])
    assert legacy.source=='runtime'

def test_invalid_app_log_query():
    a=parser().parse_args(['logs','notes','--source','app','--since','yesterday'])
    with pytest.raises(ValueError): query_parameters(a)
