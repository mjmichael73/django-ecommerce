"""Review submission form (card bd8f759h).

The detail page is the only review surface, so the form ships without its
own template — the view never re-renders it, it only validates.
"""
from django import forms

from shop.models import ProductReview


class ProductReviewForm(forms.ModelForm):
    """``[rating, title, body]``; rating is a semantic 1-5 select."""

    rating = forms.TypedChoiceField(
        choices=[(value, str(value)) for value in range(1, 6)],
        coerce=int,
        label='Rating',
        error_messages={
            'invalid_choice': 'Choose a rating between 1 and 5.',
        },
    )

    class Meta:
        model = ProductReview
        fields = ['rating', 'title', 'body']
        widgets = {
            'body': forms.Textarea(attrs={'rows': 4}),
        }
