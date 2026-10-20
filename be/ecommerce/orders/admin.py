from django.contrib import admin
from django.contrib import messages
from django.urls import reverse
from orders.models import Order, OrderItem
from django.utils.safestring import mark_safe
import csv
import datetime
from django.http import HttpResponse

from payment.refunds import refund_order


def export_to_csv(modeladmin, request, queryset):
    opts = modeladmin.model._meta
    content_disposition = f'attachment; filename={opts.verbose_name}.csv'
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = content_disposition
    writer = csv.writer(response)
    fields = [field for field in opts.get_fields() if not field.many_to_many and not field.one_to_many]
    writer.writerow([field.verbose_name for field in fields])
    for obj in queryset:
        data_row = []
        for field in fields:
            value = getattr(obj, field.name)
            if isinstance(value, datetime.datetime):
                value = value.strftime('%d/%m/%Y')
            data_row.append(value)
        writer.writerow(data_row)
    return response


export_to_csv.short_description = 'Export to CSV'


def order_payment(obj):
    url = obj.get_stripe_url()
    if obj.stripe_id:
        html = f'<a href="{url}" target="_blank">{obj.stripe_id}</a>'
        return mark_safe(html)
    return ''


order_payment.short_description = 'Stripe payment'


def order_detail(obj):
    url = reverse("orders:admin_order_detail", args=[obj.id])
    return mark_safe(f'<a href="{url}">View</a>')


def order_pdf(obj):
    url = reverse('orders:admin_order_pdf', args=[obj.id])
    return mark_safe(f'<a href="{url}">PDF</a>')


order_pdf.short_description = "Invoice"


def mark_rules_reviewed(modeladmin, request, queryset):
    """Cheap analyst workflow (card 832hc4eg): accept the fraud flags.

    Sets ``is_flagged=False`` and prefixes ``fraud_notes`` with
    ``reviewed: `` — the score and the triggered rule names stay in the
    audit trail; nothing else on the order is touched.
    """
    for order in queryset:
        order.fraud_notes = f'reviewed: {order.fraud_notes}'[:250]
        order.is_flagged = False
        order.save(update_fields=['is_flagged', 'fraud_notes'])


mark_rules_reviewed.short_description = 'Mark fraud rules reviewed'


def issue_full_refund(modeladmin, request, queryset):
    """Issue a full Stripe refund for every selected order (card a3cg42c4).

    Refunds are analyst-initiated: nothing in the app triggers them
    automatically. The action is idempotent on the local state — an order
    that is already fully refunded is refused by ``payment.refunds`` before
    Stripe is called, so a double click cannot refund twice.
    """
    refunded = 0
    failed = 0
    for order in queryset:
        ok, reason = refund_order(order)
        if ok:
            refunded += 1
        else:
            failed += 1
            modeladmin.message_user(
                request,
                f'Order {order.id}: refund not issued — {reason}',
                level=messages.WARNING,
            )
    modeladmin.message_user(
        request,
        f'Full refund issued for {refunded} order(s); {failed} skipped.',
        level=messages.SUCCESS if failed == 0 else messages.WARNING,
    )


issue_full_refund.short_description = 'Issue full refund (Stripe)'


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    raw_id_fields = ['product']


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'user',
        'first_name',
        'last_name',
        'email',
        'address',
        'postal_code',
        'city',
        'paid',
        order_payment,
        'refund_id',
        'refunded_amount',
        'is_flagged',
        'fraud_score',
        'created',
        'updated',
        order_detail,
        order_pdf,
    ]
    list_filter = ['paid', 'created', 'updated', 'is_flagged']
    inlines = [OrderItemInline]
    actions = [export_to_csv, mark_rules_reviewed, issue_full_refund]
