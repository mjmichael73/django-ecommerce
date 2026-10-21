"""Return / refund request model (card 4fcgcg9e).

A customer submits a return request for an item (or the whole order) they
received. An analyst reviews the request in the admin and — if approved —
issues the actual Stripe refund via ``payment.refunds.refund_order``.

The request itself never triggers a refund: nothing here is automatic. The
analyst chooses to refund (full or partial, via the existing idempotent
``refund_order`` primitive) and the request's status records the decision.
"""
from django.db import models
from django.conf import settings

from orders.models import Order, OrderItem


class ReturnRequest(models.Model):
    """A customer's request to return goods and/or receive a refund.

    The lifecycle is purely analyst-driven on the *refund* side. A request is
    submitted as ``SUBMITTED`` and an analyst moves it through
    ``APPROVED`` → ``REFUNDED`` (or ``REJECTED`` / ``CANCELED``). Only
    when a request reaches ``REFUNDED`` is a Stripe refund actually issued
    — and even then it goes through ``refund_order``'s idempotency guards so
    a double click or a webhook replay can never refund twice.
    """

    class Status(models.TextChoices):
        SUBMITTED = 'SUBMITTED', 'Submitted'
        APPROVED = 'APPROVED', 'Approved'
        REFUNDED = 'REFUNDED', 'Refunded'
        REJECTED = 'REJECTED', 'Rejected'
        CANCELED = 'CANCELED', 'Canceled'

    order = models.ForeignKey(
        Order,
        related_name='return_requests',
        on_delete=models.CASCADE,
    )
    # Optional line-item scope: when set, the request covers only one item.
    # When NULL the request covers the whole order.
    item = models.ForeignKey(
        OrderItem,
        related_name='return_requests',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='return_requests',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text='Customer who submitted the request. NULL for guest orders.',
    )

    # What the customer is returning / why.
    reason = models.CharField(
        max_length=300,
        help_text='Why the customer is requesting this return.',
    )
    # Free-form detail — may contain PII, so treat as sensitive when surfaced.
    detail = models.TextField(blank=True)

    # Analyst-managed state.
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.SUBMITTED,
    )
    # When the analyst recorded the refund action (mirrors Order.refunded_at
    # but scoped to this request row).
    refunded_at = models.DateTimeField(null=True, blank=True)
    # The Stripe refund id that resolved this request, if any.
    refund_id = models.CharField(max_length=250, blank=True)

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created']
        indexes = [
            models.Index(fields=['-created']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f'Return #{self.id} for order {self.order_id}'

    def is_full_order(self) -> bool:
        """True when this request covers the whole order (no item scope)."""
        return self.item_id is None

    def get_item_cost(self):
        """Return the line-item value at stake, or the order total for a
        full-order request.

        Always returns a Decimal in $ units — the same unit ``refund_order``
        expects — so a downstream refund call needs no conversion.
        """
        if self.item_id is not None and self.item_id:
            return self.item.get_cost()
        return self.order.get_total_cost()
