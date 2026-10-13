import pytest

from apps.core.actions import action_payload


@pytest.mark.parametrize('state', ['accepted', 'running', 'succeeded', 'failed', 'unconfirmed'])
def test_observation_preserves_identity_without_domain_outcome(state):
    result = action_payload(action_id='scan:42', state=state, message='Observed',
                            status_url='/status/42?revision=3', revision='3')
    assert result == {'action': {'version': 1, 'id': 'scan:42', 'state': state,
                                'message': 'Observed', 'status_url': '/status/42?revision=3',
                                'revision': '3'}}


@pytest.mark.parametrize('field,value', [
    ('action_id', None), ('action_id', 1), ('action_id', ''), ('action_id', 'x' * 129),
    ('state', 'queued'), ('state', []), ('message', ''), ('message', 'x' * 1001),
    ('message', 'unsafe\nmessage'), ('revision', 1), ('revision', 'x' * 257),
    ('status_url', '//foreign.test/status'), ('status_url', 'https://local.test/status'),
    ('status_url', '/\\foreign.test/status'), ('status_url', '/status#fragment'),
    ('status_url', 'status'), ('status_url', '/status\nheader'),
])
def test_invalid_observations_are_rejected(field, value):
    values = dict(action_id='42', state='running', message='Observed')
    values[field] = value
    with pytest.raises(ValueError):
        action_payload(**values)


def test_completed_observation_can_have_no_poll_or_revision():
    assert action_payload(action_id='42', state='failed', message='Stopped')['action']['status_url'] is None
