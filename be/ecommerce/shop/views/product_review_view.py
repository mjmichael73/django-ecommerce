"""Review submission view (card bd8f759h).

POST-only redirect view: there is no dedicated review template — the
product detail page IS the review surface, so every outcome (success,
duplicate, invalid input) stamps a message and redirects back there.

A GET on this route can only come from a login ``?next=`` redirect (nothing
links to it), so it is sent back to the detail page instead of 405'ing — a
user bounced to login mid-review lands on the form again. Only POSTs are
ever processed, and the rate limiter counts POSTs only.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect

from ecommerce.rate_limit import rate_limit
from shop.forms import ProductReviewForm
from shop.models import Product, ProductReview

SUCCESS_MESSAGE = 'Thanks! Your review was published.'
ALREADY_REVIEWED_MESSAGE = 'You already reviewed this product.'


# rate_limit outermost so a non-POST never consumes quota; the window is
# still consumed by unauthenticated POSTs (login_required sits inside the
# limiter).
@rate_limit('review')
@login_required
def product_review(request, product_id, product_slug):
    product = get_object_or_404(
        Product, id=product_id, slug=product_slug, available=True
    )
    if request.method != 'POST':
        return redirect(product.get_absolute_url())

    form = ProductReviewForm(request.POST)
    if not form.is_valid():
        problems = '; '.join(
            f'{field}: {" ".join(errors)}' for field, errors in form.errors.items()
        )
        messages.error(request, f'Your review was not posted — {problems}')
        return redirect(product.get_absolute_url())

    # The one-per-user rule is mirrored early so an entirely expected user
    # action (resubmitting) never reaches an INSERT that would violate the
    # constraint and log a django.db.backends ERROR traceback.
    if ProductReview.objects.filter(product=product, user=request.user).exists():
        messages.error(request, ALREADY_REVIEWED_MESSAGE)
        return redirect(product.get_absolute_url())

    try:
        with transaction.atomic():
            review = form.save(commit=False)
            review.product = product
            review.user = request.user
            review.save()
    except IntegrityError:
        # The per-user unique constraint is the last line of defense: two
        # racing submissions cannot create a second row.
        messages.error(request, ALREADY_REVIEWED_MESSAGE)
        return redirect(product.get_absolute_url())

    messages.success(request, SUCCESS_MESSAGE)
    return redirect(product.get_absolute_url())
