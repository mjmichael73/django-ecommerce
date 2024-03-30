from celery import shared_task
from django.core.mail import send_mail
from orders.models import Order


@shared_task
def order_created(order_id):
    """
    Task to send email notification when an order is successfully created.
    :param order_id:
    :return:
    """
    print(f"Task called for Order Id: {order_id}")
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
    print(f"Result of sending email is: {mail_sent}")
    return mail_sent
