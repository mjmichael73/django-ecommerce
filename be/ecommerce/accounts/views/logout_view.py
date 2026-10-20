from django.contrib.auth.views import LogoutView

from ecommerce.csrf_rotation import rotate_csrf_token


class CsrfRotatingLogoutView(LogoutView):
    """Logout view that rotates the CSRF token.

    Django's ``LogoutView`` flushes the session but leaves the CSRF cookie
    untouched, so a token captured before the logout would still be accepted
    afterwards. Rotating here closes that window.
    """

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        rotate_csrf_token(request)
        return response
