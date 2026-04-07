from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from orders.models import Order, OrderItem
from shop.models import Category, Product

User = get_user_model()


class RegistrationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.url = reverse('accounts:register')

    def test_get_shows_form(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_post_creates_user_logs_in_redirects_profile(self):
        response = self.client.post(
            self.url,
            {
                'username': 'newshopper',
                'email': 'new@example.com',
                'password1': 'complex-pass-phrase-99',
                'password2': 'complex-pass-phrase-99',
            },
        )
        self.assertRedirects(response, reverse('accounts:profile'))
        user = User.objects.get(username='newshopper')
        self.assertEqual(user.email, 'new@example.com')
        response = self.client.get(reverse('accounts:profile'))
        self.assertEqual(response.status_code, 200)

    def test_post_duplicate_email_rejected(self):
        User.objects.create_user('u1', 'dup@example.com', 'complex-pass-phrase-99')
        response = self.client.post(
            self.url,
            {
                'username': 'u2',
                'email': 'dup@example.com',
                'password1': 'complex-pass-phrase-99',
                'password2': 'complex-pass-phrase-99',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'already exists')
        self.assertFalse(User.objects.filter(username='u2').exists())

    def test_authenticated_user_redirected_from_register(self):
        user = User.objects.create_user('x', 'x@example.com', 'complex-pass-phrase-99')
        self.client.force_login(user)
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse('accounts:profile'))


class LoginLogoutTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            'loginuser',
            'login@example.com',
            'complex-pass-phrase-99',
        )

    def test_login_post_success(self):
        response = self.client.post(
            reverse('accounts:login'),
            {'username': 'loginuser', 'password': 'complex-pass-phrase-99'},
        )
        self.assertRedirects(response, reverse('accounts:profile'))

    def test_login_post_invalid_credentials(self):
        response = self.client.post(
            reverse('accounts:login'),
            {'username': 'loginuser', 'password': 'wrong-password'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['user'].is_authenticated)

    def test_logout_post(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('accounts:logout'))
        self.assertRedirects(response, reverse('shop:product_list'))
        response = self.client.get(reverse('accounts:profile'))
        self.assertEqual(response.status_code, 302)


class ProfileTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            'profileuser',
            'profile@example.com',
            'complex-pass-phrase-99',
            first_name='Pat',
            last_name='Lee',
        )

    def test_profile_requires_login(self):
        response = self.client.get(reverse('accounts:profile'))
        login_url = reverse('accounts:login')
        self.assertRedirects(
            response,
            f'{login_url}?next={reverse("accounts:profile")}',
        )

    def test_profile_post_updates_and_messages(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('accounts:profile'),
            {'first_name': 'Patricia', 'last_name': 'Lee', 'email': 'profile@example.com'},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Patricia')
        content = response.content.decode().lower()
        self.assertIn('profile was updated', content)

    def test_profile_email_must_be_unique(self):
        User.objects.create_user('other', 'taken@example.com', 'complex-pass-phrase-99')
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('accounts:profile'),
            {
                'first_name': 'Pat',
                'last_name': 'Lee',
                'email': 'taken@example.com',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'profile@example.com')


class OrderHistoryTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user_a = User.objects.create_user(
            'a', 'a@example.com', 'complex-pass-phrase-99',
        )
        self.user_b = User.objects.create_user(
            'b', 'b@example.com', 'complex-pass-phrase-99',
        )
        self.order_a = Order.objects.create(
            user=self.user_a,
            first_name='A',
            last_name='A',
            email='a@example.com',
            address='1',
            postal_code='1',
            city='C',
        )
        self.order_b = Order.objects.create(
            user=self.user_b,
            first_name='B',
            last_name='B',
            email='b@example.com',
            address='2',
            postal_code='2',
            city='C',
        )

    def test_order_list_requires_login(self):
        response = self.client.get(reverse('accounts:order_list'))
        self.assertEqual(response.status_code, 302)

    def test_order_list_shows_only_own_orders(self):
        self.client.force_login(self.user_a)
        response = self.client.get(reverse('accounts:order_list'))
        self.assertEqual(response.status_code, 200)
        orders = response.context['orders']
        self.assertEqual(list(orders), [self.order_a])

    def test_order_detail_requires_login(self):
        url = reverse('accounts:order_detail', args=[self.order_a.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_order_detail_owner_ok(self):
        self.client.force_login(self.user_a)
        url = reverse('accounts:order_detail', args=[self.order_a.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['order'], self.order_a)

    def test_order_detail_other_user_404(self):
        self.client.force_login(self.user_b)
        url = reverse('accounts:order_detail', args=[self.order_a.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)


@patch('orders.views.order_create_view.order_created.delay')
class CheckoutUserAssociationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.category = Category.objects.create(name='Books', slug='books')
        self.product = Product.objects.create(
            category=self.category,
            name='Guide',
            slug='guide',
            price=Decimal('19.99'),
        )
        session = self.client.session
        session['cart'] = {
            str(self.product.id): {'quantity': 1, 'price': str(self.product.price)},
        }
        session.save()
        self.user = User.objects.create_user(
            'buyer',
            'buyer@example.com',
            'complex-pass-phrase-99',
            first_name='Sam',
            last_name='Shopper',
        )
        self.checkout_url = reverse('orders:order_create')
        self.payload = {
            'first_name': 'Sam',
            'last_name': 'Shopper',
            'email': 'buyer@example.com',
            'address': '9 Test Ave',
            'postal_code': '99999',
            'city': 'Testville',
        }

    def test_guest_order_has_no_user(self, _mock):
        self.client.post(self.checkout_url, self.payload)
        order = Order.objects.get()
        self.assertIsNone(order.user)

    def test_authenticated_checkout_sets_user(self, _mock):
        self.client.force_login(self.user)
        self.client.post(self.checkout_url, self.payload)
        order = Order.objects.get()
        self.assertEqual(order.user_id, self.user.pk)

    def test_checkout_form_prefill_for_authenticated_user(self, _mock):
        self.client.force_login(self.user)
        response = self.client.get(self.checkout_url)
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertEqual(form.initial.get('first_name'), 'Sam')
        self.assertEqual(form.initial.get('email'), 'buyer@example.com')
