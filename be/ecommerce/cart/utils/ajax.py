"""AJAX response helpers for the cart app (card 77ce5899).

These keep the JSON contract consistent across ``cart_add``, ``cart_remove``,
and ``cart_detail``: a dict with ``ok``, ``total``, ``total_items``, and
context-dependent extras (``quantity``, ``product_name``, ``errors``).
"""
from decimal import Decimal

from django.http import JsonResponse


def cart_summary_json(cart):
    """Build the JSON body for an AJAX cart response."""
    return {
        'ok': True,
        'total': as_price(cart.get_total_price_after_discount()),
        'total_items': len(cart),
        'subtotal': as_price(cart.get_total_price()),
    }


def cart_error_json(message, status=400, errors=None):
    """Build a JSON error response for AJAX requests."""
    body = {'ok': False, 'error': message}
    if errors is not None:
        body['errors'] = errors
    return JsonResponse(body, status=status)


def format_form_errors(form):
    """Flatten a Django form's errors into a simple dict for JSON.

    Example: {"quantity": ["This field is required."]}
    """
    return {
        field: [str(e) for e in errors]
        for field, errors in form.errors.items()
    }


def as_price(value):
    """Format a Decimal price as a string with 2 decimal places."""
    return str(Decimal(value).quantize(Decimal('0.01')))
