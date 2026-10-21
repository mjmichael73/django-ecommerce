from django import forms

from orders.models import OrderItem
from returns.models import ReturnRequest


class ReturnRequestForm(forms.ModelForm):
    """Customer-facing form: a free-text reason for the return.

    The order (and optionally a line item) is pinned by the URL/view — the
    customer never picks which order to return, only *why*. The ``item``
    field is a hidden choice so the customer can scope a return to one line
    without touching it directly.
    """

    item = forms.ModelChoiceField(
        queryset=OrderItem.objects.none(),
        required=False,
        widget=forms.HiddenInput,
    )

    class Meta:
        model = ReturnRequest
        fields = ['reason', 'detail', 'item']
        widgets = {
            'reason': forms.TextInput(
                attrs={'maxlength': 300, 'placeholder': 'Reason for return'},
            ),
            'detail': forms.Textarea(
                attrs={'rows': 4, 'placeholder': 'Any additional details (optional)'},
            ),
        }

    def __init__(self, *args, order=None, **kwargs):
        """Bind the item queryset to *order*'s lines.

        ``order`` is required — the caller asserts ownership before this form
        ever sees the data, so a customer cannot inject an item from another
        order.
        """
        super().__init__(*args, **kwargs)
        if order is not None:
            self.fields['item'].queryset = order.items.all()
        self._order = order

    def clean(self):
        cleaned = super().clean()
        item = cleaned.get('item')
        if item is not None and item.order_id != getattr(self._order, 'id', None):
            self.add_error('item', 'That item does not belong to this order.')
        return cleaned

    def save(self, commit=True):
        request_obj = super().save(commit=False)
        # The view pins the order; keep the model column authoritative.
        request_obj.order = self._order
        if commit:
            request_obj.save()
        return request_obj
