from django.http import HttpResponse
from django.template.loader import render_to_string
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import get_object_or_404
import weasyprint
from ecommerce.pdf_stylesheet import pdf_invoice_css_path
from orders.models import Order


@staff_member_required
def admin_order_pdf(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    html = render_to_string(
        "orders/order/pdf.html",
        {
            "order": order
        }
    )
    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f'filename=order_{order.id}.pdf'
    weasyprint.HTML(string=html).write_pdf(
        response,
        stylesheets=[weasyprint.CSS(filename=pdf_invoice_css_path())],
    )
    return response
