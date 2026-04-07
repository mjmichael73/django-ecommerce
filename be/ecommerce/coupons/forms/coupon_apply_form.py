from django import forms


class CouponApplyForm(forms.Form):
    code = forms.CharField(
        label='Coupon code',
        widget=forms.TextInput(
            attrs={
                'placeholder': 'Enter code',
                'autocomplete': 'off',
            }
        ),
    )
