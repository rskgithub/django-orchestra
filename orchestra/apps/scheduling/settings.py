from django.conf import settings

ugettext = lambda s: s

# Models that wants to keep contraced
SCHEDULING_SCHEDULABLE_MODELS = getattr(settings, 'SCHEDULING_SCHEDULABLE_MODELS', (
    'django.contrib.auth.models.User',
    'dns.names.models.Name', 'dns.zones.models.Zone',
    'system_users.models.SystemGroup', 
    'web.models.VirtualHost',
    'mail.models.VirtualAliase',
    'lists.models.List',
    'vps.models.VPS',
    'tasks.models.Task',
    'databases.models.Database', 'databases.models.DBUser',
))
