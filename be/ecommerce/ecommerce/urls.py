from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from accounts.urls import password_reset_confirm_view

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls', namespace='accounts')),
    path('cart/', include('cart.urls', namespace='cart')),
    path('wishlist/', include('wishlist.urls', namespace='wishlist')),
    path('orders/', include('orders.urls', namespace='orders')),
    path('payment/', include('payment.urls', namespace='payment')),
    path('coupons/', include('coupons.urls', namespace='coupons')),
    path('', include('shop.urls', namespace='shop')),
    # Django's built-in reset email (registration/password_reset_email.html)
    # reverses the *bare* name ``password_reset_confirm``. Accounts' URLs are
    # namespaced, so the emailed link needs an unnamespaced alias for that name.
    # Same path and view as ``accounts:password_reset_confirm`` above.
    path(
        'accounts/reset/<uidb64>/<token>/',
        password_reset_confirm_view,
        name='password_reset_confirm',
    ),
]

# Social authentication (card hhde4h24): allauth's URLs live under the same
# /accounts/ prefix, appended AFTER the local accounts.urls include above so
# paths it shares (/accounts/login/, /accounts/logout/) keep resolving to the
# local views. The google_* paths it adds are new endpoints.
if settings.SOCIAL_AUTH_GOOGLE_ENABLED:
    urlpatterns += [path('accounts/', include('allauth.urls'))]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
