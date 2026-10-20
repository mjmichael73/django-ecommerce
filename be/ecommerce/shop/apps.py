from django.apps import AppConfig


class ShopConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'shop'

    def ready(self):
        # Wire cache invalidation signal handlers (card 29b8eadg).
        from shop import signals  # noqa: F401
