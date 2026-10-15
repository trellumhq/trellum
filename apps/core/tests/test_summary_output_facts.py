"""Explicit served-prefix facts never inspect private query bodies."""
import gzip
import json
from types import SimpleNamespace
import pytest
from apps.core import storage

pytestmark=pytest.mark.django_db


def test_local_entry_reserved_pages_and_symlinks(studio_tree,tmp_path):
    root=studio_tree.output_dir/'sales';root.mkdir(parents=True)
    (root/'_metrics-workspace-' ).write_text('unrelated')
    (root/('_metrics-workspace-'+'a'*32+'-page-1.html')).write_text('page')
    (root/'z.html').write_text('entry')
    outside=tmp_path/'outside.html';outside.write_text('outside')
    (root/'a.html').symlink_to(outside)
    facts=storage.summary_output_facts(studio_tree,'sales')
    assert facts['has_output'] and facts['html_entry']=='z.html'


def test_remote_only_served_pointer_names_and_private_head(studio,monkeypatch):
    prefix=f'{studio.org.slug}/{studio.slug}/sales/builds/001/'
    requests=[]
    class Client:
        def list_objects_v2(self,**kwargs):
            requests.append(('list',kwargs['Prefix']))
            assert kwargs['Prefix']==prefix and kwargs['MaxKeys']==128
            return {'Contents':[{'Key':prefix+'index.html'}],'IsTruncated':False}
        def head_object(self,**kwargs):
            requests.append(('head',kwargs['Key']))
            return {'ContentLength':99999999}
        def get_object(self,**kwargs):
            pytest.fail('private query contents must never be read')
    monkeypatch.setattr(storage,'is_remote',lambda:True)
    from apps.core import s3
    def short_client(**kwargs):
        assert kwargs=={'read_timeout':2}
        return Client()
    monkeypatch.setattr(s3,'s3_client',short_client)
    budget=storage.SummaryReadBudget(128*1024)
    facts=storage.summary_output_facts(studio,'sales',build='001',budget=budget)
    assert facts['html_entry']=='index.html' and facts['has_live_queries']
    assert all('002' not in key for _,key in requests)
    assert requests[1][1].startswith('_private/') and budget.used<128*1024


def test_remote_listing_page_cap(studio,monkeypatch):
    class Client:
        def list_objects_v2(self,**kwargs):
            return {'Contents':[],'IsTruncated':True,'NextContinuationToken':'more'}
    monkeypatch.setattr(storage,'is_remote',lambda:True)
    from apps.core import s3
    def short_client(**kwargs):
        assert kwargs=={'read_timeout':2}
        return Client()
    monkeypatch.setattr(s3,'s3_client',short_client)
    with pytest.raises(ValueError,match='oversized'):
        storage.summary_output_facts(studio,'sales',build='001')


@pytest.mark.parametrize('name',['../outside.json','nested/data.json','_live_queries.json','data.json/../x.json'])
def test_scope_filename_rejected(studio_tree,name):
    with pytest.raises(ValueError):
        storage.read_summary_artifact(studio_tree,'sales',name,max_bytes=1024,scope=True)


def test_gzip_dual_accounting_and_decoded_boundary(studio_tree):
    root=studio_tree.output_dir/'sales';root.mkdir(parents=True)
    encoded=gzip.compress(b'x'*1025)
    (root/'data.json').write_bytes(encoded)
    budget=storage.SummaryReadBudget(2048)
    with pytest.raises(ValueError,match='decoded byte limit'):
        storage.read_summary_artifact(studio_tree,'sales','data.json',max_bytes=1024,budget=budget)
    assert budget.encoded==len(encoded) and budget.decoded==1025 and budget.used==1025


def test_local_directory_and_symlink_entries_count_toward_cap(studio_tree):
    root=studio_tree.output_dir/'sales';root.mkdir(parents=True)
    for ordinal in range(257):
        (root/str(ordinal)).mkdir()
    with pytest.raises(ValueError,match='entry count oversized'):
        storage.summary_output_facts(studio_tree,'sales')
