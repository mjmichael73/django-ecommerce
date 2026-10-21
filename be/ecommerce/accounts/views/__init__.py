from .logout_view import CsrfRotatingLogoutView
from .order_views import order_detail, order_list, order_track
from .profile_view import profile
from .register_view import register
from .resend_verification_view import resend_verification
from .verify_email_view import verify_email
from returns.views import return_request_create, return_request_list

__all__ = [
    'CsrfRotatingLogoutView',
    'order_detail',
    'order_list',
    'order_track',
    'return_request_create',
    'return_request_list',
    'profile',
    'register',
    'resend_verification',
    'verify_email',
]
