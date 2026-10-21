"""Admin for the return / refund request workflow (card 4fcgcg9e).

Refunds are analyst-initiated: a customer *requests* a return, an analyst
reviews it, and only when the analyst approves does the actual Stripe refund
flow (``payment.refunds.refund_order``) get triggered — never automatically,
never on submission. Each resolution is guarded by ``refund_order``'s
idempotency checks so a double click, a webhook replay, or a repeated admin
action cannot refund twice. Stripe errors are caught inside ``refund_order``
and surfaced as admin messages, never as 500s.
"""
from decimal import Decimal

from django.contrib import admin, messages
from django.utils import timezone

from payment.refunds import refund_order

from .models import ReturnRequest


@admin.register(ReturnRequest)
class ReturnRequestAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'order_link',
        'user',
        'item',
        'reason',
        'status',
        'refund_id',
        'created',
        'updated',
    ]
    list_filter = ['status', 'created', 'updated']
    search_fields = ['id', 'order__id', 'reason']
    readonly_fields = ['order', 'user', 'item', 'reason', 'detail', 'created', 'updated']
    actions = ['approve', 'reject', 'mark_refunded', 'mark_canceled']

    def order_link(self, obj):
        if obj.order_id:
            from django.urls import reverse
            from django.utils.safestring import mark_safe
            url = reverse('admin:orders_order_change', args=[obj.order_id])
            return mark_safe(f'<a href="{url}">Order #{obj.order_id}</a>')
        return '—'
    order_link.short_description = 'Order'

    def approve(self, request, queryset):
        """Analyst approves the return request.

        This flags the request as APPROVED — it does *not* refund yet. The
        analyst issues the refund separately (see ``mark_refunded`` or the
        Order admin's ``issue_full_refund``), keeping the refund action
        explicit and auditable.
        """
        count = 0
        for rr in queryset:
            if rr.status != ReturnRequest.Status.SUBMITTED:
                self.message_user(
                    request,
                    f'Return #{rr.id}: not SUBMITTED (current: {rr.status}), skipped.',
                    level=messages.WARNING,
                )
                continue
            rr.status = ReturnRequest.Status.APPROVED
            rr.save(update_fields=['status', 'updated'])
            count += 1
        plural = '' if count == 1 else 's'
        self.message_user(
            request,
            f'Approved {count} return request{plural}.',
            level=messages.SUCCESS,
        )
    approve.short_description = 'Approve selected return requests'

    def reject(self, request, queryset):
        """Analyst rejects the return request (no refund issued)."""
        count = 0
        for rr in queryset:
            if rr.status not in (
                ReturnRequest.Status.SUBMITTED,
                ReturnRequest.Status.APPROVED,
            ):
                self.message_user(
                    request,
                    f'Return #{rr.id}: cannot reject from status {rr.status}.',
                    level=messages.WARNING,
                )
                continue
            rr.status = ReturnRequest.Status.REJECTED
            rr.save(update_fields=['status', 'updated'])
            count += 1
        plural = '' if count == 1 else 's'
        self.message_user(
            request,
            f'Rejected {count} return request{plural}.',
            level=messages.SUCCESS,
        )
    reject.short_description = 'Reject selected return requests'

    def mark_refunded(self, request, queryset):
        """Issue the Stripe refund for approved return requests.

        Full-order requests refund the whole order (net of any prior partial
        refunds) through ``refund_order``. Item-scoped requests refund the
        item's cost via a partial refund. The call is idempotent: an order
        already fully refunded is refused by ``refund_order`` before Stripe is
        contacted, and the request itself is only flipped to REFUNDED when the
        underlying refund succeeds — a second analyst click never double-refunds.
        """
        refunded = 0
        failed = 0
        for rr in queryset.select_related('order', 'item'):
            if rr.status != ReturnRequest.Status.APPROVED:
                self.message_user(
                    request,
                    f'Return #{rr.id}: not APPROVED (current: {rr.status}), skipped.',
                    level=messages.WARNING,
                )
                failed += 1
                continue

            if rr.is_full_order():
                # Full-order refund — ``refund_order`` with amount=None refunds
                # the whole remaining balance and is guarded against double-refund.
                ok, reason = refund_order(rr.order)
            else:
                # Item-scoped partial refund. ``amount`` is the item's line cost;
                # refunds.py coerces it to cents and accumulates into
                # refunded_amount.
                amount = rr.get_item_cost()
                ok, reason = refund_order(rr.order, amount=amount)

            if ok:
                # Persist the resolution on the request row itself (targeted UPDATE).
                update_fields = ['status', 'refunded_at', 'refund_id', 'updated']
                # Read the stripe refund id from the order row that refund_order
                # just wrote, so the admin audit trail has something concrete.
                from orders.models import Order as _Order
                stripe_refund_id = _Order.objects.filter(pk=rr.order_id).values_list(
                    'refund_id', flat=True
                ).first() or ''
                ReturnRequest.objects.filter(pk=rr.pk).update(
                    status=ReturnRequest.Status.REFUNDED,
                    refunded_at=timezone.now(),
                    refund_id=stripe_refund_id,
                )
                refunded += 1
            else:
                failed += 1
                self.message_user(
                    request,
                    f'Return #{rr.id} (order {rr.order_id}): '
                    f'refund not issued — {reason}',
                    level=messages.WARNING,
                )

        self.message_user(
            request,
            f'{refunded} return request(s) refunded; {failed} skipped/errored.',
            level=messages.SUCCESS if failed == 0 else messages.WARNING,
        )
    mark_refunded.short_description = 'Issue Stripe refund for approved returns'

    def mark_canceled(self, request, queryset):
        """Analyst cancels an open or approved request (e.g. customer withdrew)."""
        count = 0
        for rr in queryset:
            if rr.status in (
                ReturnRequest.Status.REFUNDED,
                ReturnRequest.Status.REJECTED,
            ):
                self.message_user(
                    request,
                    f'Return #{rr.id}: terminal status {rr.status}, cannot cancel.',
                    level=messages.WARNING,
                )
                continue
            rr.status = ReturnRequest.Status.CANCELED
            rr.save(update_fields=['status', 'updated'])
            count += 1
        plural = '' if count == 1 else 's'
        self.message_user(
            request,
            f'Canceled {count} return request{plural}.',
            level=messages.SUCCESS,
        )
    mark_canceled.short_description = 'Cancel selected return requests'
