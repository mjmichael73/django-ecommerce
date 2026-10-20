from django.shortcuts import redirect, render
from django.urls import reverse

from accounts.forms import RegisterForm
from ecommerce.csrf_rotation import login_with_csrf_rotation


def register(request):
    if request.user.is_authenticated:
        return redirect(reverse('accounts:profile'))
    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            login_with_csrf_rotation(request, user)
            return redirect(reverse('accounts:profile'))
    else:
        form = RegisterForm()
    return render(request, 'accounts/register.html', {'form': form})
