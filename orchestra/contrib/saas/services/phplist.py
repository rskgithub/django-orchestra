from django import forms
from django.core.exceptions import ValidationError
from django.core.urlresolvers import reverse
from django.db.models import Q
from django.utils.safestring import mark_safe
from django.utils.translation import ugettext_lazy as _

from orchestra.contrib.databases.models import Database, DatabaseUser
from orchestra.contrib.mailboxes.models import Mailbox
from orchestra.forms.widgets import SpanWidget

from .. import settings
from ..forms import SaaSPasswordForm
from .options import SoftwareService


class PHPListForm(SaaSPasswordForm):
    admin_username = forms.CharField(label=_("Admin username"), required=False,
        widget=SpanWidget(display='admin'))
    database = forms.CharField(label=_("Database"), required=False,
        help_text=_("Database dedicated to this phpList instance."),
        widget=SpanWidget(display=settings.SAAS_PHPLIST_DB_NAME.replace(
            '%(', '&lt;').replace(')s', '&gt;')))
    mailbox = forms.CharField(label=_("Bounces mailbox"), required=False,
        help_text=_("Dedicated mailbox used for reciving bounces."),
        widget=SpanWidget(display=settings.SAAS_PHPLIST_BOUNCES_MAILBOX_NAME.replace(
            '%(', '&lt;').replace(')s', '&gt;')))
    
    def __init__(self, *args, **kwargs):
        super(PHPListForm, self).__init__(*args, **kwargs)
        self.fields['name'].label = _("Site name")
        context = {
            'site_name': '&lt;site_name&gt;',
            'name': '&lt;site_name&gt;',
        }
        domain = self.plugin.site_domain % context
        help_text = _("Admin URL http://{}/admin/").format(domain)
        self.fields['site_url'].help_text = help_text


class PHPListChangeForm(PHPListForm):
    def __init__(self, *args, **kwargs):
        super(PHPListChangeForm, self).__init__(*args, **kwargs)
        site_domain = self.instance.get_site_domain()
        admin_url = "http://%s/admin/" % site_domain
        help_text = _("Admin URL <a href={0}>{0}</a>").format(admin_url)
        self.fields['site_url'].help_text = help_text
        # DB link
        db = self.instance.database
        db_url = reverse('admin:databases_database_change', args=(db.pk,))
        db_link = mark_safe('<a href="%s">%s</a>' % (db_url, db.name))
        self.fields['database'].widget = SpanWidget(original=db.name, display=db_link)
        # Mailbox link
        mailbox_id = self.instance.data.get('mailbox_id')
        if mailbox_id:
            try:
                mailbox = Mailbox.objects.get(id=mailbox_id)
            except Mailbox.DoesNotExist:
                pass
            else:
                mailbox_url = reverse('admin:mailboxes_mailbox_change', args=(mailbox.pk,))
                mailbox_link = mark_safe('<a href="%s">%s</a>' % (mailbox_url, mailbox.name))
                self.fields['mailbox'].widget = SpanWidget(
                    original=mailbox.name, display=mailbox_link)


class PHPListService(SoftwareService):
    name = 'phplist'
    verbose_name = "phpList"
    form = PHPListForm
    change_form = PHPListChangeForm
    icon = 'orchestra/icons/apps/Phplist.png'
    site_domain = settings.SAAS_PHPLIST_DOMAIN
    
    def get_db_name(self):
        context = {
            'name': self.instance.name,
            'site_name': self.instance.name,
        }
        return settings.SAAS_PHPLIST_DB_NAME % context
        db_name = 'phplist_mu_%s' % self.instance.name
        # Limit for mysql database names
        return db_name[:65]
    
    def get_db_user(self):
        return settings.SAAS_PHPLIST_DB_USER
    
    def get_mailbox_name(self):
        context = {
            'name': self.instance.name,
            'site_name': self.instance.name,
        }
        return settings.SAAS_PHPLIST_BOUNCES_MAILBOX_NAME % context
    
    def get_account(self):
        account_model = self.instance._meta.get_field_by_name('account')[0]
        return account_model.rel.to.objects.get_main()
    
    def validate(self):
        super(PHPListService, self).validate()
        create = not self.instance.pk
        if create:
            account = self.get_account()
            # Validated Database
            db_user = self.get_db_user()
            try:
                DatabaseUser.objects.get(username=db_user)
            except DatabaseUser.DoesNotExist:
                raise ValidationError(
                    _("Global database user for PHPList '%(db_user)s' does not exists.") % {
                        'db_user': db_user
                    }
                )
            db = Database(name=self.get_db_name(), account=account)
            try:
                db.full_clean()
            except ValidationError as e:
                raise ValidationError({
                    'name': e.messages,
                })
            # Validate mailbox
            mailbox = Mailbox(name=self.get_mailbox_name(), account=account)
            try:
                mailbox.full_clean()
            except ValidationError as e:
                raise ValidationError({
                    'name': e.messages,
                })
    
    def save(self):
        account = self.get_account()
        # Database
        db_name = self.get_db_name()
        db_user = self.get_db_user()
        db, db_created = account.databases.get_or_create(name=db_name, type=Database.MYSQL)
        user = DatabaseUser.objects.get(username=db_user)
        db.users.add(user)
        self.instance.database_id = db.pk
        # Mailbox
        mailbox_name = self.get_mailbox_name()
        mailbox, mb_created = account.mailboxes.get_or_create(name=mailbox_name)
        if mb_created:
            mailbox.set_password(settings.SAAS_PHPLIST_BOUNCES_MAILBOX_PASSWORD)
            mailbox.save(update_fields=('password',))
            self.instance.data.update({
                'mailbox_id': mailbox.pk,
                'mailbox_name': mailbox_name,
            })
    
    def delete(self):
        account = self.get_account()
        # delete Mailbox (database will be deleted by ORM's cascade behaviour
        mailbox_name = self.instance.data.get('mailbox_name') or self.get_mailbox_name()
        mailbox_id = self.instance.data.get('mailbox_id')
        qs = Q(Q(name=mailbox_name) | Q(id=mailbox_id))
        for mailbox in account.mailboxes.filter(qs):
            mailbox.delete()
