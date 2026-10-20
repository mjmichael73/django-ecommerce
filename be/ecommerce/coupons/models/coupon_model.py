from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator


class Coupon(models.Model):
    # ``code`` is ``unique=True``: Django creates an implicit unique index on the
    # field, so coupon lookups by code (e.g. ``Coupon.objects.get(code=...)`` in
    # ``coupon_apply_view``) are already covered without an explicit index.
    # Do NOT add a redundant ``models.Index(fields=['code'])`` — it would be a
    # no-op and only add migration noise.
    code = models.CharField(max_length=50, unique=True)
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField()
    discount = models.IntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text='Percentage value (0 to 100)'
    )
    active = models.BooleanField()

    def __str__(self):
        return self.code
