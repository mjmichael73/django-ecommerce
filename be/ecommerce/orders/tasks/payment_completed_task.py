from celery import shared_task
from django.core.mail import send_mail
from orders.models import Order


@shared_task
def payment_completed(order_id, _request_id=None):
    """
    Task to send email notification when an order is completely paid.

    ``_request_id`` is an optional correlation ID forwarded from the web
    request that triggered payment (card 6cc44f8g). It is not used directly
    here — the Celery task-tracing hook in ``ecommerce/celery.py`` rebinds
    the contextvar so log lines emitted by this task (and its callees)
    carry the originating ``request_id`` without polluting the task body.
    The parameter is accepted so ``propagate_request_id()`` can splat it
    onto ``delay(...)`` without ``UnexpectedKeywordError``.
    """
    order = Order.objects.get(id=order_id)
    subject = f'Order nr. {order.id}'
    message = f'Dear {order.first_name},\n\n You have successfully placed an order. Your order ID is {order.id}.'
    mail_sent = send_mail(
        subject,
        message,
        'admin@ecommerce.com',
        [
            order.email
        ]
    )
    return mail_sent
