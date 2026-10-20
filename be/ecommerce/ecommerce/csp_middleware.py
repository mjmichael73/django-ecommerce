"""Content Security Policy header middleware.

Emits a single ``Content-Security-Policy`` header on every response the app
produces. The value is compiled from ``settings.CSP_POLICY`` (a directive ->
value-string dict assembled in ``settings.py`` from plain constants) plus an
optional ``report-uri`` taken from the ``CSP_REPORT_URI`` environment
variable, which is only appended when set.

There is no per-request parsing: the policy lives in one settings constant
and the header value is rebuilt from it on each response (cheap dict -> string
formatting, and it honours ``override_settings`` in tests). The middleware is
registered outermost (MIDDLEWARE position 0) so it wraps responses produced by
all inner layers — SecurityMiddleware SECURE_SSL_REDIRECT 301s, WhiteNoise
static responses, and CommonMiddleware APPEND_SLASH / disallowed-UA 403s all
pass through it — and applies to all responses equally (including 429/plain
ones).
"""
from django.conf import settings


def _csp_header_value() -> str:
    """Compile the header value from ``settings.CSP_POLICY``.

    Read on every request so ``override_settings`` (tests) is honoured.
    """
    parts = [
        f"{directive} {value}"
        for directive, value in settings.CSP_POLICY.items()
    ]
    report_uri = getattr(settings, "CSP_REPORT_URI", "") or ""
    if report_uri:
        parts.append(f"report-uri {report_uri}")
    return "; ".join(parts)


class CSPMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response["Content-Security-Policy"] = _csp_header_value()
        return response
