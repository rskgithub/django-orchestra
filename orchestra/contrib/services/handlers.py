import calendar
import datetime
import decimal
import math

from dateutil import relativedelta
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.utils import timezone, translation
from django.utils.translation import ugettext, ugettext_lazy as _

from orchestra import plugins
from orchestra.utils.humanize import text2int
from orchestra.utils.python import AttrDict, cmp_to_key

from . import settings, helpers


class ServiceHandler(plugins.Plugin, metaclass=plugins.PluginMount):
    """
    Separates all the logic of billing handling from the model allowing to better
    customize its behaviout
    
    Relax and enjoy the journey.
    """
    _VOLUME = 'VOLUME'
    _COMPENSATION = 'COMPENSATION'
    
    model = None
    
    def __init__(self, service):
        self.service = service
    
    def __getattr__(self, attr):
        return getattr(self.service, attr)
    
    @classmethod
    def get_choices(cls):
        choices = super(ServiceHandler, cls).get_choices()
        return [('', _("Default"))] + choices
    
    def validate_content_type(self, service):
        pass
    
    def validate_match(self, service):
        if not service.match:
            service.match = 'True'
        try:
            obj = service.content_type.model_class().objects.all()[0]
        except IndexError:
            return
        try:
            bool(self.matches(obj))
        except Exception as exception:
            name = type(exception).__name__
            raise ValidationError(': '.join((name, str(exception))))
    
    def validate_metric(self, service):
        try:
            obj = service.content_type.model_class().objects.all()[0]
        except IndexError:
            return
        try:
            bool(self.get_metric(obj))
        except Exception as exception:
            name = type(exception).__name__
            raise ValidationError(': '.join((name, str(exception))))
    
    def get_content_type(self):
        if not self.model:
            return self.content_type
        app_label, model = self.model.split('.')
        return ContentType.objects.get_by_natural_key(app_label, model.lower())
    
    def matches(self, instance):
        if not self.match:
            # Blank expressions always evaluate True
            return True
        safe_locals = {
            'instance': instance,
            'obj': instance,
            instance._meta.model_name: instance,
        }
        return eval(self.match, safe_locals)
    
    def get_ignore_delta(self):
        if self.ignore_period == self.NEVER:
            return None
        value, unit = self.ignore_period.split('_')
        value = text2int(value)
        if unit.lower().startswith('day'):
            return datetime.timedelta(days=value)
        if unit.lower().startswith('month'):
            return datetime.timedelta(months=value)
        else:
            raise ValueError("Unknown unit %s" % unit)
    
    def get_order_ignore(self, order):
        """ service trial delta """
        ignore_delta = self.get_ignore_delta()
        if ignore_delta and (order.cancelled_on-ignore_delta).date() <= order.registered_on:
            return True
        return order.ignore
    
    def get_ignore(self, instance):
        if self.ignore_superusers:
            account = getattr(instance, 'account', instance)
            if account.type in settings.SERVICES_IGNORE_ACCOUNT_TYPE:
                return True
            if 'superuser' in settings.SERVICES_IGNORE_ACCOUNT_TYPE and account.is_superuser:
                return True
        return False
    
    def get_metric(self, instance):
        if self.metric:
            safe_locals = {
                instance._meta.model_name: instance,
                'instance': instance,
                'math': math,
                'logsteps': lambda n, size=1: \
                    round(n/(decimal.Decimal(size*10**int(math.log10(max(n, 1))))))*size*10**int(math.log10(max(n, 1))),
                'log10': math.log10,
                'Decimal': decimal.Decimal,
            }
            try:
                return eval(self.metric, safe_locals)
            except Exception as error:
                raise type(error)("%s on '%s'" %(error, self.service))
    
    def get_order_description(self, instance):
        safe_locals = {
            'instance': instance,
            'obj': instance,
            'ugettext': ugettext,
            instance._meta.model_name: instance,
        }
        account = getattr(instance, 'account', instance)
        with translation.override(account.language):
            if not self.order_description:
                return '%s: %s' % (ugettext(self.description), instance)
            return eval(self.order_description, safe_locals)
    
    def get_billing_point(self, order, bp=None, **options):
        not_cachable = self.billing_point == self.FIXED_DATE and options.get('fixed_point')
        if not_cachable or bp is None:
            bp = options.get('billing_point', timezone.now().date())
            if not options.get('fixed_point'):
                msg = ("Support for '%s' period and '%s' point is not implemented"
                    % (self.get_billing_period_display(), self.get_billing_point_display()))
                if self.billing_period == self.MONTHLY:
                    date = bp
                    if self.payment_style == self.PREPAY:
                        date += relativedelta.relativedelta(months=1)
                    else:
                        date = timezone.now().date()
                    if self.billing_point == self.ON_REGISTER:
                        day = order.registered_on.day
                    elif self.billing_point == self.FIXED_DATE:
                        day = 1
                    else:
                        raise NotImplementedError(msg)
                    bp = datetime.date(year=date.year, month=date.month, day=day)
                elif self.billing_period == self.ANUAL:
                    if self.billing_point == self.ON_REGISTER:
                        month = order.registered_on.month
                        day = order.registered_on.day
                    elif self.billing_point == self.FIXED_DATE:
                        month = settings.SERVICES_SERVICE_ANUAL_BILLING_MONTH
                        day = 1
                    else:
                        raise NotImplementedError(msg)
                    year = bp.year
                    if self.payment_style == self.POSTPAY:
                        year = bp.year - relativedelta.relativedelta(years=1)
                    if bp.month >= month:
                        year = bp.year + 1
                    bp = datetime.date(year=year, month=month, day=day)
                elif self.billing_period == self.NEVER:
                    bp = order.registered_on
                else:
                    raise NotImplementedError(msg)
        if self.on_cancel != self.NOTHING and order.cancelled_on and order.cancelled_on < bp:
            bp = order.cancelled_on
        return bp
    
#    def aligned(self, date):
#        if self.granularity == self.DAILY:
#            return date
#        elif self.granularity == self.MONTHLY:
#            return datetime.date(year=date.year, month=date.month, day=1)
#        elif self.granularity == self.ANUAL:
#            return datetime.date(year=date.year, month=1, day=1)
#        raise NotImplementedError
    
    def get_price_size(self, ini, end):
        rdelta = relativedelta.relativedelta(end, ini)
        if self.billing_period == self.MONTHLY:
            size = rdelta.years * 12
            size += rdelta.months
            days = calendar.monthrange(end.year, end.month)[1]
            size += decimal.Decimal(str(rdelta.days))/days
        elif self.billing_period == self.ANUAL:
            size = rdelta.years
            size += decimal.Decimal(str(rdelta.months))/12
            days = 366 if calendar.isleap(end.year) else 365
            size += decimal.Decimal(str(rdelta.days))/days
        elif self.billing_period == self.NEVER:
            size = 1
        else:
            raise NotImplementedError
        return decimal.Decimal(str(size))
    
    def get_pricing_slots(self, ini, end):
        day = 1
        month = settings.SERVICES_SERVICE_ANUAL_BILLING_MONTH
        if self.billing_point == self.ON_REGISTER:
            day = ini.day
            month = ini.month
        period = self.get_pricing_period()
        rdelta = self.get_pricing_rdelta()
        if period == self.MONTHLY:
            ini = datetime.date(year=ini.year, month=ini.month, day=day)
        elif period == self.ANUAL:
            ini = datetime.date(year=ini.year, month=month, day=day)
        elif period == self.NEVER:
            yield ini, end
            raise StopIteration
        else:
            raise NotImplementedError
        while True:
            next = ini + rdelta
            yield ini, next
            if next >= end:
                break
            ini = next
    
    def get_pricing_rdelta(self):
        period = self.get_pricing_period()
        if period == self.MONTHLY:
            return relativedelta.relativedelta(months=1)
        elif period == self.ANUAL:
            return relativedelta.relativedelta(years=1)
        elif period == self.NEVER:
            return None
    
    def generate_discount(self, line, dtype, price):
        line.discounts.append(AttrDict(**{
            'type': dtype,
            'total': price,
        }))
    
    def generate_line(self, order, price, *dates, **kwargs):
        if len(dates) == 2:
            ini, end = dates
        elif len(dates) == 1:
            ini, end = dates[0], dates[0]
        else:
            raise AttributeError
        metric = kwargs.pop('metric', 1)
        discounts = kwargs.pop('discounts', ())
        computed = kwargs.pop('computed', False)
        if kwargs:
            raise AttributeError
        
        size = self.get_price_size(ini, end)
        if not computed:
            price = price * size
        subtotal = self.nominal_price * size * metric
        line = AttrDict(**{
            'order': order,
            'subtotal': subtotal,
            'ini': ini,
            'end': end,
            'size': size,
            'metric': metric,
            'discounts': [],
        })
        discounted = 0
        for dtype, dprice in discounts:
            self.generate_discount(line, dtype, dprice)
            discounted += dprice
        subtotal += discounted
        if subtotal > price:
            self.generate_discount(line, self._VOLUME, price-subtotal)
        return line
    
    def assign_compensations(self, givers, receivers, **options):
        compensations = []
        for order in givers:
            if order.billed_until and order.cancelled_on and order.cancelled_on < order.billed_until:
                interval = helpers.Interval(order.cancelled_on, order.billed_until, order)
                compensations.append(interval)
        for order in receivers:
            if not order.billed_until or order.billed_until < order.new_billed_until:
                # receiver
                ini = order.billed_until or order.registered_on
                end = order.cancelled_on or datetime.date.max
                interval = helpers.Interval(ini, end)
                compensations, used_compensations = helpers.compensate(interval, compensations)
                order._compensations = used_compensations
                for comp in used_compensations:
                    comp.order.new_billed_until = min(comp.order.billed_until, comp.ini,
                            getattr(comp.order, 'new_billed_until', datetime.date.max))
        if options.get('commit', True):
            for order in givers:
                if hasattr(order, 'new_billed_until'):
                    order.billed_until = order.new_billed_until
                    order.save(update_fields=['billed_until'])
    
    def apply_compensations(self, order, only_beyond=False):
        dsize = 0
        ini = order.billed_until or order.registered_on
        end = order.new_billed_until
        beyond = end
        cend = None
        for comp in getattr(order, '_compensations', []):
            intersect = comp.intersect(helpers.Interval(ini=ini, end=end))
            if intersect:
                cini, cend = intersect.ini, intersect.end
                if comp.end > beyond:
                    cend = comp.end
                    if only_beyond:
                        cini = beyond
                elif not only_beyond:
                    continue
                dsize += self.get_price_size(cini, cend)
            # Extend billing point a little bit to benefit from a substantial discount
            elif comp.end > beyond and (comp.end-comp.ini).days > 3*(comp.ini-beyond).days:
                cend = comp.end
                dsize += self.get_price_size(comp.ini, cend)
        return dsize, cend
    
    def get_register_or_renew_events(self, porders, ini, end):
        counter = 0
        for order in porders:
            bu = getattr(order, 'new_billed_until', order.billed_until)
            if bu:
                registered = order.registered_on
                if registered > ini and registered <= end:
                    counter += 1
                if registered != bu and bu > ini and bu <= end:
                    counter += 1
                if order.billed_until and order.billed_until != bu:
                    if registered != order.billed_until and order.billed_until > ini and order.billed_until <= end:
                        counter += 1
        return counter
    
    def bill_concurrent_orders(self, account, porders, rates, ini, end):
        # Concurrent
        # Get pricing orders
        priced = {}
        for ini, end, orders in helpers.get_chunks(porders, ini, end):
            size = self.get_price_size(ini, end)
            metric = len(orders)
            interval = helpers.Interval(ini=ini, end=end)
            for position, order in enumerate(orders, start=1):
                csize = 0
                compensations = getattr(order, '_compensations', [])
                # Compensations < new_billed_until
                for comp in compensations:
                    intersect = comp.intersect(interval)
                    if intersect:
                        csize += self.get_price_size(intersect.ini, intersect.end)
                price = self.get_price(account, metric, position=position, rates=rates)
                price = price * size
                cprice = price * csize
                if order in priced:
                    priced[order][0] += price
                    priced[order][1] += cprice
                else:
                    priced[order] = (price, cprice)
        lines = []
        for order, prices in priced.items():
            discounts = ()
            # Generate lines and discounts from order.nominal_price
            price, cprice = prices
            # Compensations > new_billed_until
            dsize, new_end = self.apply_compensations(order, only_beyond=True)
            cprice += dsize*price
            if cprice:
                discounts = (
                    (self._COMPENSATION, -cprice),
                )
                if new_end:
                    size = self.get_price_size(order.new_billed_until, new_end)
                    price += price*size
                    order.new_billed_until = new_end
            line = self.generate_line(order, price, ini, new_end or end, discounts=discounts, computed=True)
            lines.append(line)
        return lines
    
    def bill_registered_or_renew_events(self, account, porders, rates):
        # Before registration
        lines = []
        rdelta = self.get_pricing_rdelta()
        if not rdelta:
            raise NotImplementedError
        for position, order in enumerate(porders, start=1):
            if hasattr(order, 'new_billed_until'):
                pend = order.billed_until or order.registered_on
                pini = pend - rdelta
                metric = self.get_register_or_renew_events(porders, pini, pend)
                position = min(position, metric)
                price = self.get_price(account, metric, position=position, rates=rates)
                ini = order.billed_until or order.registered_on
                end = order.new_billed_until
                discounts = ()
                dsize, new_end = self.apply_compensations(order)
                if dsize:
                    discounts=(
                        (self._COMPENSATION, -dsize*price),
                    )
                    if new_end:
                        order.new_billed_until = new_end
                        end = new_end
                line = self.generate_line(order, price, ini, end, discounts=discounts)
                lines.append(line)
        return lines
    
    def bill_with_orders(self, orders, account, **options):
        # For the "boundary conditions" just think that:
        #   date(2011, 1, 1) is equivalent to datetime(2011, 1, 1, 0, 0, 0)
        #   In most cases:
        #       ini >= registered_date, end < registered_date
        # boundary lookup and exclude cancelled and billed
        orders_ = []
        bp = None
        ini = datetime.date.max
        end = datetime.date.min
        for order in orders:
            cini = order.registered_on
            if order.billed_until:
                # exclude cancelled and billed
                if self.on_cancel != self.REFUND:
                    if order.cancelled_on and order.billed_until > order.cancelled_on:
                        continue
                cini = order.billed_until
            bp = self.get_billing_point(order, bp=bp, **options)
            order.new_billed_until = bp
            ini = min(ini, cini)
            end = max(end, bp)
            orders_.append(order)
        orders = orders_
        
        # Compensation
        related_orders = account.orders.filter(service=self.service)
        if self.payment_style == self.PREPAY and self.on_cancel == self.COMPENSATE:
            # Get orders pending for compensation
            givers = list(related_orders.givers(ini, end))
            givers = sorted(givers, key=cmp_to_key(helpers.cmp_billed_until_or_registered_on))
            orders = sorted(orders, key=cmp_to_key(helpers.cmp_billed_until_or_registered_on))
            self.assign_compensations(givers, orders, **options)
        
        rates = self.get_rates(account)
        has_billing_period = self.billing_period != self.NEVER
        has_pricing_period = self.get_pricing_period() != self.NEVER
        if rates and (has_billing_period or has_pricing_period):
            concurrent = has_billing_period and not has_pricing_period
            if not concurrent:
                rdelta = self.get_pricing_rdelta()
                ini -= rdelta
            porders = related_orders.pricing_orders(ini, end)
            porders = list(set(orders).union(set(porders)))
            porders = sorted(porders, key=cmp_to_key(helpers.cmp_billed_until_or_registered_on))
            if concurrent:
                # Periodic billing with no pricing period
                lines = self.bill_concurrent_orders(account, porders, rates, ini, end)
            else:
                # Periodic and one-time billing with pricing period
                lines = self.bill_registered_or_renew_events(account, porders, rates)
        else:
            # No rates optimization or one-time billing without pricing period
            lines = []
            price = self.nominal_price
            # Calculate nominal price
            for order in orders:
                ini = order.billed_until or order.registered_on
                end = order.new_billed_until
                discounts = ()
                dsize, new_end = self.apply_compensations(order)
                if dsize:
                    discounts=(
                        (self._COMPENSATION, -dsize*price),
                    )
                    if new_end:
                        order.new_billed_until = new_end
                        end = new_end
                line = self.generate_line(order, price, ini, end, discounts=discounts)
                lines.append(line)
        return lines
    
    def bill_with_metric(self, orders, account, **options):
        lines = []
        bp = None
        for order in orders:
            bp = self.get_billing_point(order, bp=bp, **options)
            if (self.billing_period != self.NEVER and
                self.get_pricing_period() == self.NEVER and
                self.payment_style == self.PREPAY and order.billed_on):
                    # Recharge
                    if self.payment_style == self.PREPAY and order.billed_on:
                        rini = order.billed_on
                        charged = None
                        new_metric, new_price = 0, 0
                        for cini, cend, metric in order.get_metric(rini, bp, changes=True):
                            if charged is None:
                                charged = metric
                            size = self.get_price_size(cini, cend)
                            new_price += self.get_price(order, metric) * size
                            new_metric += metric
                        size = self.get_price_size(rini, bp)
                        old_price = self.get_price(account, charged) * size
                        if new_price > old_price:
                            metric = new_metric - charged
                            price = new_price - old_price
                            lines.append(self.generate_line(order, price, rini, bp, metric=metric, computed=True))
            if order.billed_until and order.cancelled_on and order.cancelled_on >= order.billed_until:
                continue
            if self.billing_period != self.NEVER:
                ini = order.billed_until or order.registered_on
                # Periodic billing
                if bp <= ini:
                    continue
                order.new_billed_until = bp
                if self.get_pricing_period() == self.NEVER:
                    # Changes (Mailbox disk-like)
                    for cini, cend, metric in order.get_metric(ini, bp, changes=True):
                        price = self.get_price(account, metric)
                        lines.append(self.generate_line(order, price, cini, cend, metric=metric))
                elif self.get_pricing_period() == self.billing_period:
                    # pricing_slots (Traffic-like)
                    if self.payment_style == self.PREPAY:
                        raise NotImplementedError
                    for cini, cend in self.get_pricing_slots(ini, bp):
                        metric = order.get_metric(cini, cend)
                        price = self.get_price(account, metric)
                        lines.append(self.generate_line(order, price, cini, cend, metric=metric))
                else:
                    raise NotImplementedError
            else:
                # One-time billing
                if order.billed_until:
                    continue
                date = timezone.now().date()
                order.new_billed_until = date
                if self.get_pricing_period() == self.NEVER:
                    # get metric (Job-like)
                    metric = order.get_metric(date)
                    price = self.get_price(account, metric)
                    lines.append(self.generate_line(order, price, date, metric=metric))
                else:
                    raise NotImplementedError
        return lines
    
    def generate_bill_lines(self, orders, account, **options):
        if options.get('proforma', False):
            options['commit'] = False
        if not self.metric:
            lines = self.bill_with_orders(orders, account, **options)
        else:
            lines = self.bill_with_metric(orders, account, **options)
        if options.get('commit', True):
            now = timezone.now().date()
            for line in lines:
                order = line.order
                order.billed_on = now
                order.billed_until = getattr(order, 'new_billed_until', order.billed_until)
                order.save(update_fields=['billed_on', 'billed_until'])
        return lines
