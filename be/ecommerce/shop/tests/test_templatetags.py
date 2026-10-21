"""Unit tests for template tags (card 3g83heaa, card 86ac598h).

Covers:
- facet_tags: ``dict_get``, ``get_category_count`` filters
- media_tags: ``srcset`` filter
"""
from django.template import Context, Template
from django.test import TestCase


class DictGetTests(TestCase):
    """``dict_get`` filter — look up a key, return 0 if missing."""

    def test_existing_key(self):
        template = Template(
            "{% load facet_tags %}{{ data|dict_get:'price' }}"
        )
        result = template.render(Context({'data': {'price': 42}}))
        self.assertEqual(result, '42')

    def test_missing_key_returns_zero(self):
        template = Template(
            "{% load facet_tags %}{{ data|dict_get:'missing' }}"
        )
        result = template.render(Context({'data': {'price': 42}}))
        self.assertEqual(result, '0')

    def test_none_dict_returns_zero(self):
        template = Template(
            "{% load facet_tags %}{{ data|dict_get:'price' }}"
        )
        result = template.render(Context({'data': None}))
        self.assertEqual(result, '0')


class GetCategoryCountTests(TestCase):
    """``get_category_count`` filter."""

    def test_existing_category(self):
        from shop.models import Category
        cat = Category.objects.create(name='Home', slug='home')
        template = Template(
            "{% load facet_tags %}{{ counts|get_category_count:category }}"
        )
        result = template.render(Context({
            'counts': {'home': 5, 'garden': 3},
            'category': cat,
        }))
        self.assertEqual(result, '5')

    def test_missing_category_returns_zero(self):
        from shop.models import Category
        cat = Category.objects.create(name='Office', slug='office')
        template = Template(
            "{% load facet_tags %}{{ counts|get_category_count:category }}"
        )
        result = template.render(Context({
            'counts': {'home': 5},
            'category': cat,
        }))
        self.assertEqual(result, '0')

    def test_none_counts_returns_zero(self):
        from shop.models import Category
        cat = Category.objects.create(name='Home', slug='home')
        template = Template(
            "{% load facet_tags %}{{ counts|get_category_count:category }}"
        )
        result = template.render(Context({
            'counts': None,
            'category': cat,
        }))
        self.assertEqual(result, '0')


class SrcsetFilterTests(TestCase):
    """``srcset`` filter renders a srcset attribute value list."""

    def test_empty_pairs_returns_empty(self):
        template = Template("{% load media_tags %}{% srcset pairs %}")
        result = template.render(Context({'pairs': []}))
        self.assertEqual(result, '')

    def test_multiple_pairs(self):
        template = Template("{% load media_tags %}{% srcset pairs %}")
        result = template.render(Context({
            'pairs': [
                ('https://example.com/img-300.jpg', 300),
                ('https://example.com/img-600.jpg', 600),
            ],
        }))
        self.assertEqual(result, 'https://example.com/img-300.jpg 300w, https://example.com/img-600.jpg 600w')

    def test_single_pair(self):
        template = Template("{% load media_tags %}{% srcset pairs %}")
        result = template.render(Context({
            'pairs': [('https://example.com/img-300.jpg', 300)],
        }))
        self.assertEqual(result, 'https://example.com/img-300.jpg 300w')
