from django import forms

PRODUCT_QUANTITY_CHOICES = [(i, str(i)) for i in range(1, 21)]


class CartAddProductForm(forms.Form):
    quantity = forms.TypedChoiceField(
        label='Quantity',
        choices=PRODUCT_QUANTITY_CHOICES,
        coerce=int,
    )
    override = forms.BooleanField(
        required=False,
        initial=False,
        widget=forms.HiddenInput,
    )

    def __init__(self, *args, variants=None, **kwargs):
        """
        ``variants`` is optional: when a (truthy) queryset of variants is given
        the form gains a leading, optional variant selector; without it the
        form is exactly the historical quantity/override pair.
        """
        super().__init__(*args, **kwargs)
        if variants:
            self.fields['variant'] = forms.ModelChoiceField(
                queryset=variants,
                required=False,
                empty_label='Default',
                label='Variant',
            )
            self.order_fields(['variant'])
