from celery import shared_task
from orders.models import Order


@shared_task
def payment_completed(order_id, _request_id=None):
    """Send the paid-order invoice email (card a3cg42c4).

    ``_request_id`` is forwarded from the webhook's originating request
    (card 6cc44f8g). The Celery task-tracing hook in ``ecommerce/celery.py``
    rebinds the contextvar so log lines emitted here carry that ID for
    end-to-end correlation — the parameter itself is not used directly.
    """
    from io import BytesIO

    import weasyprint
    from django.template.loader import render_to_string
    from django.core.mail import EmailMessage

    from ecommerce.pdf_stylesheet import pdf_invoice_css_path

    order = Order.objects.get(id=order_id)
    subject = f'Django Ecommerce - Invoice no. {order.id}'
    message = 'Please, find attached invoice for your recent purchase.'
    email = EmailMessage(
        subject,
        message,
        'admin@myshop.com',
        [
            order.email
        ]
    )
    html = render_to_string('orders/order/pdf.html', {'order': order})
    out = BytesIO()
    stylesheets = [weasyprint.CSS(filename=pdf_invoice_css_path())]
    weasyprint.HTML(string=html).write_pdf(out, stylesheets=stylesheets)
    email.attach(f'order_{order.id}.pdf', out.getvalue(), 'application/pdf')
    email.send()
