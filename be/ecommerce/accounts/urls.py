from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from accounts import views
from ecommerce.rate_limit import rate_limit

app_name = 'accounts'

# Password reset (card 9ee2accb) — Django's built-in views and default reset
# email. The POST that triggers the email is rate-limited (email bombing);
# GETs pass through untouched, exactly like checkout/coupon. The ``success_url``
# defaults reverse bare names (``password_reset_done``/``password_reset_complete``)
# which do not resolve inside the ``accounts`` namespace, so both are explicit.
password_reset_view = rate_limit('password_reset')(
    auth_views.PasswordResetView.as_view(
        template_name='accounts/password_reset_form.html',
        success_url=reverse_lazy('accounts:password_reset_done'),
    )
)

password_reset_confirm_view = auth_views.PasswordResetConfirmView.as_view(
    template_name='accounts/password_reset_confirm.html',
    success_url=reverse_lazy('accounts:password_reset_complete'),
    post_reset_login=False,
)

urlpatterns = [
    path('register/', views.register, name='register'),
    path(
        'verify-email/<str:token_secret>/',
        views.verify_email,
        name='verify_email',
    ),
    path(
        'resend-verification/',
        views.resend_verification,
        name='resend_verification',
    ),
    path(
        'login/',
        auth_views.LoginView.as_view(
            template_name='accounts/login.html',
            redirect_authenticated_user=True
        ),
        name='login',
    ),
    path(
        'logout/',
        views.CsrfRotatingLogoutView.as_view(),
        name='logout',
    ),
    path('profile/', views.profile, name='profile'),
    path('orders/', views.order_list, name='order_list'),
    path('orders/<int:pk>/', views.order_detail, name='order_detail'),
    path('tracking/', views.order_track, name='order_track'),
    path('returns/', views.return_request_list, name='return_list'),
    path('orders/<int:pk>/return/', views.return_request_create, name='order_return'),
    path(
        'password-reset/',
        password_reset_view,
        name='password_reset',
    ),
    path(
        'password-reset/done/',
        auth_views.PasswordResetDoneView.as_view(
            template_name='accounts/password_reset_done.html',
        ),
        name='password_reset_done',
    ),
    path(
        'reset/<uidb64>/<token>/',
        password_reset_confirm_view,
        name='password_reset_confirm',
    ),
    path(
        'reset/done/',
        auth_views.PasswordResetCompleteView.as_view(
            template_name='accounts/password_reset_complete.html',
        ),
        name='password_reset_complete',
    ),
]
