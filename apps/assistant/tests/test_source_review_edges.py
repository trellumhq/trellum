import json
import pytest
from apps.assistant import source_queries, source_tools
from apps.assistant.models import SourceQuery
from apps.assistant.tests.test_source_access import source_context
from apps.assistant.tests.conftest import assistant_config, make_assistant_config
from apps.assistant.tests.test_source_queries import executor
from apps.assistant.tests.test_message_sse import fake_llm, FakeResponse, ToolUseBlock

pytestmark = pytest.mark.django_db(transaction=True)


def test_delete_running_session_retains_capacity_until_executor_stops(source_context, executor, settings):
    studio, user, session, grant = source_context
    settings.TRELLUM_SOURCE_QUERY_ORG_PENDING_LIMIT = 1
    job, _ = source_queries.admit(studio=studio, actor=user, session=session, grant_id=grant.pk,
        sql='SELECT count(*) FROM analytics.sales', idempotency_key='delete-running')
    def running(*args, **kwargs):
        kwargs['on_started'](12345)
        session.delete()
        assert not SourceQuery.objects.filter(pk=job.pk).exists()
        assert source_queries._orphan_reservations(source_queries.timezone.now()).count() == 1
        from apps.assistant.models import AssistantSession
        replacement = AssistantSession.objects.create(studio=studio, org=studio.org, user=user)
        with pytest.raises(source_queries.PendingLimitReached):
            source_queries.admit(studio=studio, actor=user, session=replacement, grant_id=grant.pk,
                                 sql='SELECT count(*) FROM analytics.sales', idempotency_key='replacement')
        return executor.describe_source.return_value
    executor.execute_query.side_effect = running
    source_queries.process_next(worker_id='synthetic-review')
    assert source_queries._orphan_reservations(source_queries.timezone.now()).count() == 0


def test_disconnect_at_tool_result_preserves_pending_resume(source_context, executor, fake_llm, login):
    studio, user, session, grant = source_context
    fake_llm(FakeResponse([ToolUseBlock('edge1', 'query_source', {
        'source_id': grant.pk, 'sql': 'SELECT count(*) FROM analytics.sales'})], 'tool_use'))
    response = login(user).post(f'/s/{studio.org.slug}/{studio.slug}/api/assistant/sessions/{session.pk}/message',
        data=json.dumps({'message':'Count sales'}), content_type='application/json')
    stream = iter(response.streaming_content)
    for chunk in stream:
        if b'event: tool_result\n' in chunk:
            break
    response.close()
    session.refresh_from_db()
    assert SourceQuery.objects.filter(session=session, state=SourceQuery.ACCEPTED).count() == 1
    assert session.state['_source_pending']['id'] == str(SourceQuery.objects.get(session=session).pk)
    assert not session.state.get('_continuation_error')


def test_revoked_evidence_is_withheld_from_personal_export(source_context, executor, login):
    from django.core.exceptions import PermissionDenied
    from apps.assistant.tools import AssistantToolbox
    from apps.orgs import personal_data
    studio, user, session, grant = source_context
    box = AssistantToolbox(studio, actor=user, session=session, role='org_admin')
    payload = json.loads(source_tools.execute(box, 'query_source', {
        'source_id': grant.pk, 'sql':'SELECT count(*) FROM analytics.sales'}))
    session.state['_source_pending'] = {'id': payload['id'], 'tool_use_id':'export1'}
    session.transcript.append({'role':'tool', 'tool_use_id':'export1', 'result':'pending'})
    source_queries.process_next(worker_id='synthetic-review')
    source_tools.resume(session, actor=user, studio=studio, query_id=payload['id'])
    session.save()
    assert personal_data.export(user, studio.org, actor=user)['activity']['assistant_conversations'][0]['transcript'][0]['provenance']['rows'] == [[42]]
    assert personal_data.export(user, studio.org)['activity']['assistant_conversations'][0]['transcript'] == []
    grant.users.clear()
    with pytest.raises(PermissionDenied):
        source_tools.check_history(session, actor=user, studio=studio)
    base=f'/s/{studio.org.slug}/{studio.slug}/api/assistant/sessions/{session.pk}'
    assert login(user).get(base).status_code == 403
    exported = personal_data.export(user, studio.org, actor=user)
    assert exported['activity']['assistant_conversations'][0]['transcript'] == []
