"""
Resolve the invoice PDF stylesheet for WeasyPrint.

Production / Docker: use ``STATIC_ROOT/css/pdf.css`` after ``collectstatic``.
Development: fall back to django.contrib.staticfiles finders (e.g. ``shop/static/css/pdf.css``)
when STATIC_ROOT has not been populated yet.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders


def pdf_invoice_css_path() -> str:
    """
    Absolute filesystem path to ``css/pdf.css`` for use with ``weasyprint.CSS(filename=...)``.
    """
    root_candidate = Path(settings.STATIC_ROOT) / 'css' / 'pdf.css'
    if root_candidate.is_file():
        return str(root_candidate.resolve())
    found = finders.find('css/pdf.css')
    if found:
        return str(Path(found).resolve())
    raise FileNotFoundError(
        'Missing css/pdf.css: run `python manage.py collectstatic` or keep '
        'shop/static/css/pdf.css in the project.'
    )
