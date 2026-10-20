"""Product reviews (card bd8f759h).

Model (one review per user per product, user PROTECT), the redirect-only
POST view with a success/already-reviewed/invalid message tripwire, the
detail-page review surface (summary chip, list, form vs. log-in link),
the ``review`` rate-limit scope, and the admin approve/unapprove actions.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.urls import reverse

from shop.forms import ProductReviewForm
from shop.models import Category, Product, ProductReview

User = get_user_model()

REVIEW_URL_NAME = 'shop:product_reviews'


class ProductReviewFixture:
    def setUp(self):
        super().setUp()
        self.category = Category.objects.create(name='Home', slug='home')
        self.product = Product.objects.create(
            category=self.category, name='Mug', slug='mug',
            price=Decimal('10.00'),
        )
        self.user = User.objects.create_user(
            'reviewer', 'reviewer@example.com', 'complex-pass-phrase-99',
        )

    def make_review(self, product=None, user=None, rating=4,
                    title='Good mug', body='Holds tea.', approved=True):
        return ProductReview.objects.create(
            product=product or self.product,
            user=user or self.user,
            rating=rating,
            title=title,
            body=body,
            approved=approved,
        )


class ProductReviewModelTests(ProductReviewFixture, TestCase):
    def test_same_cannot_review_twice_same_user_product(self):
        self.make_review(rating=5)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProductReview.objects.create(
                    product=self.product, user=self.user,
                    rating=3, title='Again', body='Second attempt.',
                )
        self.assertEqual(ProductReview.objects.count(), 1)

    def test_same_user_can_review_two_products(self):
        other = Product.objects.create(
            category=self.category, name='Bowl', slug='bowl',
            price=Decimal('12.00'),
        )
        self.make_review()
        self.make_review(product=other)
        self.assertEqual(ProductReview.objects.count(), 2)

    def test_deleting_user_with_reviews_is_protected(self):
        self.make_review()
        with self.assertRaises(ProtectedError):
            self.user.delete()
        # The failed delete left both rows in place.
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
        self.assertEqual(ProductReview.objects.count(), 1)

    def test_deleting_product_cascades_the_review(self):
        review = self.make_review()
        product_id = self.product.id
        self.product.delete()
        self.assertFalse(ProductReview.objects.filter(pk=review.pk).exists())
        self.assertFalse(ProductReview.objects.filter(product_id=product_id).exists())

    def test_average_rating_is_none_with_zero_reviews(self):
        self.assertIsNone(self.product.average_rating())

    def test_average_rating_excludes_unapproved(self):
        self.make_review(rating=4)
        second = User.objects.create_user(
            'r2', 'r2@example.com', 'complex-pass-phrase-99',
        )
        self.make_review(user=second, rating=5)
        self.make_review(
            user=User.objects.create_user(
                'r3', 'r3@example.com', 'complex-pass-phrase-99',
            ),
            rating=1, approved=False,
        )
        average = self.product.average_rating()
        self.assertEqual(average, 4.5)
        self.assertEqual(self.product.review_count(), 2)
        # count is an int, not None
        self.assertIsInstance(self.product.review_count(), int)

    def test_review_count_is_int_zero_without_reviews(self):
        self.assertEqual(self.product.review_count(), 0)

    def test_body_over_2000_chars_rejected_at_model_level(self):
        # TextField does not attach a MaxLengthValidator on its own — the
        # validator below is explicit, so admin/shell/programmatic writes
        # are bounded the same way as the form path.
        review = ProductReview(
            product=self.product, user=self.user, rating=3,
            title='T', body='x' * 2001,
        )
        with self.assertRaises(ValidationError):
            review.full_clean()


class RatingBoundTests(ProductReviewFixture, TestCase):
    def _review(self, rating):
        return ProductReview(
            product=self.product, user=self.user, rating=rating,
            title='T', body='B',
        )

    def test_rating_zero_is_rejected(self):
        with self.assertRaises(ValidationError):
            self._review(0).full_clean()

    def test_rating_six_is_rejected(self):
        with self.assertRaises(ValidationError):
            self._review(6).full_clean()

    def test_rating_bounds_are_inclusive(self):
        self._review(1).full_clean()
        self._review(5).full_clean()


class ProductReviewFormTests(ProductReviewFixture, TestCase):
    def test_valid_payload(self):
        form = ProductReviewForm({
            'rating': '3', 'title': 'Fine', 'body': 'Does the job.',
        })
        self.assertTrue(form.is_valid(), form.errors)

    def test_out_of_range_rating_is_invalid(self):
        form = ProductReviewForm({
            'rating': '9', 'title': 'Fine', 'body': 'Does the job.',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('rating', form.errors)

    def test_body_over_2000_chars_is_rejected(self):
        form = ProductReviewForm({
            'rating': '3', 'title': 'Fine', 'body': 'x' * 2001,
        })
        self.assertFalse(form.is_valid())
        self.assertIn('body', form.errors)


def _review_data(rating='5', title='Sturdy', body='Great with loose leaf.'):
    return {'rating': rating, 'title': title, 'body': body}


class ProductReviewViewTests(ProductReviewFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse(
            REVIEW_URL_NAME, args=[self.product.id, self.product.slug],
        )
        self.client.force_login(self.user)

    def test_anonymous_post_redirects_to_login_with_next(self):
        self.client.logout()
        response = self.client.post(self.url, _review_data())
        location = response['Location']
        self.assertEqual(location.split('?')[0], '/accounts/login/')
        self.assertIn('next=', location)
        self.assertIn(
            reverse(REVIEW_URL_NAME, args=[self.product.id, self.product.slug]),
            location,
        )
        self.assertEqual(ProductReview.objects.count(), 0)

    def test_anonymous_get_redirects(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'].split('?')[0], '/accounts/login/')
        self.assertEqual(ProductReview.objects.count(), 0)

    def test_valid_post_creates_one_review_redirects_and_messages(self):
        response = self.client.post(self.url, _review_data(), follow=True)
        self.assertRedirects(
            response, self.product.get_absolute_url(),
        )
        self.assertEqual(ProductReview.objects.count(), 1)
        review = ProductReview.objects.get()
        self.assertEqual(review.user, self.user)
        self.assertEqual(review.product, self.product)
        self.assertEqual(review.rating, 5)
        html = response.content.decode()
        self.assertIn('Thanks! Your review was published.', html)
        self.assertIn('Great with loose leaf.', html)

    def test_duplicate_post_is_rejected_with_no_second_row(self):
        self.client.post(self.url, _review_data())
        response = self.client.post(
            self.url, _review_data(body='Trying again.'), follow=True,
        )
        self.assertRedirects(response, self.product.get_absolute_url())
        self.assertEqual(ProductReview.objects.count(), 1)
        self.assertIn('You already reviewed this product.', response.content.decode())

    def test_logged_in_get_bounces_back_to_detail(self):
        # The only path into a GET here is a login ?next= redirect; land the
        # user back on the review surface instead of a 405.
        response = self.client.get(self.url)
        self.assertRedirects(response, self.product.get_absolute_url())
        self.assertEqual(ProductReview.objects.count(), 0)

    def test_invalid_rating_rejected_no_row(self):
        response = self.client.post(
            self.url, _review_data(rating='9'), follow=True,
        )
        self.assertRedirects(response, self.product.get_absolute_url())
        self.assertEqual(ProductReview.objects.count(), 0)
        html = response.content.decode()
        self.assertIn('Your review was not posted', html)
        self.assertIn('rating', html)

    def test_unavailable_product_is_404(self):
        self.product.available = False
        self.product.save()
        response = self.client.post(self.url, _review_data())
        self.assertEqual(response.status_code, 404)
        self.assertEqual(ProductReview.objects.count(), 0)


class ProductDetailReviewSurfaceTests(ProductReviewFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.detail_url = self.product.get_absolute_url()

    def test_visitor_sees_summary_log_in_link_and_no_form(self):
        response = self.client.get(self.detail_url)
        html = response.content.decode()
        self.assertIn('No reviews yet', html)
        self.assertIn('Log in to review this product', html)
        self.assertIn(f'next={self.detail_url}', html)
        self.assertNotIn('name="rating"', html)

    def test_signed_in_non_reviewer_sees_the_form(self):
        self.client.force_login(self.user)
        html = self.client.get(self.detail_url).content.decode()
        self.assertIn('name="rating"', html)
        self.assertIn('name="title"', html)
        self.assertIn('name="body"', html)
        self.assertIn(reverse(
            REVIEW_URL_NAME, args=[self.product.id, self.product.slug],
        ), html)

    def test_user_who_reviewed_sees_own_review_and_note_no_form(self):
        self.make_review(title='Exactly what I hoped', body='Quiet pour.')
        self.client.force_login(self.user)
        html = self.client.get(self.detail_url).content.decode()
        self.assertIn('You reviewed this product.', html)
        self.assertIn('Exactly what I hoped', html)
        self.assertNotIn('name="rating"', html)

    def test_summary_chip_shows_average_and_count(self):
        self.make_review(rating=4)
        self.make_review(user=User.objects.create_user(
            'r4', 'r4@example.com', 'complex-pass-phrase-99',
        ), rating=5)
        # Unapproved review must not affect the aggregate.
        self.make_review(
            user=User.objects.create_user(
                'r5', 'r5@example.com', 'complex-pass-phrase-99',
            ),
            rating=1, approved=False,
        )
        html = self.client.get(self.detail_url).content.decode()
        self.assertIn('★ 4.5 · 2 reviews', html)

    def test_zero_reviews_never_shows_zero_stars(self):
        html = self.client.get(self.detail_url).content.decode()
        self.assertIn('No reviews yet', html)
        self.assertNotIn('★ 0', html)
        self.assertNotIn('★', html)

    def test_singular_review_wording(self):
        self.make_review(rating=2)
        html = self.client.get(self.detail_url).content.decode()
        self.assertIn('★ 2.0 · 1 review', html)
        self.assertNotIn('1 reviews', html)


@override_settings(RATE_LIMIT_REVIEW=(2, 3600))
class ReviewRateLimitTests(ProductReviewFixture, TestCase):
    """The review POST scope counts per client IP, like checkout/coupon."""

    def _post(self):
        return self.client.post(self.url, _review_data())

    def setUp(self):
        super().setUp()
        cache.clear()
        self.url = reverse(
            REVIEW_URL_NAME, args=[self.product.id, self.product.slug],
        )
        self.client.force_login(self.user)

    def test_third_post_within_window_is_429(self):
        self.assertEqual(self._post().status_code, 302)
        self.assertEqual(self._post().status_code, 302)

        response = self._post()
        self.assertEqual(response.status_code, 429)
        self.assertIn('Too many requests', response.content.decode())
        # The rejected POST never created a row.
        self.assertEqual(ProductReview.objects.count(), 1)

    def test_get_is_never_limited(self):
        for _ in range(5):
            response = self.client.get(self.url)
            self.assertRedirects(response, self.product.get_absolute_url())
        # GETs consumed nothing: the first two POSTs fit in the window of 2.
        self.assertEqual(self._post().status_code, 302)
        self.assertEqual(ProductReview.objects.count(), 1)
        self.assertEqual(self._post().status_code, 302)
        # ... and the third POST is limited, proving GETs never ate quota.
        self.assertEqual(self._post().status_code, 429)
        self.assertEqual(ProductReview.objects.count(), 1)


class ProductReviewAdminTests(ProductReviewFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.unapproved = self.make_review(
            user=User.objects.create_user(
                'ar1', 'ar1@example.com', 'complex-pass-phrase-99',
            ),
            rating=3, approved=False,
        )
        self.approved = self.make_review(
            user=User.objects.create_user(
                'ar2', 'ar2@example.com', 'complex-pass-phrase-99',
            ),
            rating=4, approved=True,
        )
        self.staff = User.objects.create_superuser(
            'review-admin', 'review-admin@example.com', 'password',
        )
        self.client.force_login(self.staff)
        self.changelist = reverse('admin:shop_productreview_changelist')

    def _run_action(self, action):
        return self.client.post(
            self.changelist,
            {
                'action': action,
                '_selected_action': [self.unapproved.id, self.approved.id],
            },
            follow=True,
        )

    def test_changelist_renders_publication_columns(self):
        response = self.client.get(self.changelist)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('Rating', html)
        self.assertIn('Approved', html)
        self.assertIn('Mug — 3/5', html)   # row content
        self.assertIn('Approve selected reviews', html)
        self.assertIn('Unapprove selected reviews', html)

    def test_approve_action_flips_and_messages(self):
        response = self._run_action('approve_reviews')
        html = response.content.decode()
        self.assertIn('Approved 2 review(s).', html)
        self.unapproved.refresh_from_db()
        self.approved.refresh_from_db()
        self.assertTrue(self.unapproved.approved)
        self.assertTrue(self.approved.approved)

    def test_unapprove_action_flips_and_messages(self):
        response = self._run_action('unapprove_reviews')
        html = response.content.decode()
        self.assertIn('Unapproved 2 review(s).', html)
        self.unapproved.refresh_from_db()
        self.approved.refresh_from_db()
        self.assertFalse(self.unapproved.approved)
        self.assertFalse(self.approved.approved)

    def test_action_only_touches_selected_rows(self):
        other = self.make_review(
            user=User.objects.create_user(
                'ar3', 'ar3@example.com', 'complex-pass-phrase-99',
            ),
            rating=2, approved=False,
        )
        self.client.post(
            self.changelist,
            {'action': 'approve_reviews', '_selected_action': [self.unapproved.id]},
        )
        other.refresh_from_db()
        self.assertFalse(other.approved)
