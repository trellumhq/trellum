"""Resolve route fixture parameters inside the disposable shard only.

Credential values must not be serialized into the portable route manifest.
"""
from django.contrib.auth import get_user_model

SEED_PARAMETERS = {'connection_id','group_id','invite_id','link_id','proposal_id','report_id','rule_id','run_id','schedule_id','session_id','user_id','token'}
NATIVE_PARAMETERS = {'app_label','content_type_id','id','object_id','provider_id','token','uidb64','url'}

def resolve(provider, route):
    from apps.core.tests.portal_e2e.seed import seed_manifest
    namespace, name = provider.split('.', 1)
    if namespace == 'seed':
        if name not in SEED_PARAMETERS:
            raise ValueError('unknown seed parameter '+name)
        if name != 'token':
            if name=='run_id' and route['view'].endswith('from_alert'):
                from apps.alerts.models import AlertRun
                return AlertRun.objects.first().pk
            return seed_manifest()['providers'][name]
        if route['pattern'].startswith('invite/'):
            from apps.accounts.models import Invitation
            return Invitation.objects.first().token
        if route['pattern'].startswith('share/'):
            from apps.reports.models import ShareLink
            return ShareLink.objects.first().token
        if route['pattern'].startswith('notify/'):
            from apps.reports.notify import sign_unsubscribe_token
            from apps.reports.models import EmailSchedule
            schedule = EmailSchedule.objects.first()
            return sign_unsubscribe_token('schedule', schedule.pk, schedule.recipients.first().pk)
        raise ValueError('no token provider for '+route['pattern'])
    if namespace != 'native-boundary' or name not in NATIVE_PARAMETERS:
        raise ValueError('unknown native provider '+provider)
    from django.contrib.contenttypes.models import ContentType
    from django.contrib.auth.tokens import default_token_generator
    from django.utils.http import urlsafe_base64_encode
    from django.utils.encoding import force_bytes
    user = get_user_model().objects.get(email='operator@example.invalid')
    if name == 'object_id' and route['pattern'].startswith('admin/'):
        parts = route['pattern'].split('/')
        if parts[1] != 'r':
            from django.apps import apps
            model = apps.get_model(parts[1],parts[2])
            obj = model.objects.first()
            if obj is None:
                raise ValueError('missing native admin fixture for '+model._meta.label)
            return str(obj.pk)
    native = {'app_label':'accounts','content_type_id':ContentType.objects.get_for_model(user).pk,
              'id':user.pk,'object_id':str(user.pk),'provider_id':'portal-local-oidc',
              'uidb64':urlsafe_base64_encode(force_bytes(user.pk)),
              'token':default_token_generator.make_token(user),'url':'/account'}
    return native[name]


def url_for(route, role=None):
    import re
    def substitute(match):
        name = match.group(1)
        override = route.get('principal_parameter_overrides',{}).get(role,{})
        if name in override:
            return str(override[name])
        spec = route['seeded_parameters'][name]
        value = spec['value'] if 'value' in spec else resolve(spec['provider'],route)
        return str(value)
    if not route['first_party']:
        raise ValueError('native regex recipes must use the native protocol scenario')
    return '/' + re.sub(r'<(?:[^:>]+:)?([^>]+)>',substitute,route['pattern'])
