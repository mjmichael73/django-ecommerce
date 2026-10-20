"""CSRF token rotation on identity state changes (card ef86d264).

The tests use a client with ``enforce_csrf_checks=True`` so the requests
behave like a browser: a POST is only accepted with the token that the
server handed out, and the rotation is observed on the ``csrftoken`` cookie
the server sends back.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.middleware.csrf import CsrfViewMiddleware
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from ecommerce.csrf_rotation import login_with_csrf_rotation, rotate_csrf_token

User = get_user_model()

PASSWORD = 'complex-pass-phrase-99'


class CsrfRotationHttpTests(TestCase):
    """Observable rotation of the ``csrftoken`` cookie over HTTP."""

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.user = User.objects.create_user(
            'rotator',
            'rotator@example.com',
            PASSWORD,
        )

    def _token(self):
        """The secret currently held in the ``csrftoken`` cookie."""
        return self.client.cookies['csrftoken'].value

    def _fetch_token(self, url):
        """GET a page that renders a CSRF-protected form and return the token."""
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return self._token()

    def test_login_rotates_csrf_token(self):
        before = self._fetch_token(reverse('accounts:login'))
        response = self.client.post(
            reverse('accounts:login'),
            {
                'username': 'rotator',
                'password': PASSWORD,
                'csrfmiddlewaretoken': before,
            },
        )
        self.assertRedirects(response, reverse('accounts:profile'))
        self.assertNotEqual(self._token(), before)

    def test_registration_rotates_csrf_token(self):
        before = self._fetch_token(reverse('accounts:register'))
        response = self.client.post(
            reverse('accounts:register'),
            {
                'username': 'newrotator',
                'email': 'newrotator@example.com',
                'password1': PASSWORD,
                'password2': PASSWORD,
                'csrfmiddlewaretoken': before,
            },
        )
        self.assertRedirects(response, reverse('accounts:profile'))
        self.assertNotEqual(self._token(), before)

    def test_logout_rotates_csrf_token(self):
        self.assertTrue(self.client.login(username='rotator', password=PASSWORD))
        before = self._fetch_token(reverse('shop:product_list'))
        response = self.client.post(
            reverse('accounts:logout'),
            {'csrfmiddlewaretoken': before},
        )
        self.assertRedirects(response, reverse('shop:product_list'))
        self.assertNotEqual(self._token(), before)

    def test_failed_login_does_not_rotate_csrf_token(self):
        """A failed attempt must not burn the token of the login form."""
        before = self._fetch_token(reverse('accounts:login'))
        response = self.client.post(
            reverse('accounts:login'),
            {
                'username': 'rotator',
                'password': 'wrong-password',
                'csrfmiddlewaretoken': before,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['user'].is_authenticated)
        self.assertEqual(self._token(), before)

    def test_token_from_before_login_is_rejected_after_login(self):
        """The token captured before the login can no longer be replayed."""
        before = self._fetch_token(reverse('accounts:login'))
        self.client.post(
            reverse('accounts:login'),
            {
                'username': 'rotator',
                'password': PASSWORD,
                'csrfmiddlewaretoken': before,
            },
        )
        response = self.client.post(
            reverse('accounts:logout'),
            {'csrfmiddlewaretoken': before},
        )
        self.assertEqual(response.status_code, 403)


class CsrfRotationUnitTests(TestCase):
    """The rotation helpers themselves, without going through HTTP."""

    def _request_with_csrf_cookie(self):
        """A request carrying a CSRF cookie, as the middleware would see it."""
        request = RequestFactory().post('/accounts/register/')
        request.COOKIES['csrftoken'] = 'a' * 32
        middleware = CsrfViewMiddleware(get_response=lambda request: HttpResponse())
        middleware.process_request(request)
        return request

    def test_rotation_yields_a_different_token_on_every_call(self):
        request = self._request_with_csrf_cookie()
        original = request.META['CSRF_COOKIE']
        rotate_csrf_token(request)
        first = request.META['CSRF_COOKIE']
        rotate_csrf_token(request)
        second = request.META['CSRF_COOKIE']
        self.assertNotEqual(first, original)
        self.assertNotEqual(second, first)

    def test_login_helper_rotates_the_token_and_logs_the_user_in(self):
        request = self._request_with_csrf_cookie()
        request.session = self.client.session
        request.user = AnonymousUser()
        user = User.objects.create_user('helper', 'helper@example.com', PASSWORD)
        original = request.META['CSRF_COOKIE']

        login_with_csrf_rotation(request, user)

        self.assertEqual(request.user, user)
        self.assertNotEqual(request.META['CSRF_COOKIE'], original)
