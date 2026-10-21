"""Unit tests for account forms (card d7ac2957).

Covers ``RegisterForm.clean_email``, ``RegisterForm.save``, and
``ProfileForm.clean_email`` as pure unit tests (form validation + model
side-effects), complementing the integration-level coverage in
``test_accounts.py`` and ``test_email_verification.py``.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.forms import RegisterForm, ProfileForm

User = get_user_model()

PASS = 'complex-pass-phrase-99'


class RegisterFormCleanEmailTests(TestCase):
    """``RegisterForm.clean_email`` rejects duplicate addresses case-insensitively."""

    def test_clean_email_accepts_new_address(self):
        form = RegisterForm({
            'username': 'newuser',
            'email': 'unique@example.com',
            'password1': PASS,
            'password2': PASS,
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['email'], 'unique@example.com')

    def test_clean_email_rejects_exact_duplicate(self):
        User.objects.create_user('existing', 'dup@example.com', PASS)
        form = RegisterForm({
            'username': 'newuser',
            'email': 'dup@example.com',
            'password1': PASS,
            'password2': PASS,
        })
        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)
        # The error message includes the 'email_taken' code (see RegisterForm.clean_email).
        self.assertIn('email_taken', form.errors['email'].as_json())

    def test_clean_email_case_insensitive_match(self):
        """An address differing only in case is treated as a duplicate."""
        User.objects.create_user('existing', 'dup@example.com', PASS)
        form = RegisterForm({
            'username': 'newuser',
            'email': 'DUP@EXAMPLE.COM',
            'password1': PASS,
            'password2': PASS,
        })
        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)


class RegisterFormSaveTests(TestCase):
    """``RegisterForm.save`` persists the user with the correct email."""

    def test_save_creates_user_with_email(self):
        form = RegisterForm({
            'username': 'newshopper',
            'email': 'new@example.com',
            'password1': PASS,
            'password2': PASS,
        })
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertEqual(user.email, 'new@example.com')
        self.assertEqual(user.username, 'newshopper')

    def test_save_commit_false_does_not_persist(self):
        """``commit=False`` builds the instance but does not hit the DB."""
        form = RegisterForm({
            'username': 'newshopper',
            'email': 'new@example.com',
            'password1': PASS,
            'password2': PASS,
        })
        form.is_valid()
        user = form.save(commit=False)
        # The user is built but not saved.
        self.assertFalse(User.objects.filter(username='newshopper').exists())


class ProfileFormCleanEmailTests(TestCase):
    """``ProfileForm.clean_email`` rejects a conflicting email that belongs to
    another user, while allowing a user to keep their own email."""

    def setUp(self):
        self.user = User.objects.create_user(
            'profileuser', 'profile@example.com', PASS,
        )

    def test_clean_email_allows_own_email(self):
        form = ProfileForm(
            {'first_name': 'Pat', 'last_name': 'Lee', 'email': 'profile@example.com'},
            instance=self.user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['email'], 'profile@example.com')

    def test_clean_email_rejects_other_users_email(self):
        User.objects.create_user('other', 'taken@example.com', PASS)
        form = ProfileForm(
            {'first_name': 'Pat', 'last_name': 'Lee', 'email': 'taken@example.com'},
            instance=self.user,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)
        self.assertIn('email_taken', form.errors['email'].as_json())

    def test_clean_email_case_insensitive_other_user(self):
        """Case-only difference in another user's email is still a conflict."""
        User.objects.create_user('other', 'taken@example.com', PASS)
        form = ProfileForm(
            {'first_name': 'Pat', 'last_name': 'Lee', 'email': 'TAKEN@EXAMPLE.COM'},
            instance=self.user,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)
