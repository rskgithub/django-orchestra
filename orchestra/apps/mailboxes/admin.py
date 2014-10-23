import copy
from urlparse import parse_qs

from django import forms
from django.contrib import admin
from django.core.urlresolvers import reverse
from django.utils.safestring import mark_safe
from django.utils.translation import ugettext_lazy as _

from orchestra.admin import ExtendedModelAdmin, ChangePasswordAdminMixin
from orchestra.admin.utils import admin_link, change_url
from orchestra.apps.accounts.admin import SelectAccountAdminMixin, AccountAdminMixin

from .filters import HasMailboxListFilter, HasForwardListFilter, HasAddressListFilter
from .forms import MailboxCreationForm, MailboxChangeForm, AddressForm
from .models import Mailbox, Address, Autoresponse


class AutoresponseInline(admin.StackedInline):
    model = Autoresponse
    verbose_name_plural = _("autoresponse")
    
    def formfield_for_dbfield(self, db_field, **kwargs):
        if db_field.name == 'subject':
            kwargs['widget'] = forms.TextInput(attrs={'size':'118'})
        return super(AutoresponseInline, self).formfield_for_dbfield(db_field, **kwargs)


class MailboxAdmin(ChangePasswordAdminMixin, SelectAccountAdminMixin, ExtendedModelAdmin):
    list_display = (
        'name', 'account_link', 'filtering', 'display_addresses'
    )
    list_filter = (HasAddressListFilter, 'filtering')
    add_fieldsets = (
        (None, {
            'fields': ('account_link', 'name', 'password1', 'password2', 'filtering'),
        }),
        (_("Custom filtering"), {
            'classes': ('collapse',),
            'fields': ('custom_filtering',),
        }),
        (_("Addresses"), {
            'fields': ('addresses',)
        }),
    )
    fieldsets = (
        (None, {
            'fields': ('name', 'password', 'is_active', 'account_link', 'filtering'),
        }),
        (_("Custom filtering"), {
            'classes': ('collapse',),
            'fields': ('custom_filtering',),
        }),
        (_("Addresses"), {
            'fields': ('addresses',)
        }),
    )
    readonly_fields = ('account_link', 'display_addresses')
    change_readonly_fields = ('name',)
    add_form = MailboxCreationForm
    form = MailboxChangeForm
    
    def display_addresses(self, mailbox):
        addresses = []
        for addr in mailbox.addresses.all():
            url = change_url(addr)
            addresses.append('<a href="%s">%s</a>' % (url, addr.email))
        return '<br>'.join(addresses)
    display_addresses.short_description = _("Addresses")
    display_addresses.allow_tags = True
    
    def get_fieldsets(self, request, obj=None):
        fieldsets = super(MailboxAdmin, self).get_fieldsets(request, obj=obj)
        if obj and obj.filtering == obj.CUSTOM:
            # not collapsed filtering when exists
            fieldsets = copy.deepcopy(fieldsets)
            fieldsets[1][1]['classes'] = fieldsets[0][1]['fields'] + ('open',)
        elif '_to_field' in parse_qs(request.META['QUERY_STRING']):
            # remove address from popup
            fieldsets = list(copy.deepcopy(fieldsets))
            fieldsets.pop(-1)
        return fieldsets
    
    def get_form(self, *args, **kwargs):
        form = super(MailboxAdmin, self).get_form(*args, **kwargs)
        form.modeladmin = self
        return form
    
    def save_model(self, request, obj, form, change):
        """ save hacky mailbox.addresses """
        super(MailboxAdmin, self).save_model(request, obj, form, change)
        obj.addresses = form.cleaned_data['addresses']


class AddressAdmin(SelectAccountAdminMixin, ExtendedModelAdmin):
    list_display = (
        'email', 'domain_link', 'display_mailboxes', 'display_forward', 'account_link'
    )
    list_filter = (HasMailboxListFilter, HasForwardListFilter)
    fields = ('account_link', ('name', 'domain'), 'mailboxes', 'forward')
    inlines = [AutoresponseInline]
    search_fields = ('name', 'domain__name',)
    readonly_fields = ('account_link', 'domain_link', 'email_link')
    filter_by_account_fields = ('domain', 'mailboxes')
    filter_horizontal = ['mailboxes']
    form = AddressForm
    
    domain_link = admin_link('domain', order='domain__name')
    
    def email_link(self, address):
        link = self.domain_link(address)
        return "%s@%s" % (address.name, link)
    email_link.short_description = _("Email")
    email_link.allow_tags = True
    
    def display_mailboxes(self, address):
        boxes = []
        for mailbox in address.mailboxes.all():
            url = change_url(mailbox)
            boxes.append('<a href="%s">%s</a>' % (url, mailbox.name))
        return '<br>'.join(boxes)
    display_mailboxes.short_description = _("Mailboxes")
    display_mailboxes.allow_tags = True
    
    def display_forward(self, address):
        values = [ dest for dest in address.forward.split() ]
        return '<br>'.join(values)
    display_forward.short_description = _("Forward")
    display_forward.allow_tags = True
    
    def formfield_for_dbfield(self, db_field, **kwargs):
        if db_field.name == 'forward':
            kwargs['widget'] = forms.TextInput(attrs={'size':'118'})
        return super(AddressAdmin, self).formfield_for_dbfield(db_field, **kwargs)
    
    def get_queryset(self, request):
        """ Select related for performance """
        qs = super(AddressAdmin, self).get_queryset(request)
        return qs.select_related('domain')
    
    def get_fields(self, request, obj=None):
        """ Remove mailboxes field when creating address from a popup i.e. from mailbox add form """
        fields = super(AddressAdmin, self).get_fields(request, obj=obj)
        if '_to_field' in parse_qs(request.META['QUERY_STRING']):
            # Add address popup
            fields = list(fields)
            fields.remove('mailboxes')
        return fields


admin.site.register(Mailbox, MailboxAdmin)
admin.site.register(Address, AddressAdmin)