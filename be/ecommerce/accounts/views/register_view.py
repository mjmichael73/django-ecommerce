from django.contrib.auth import login
from django.shortcuts import redirect, render
from django.urls import reverse

from accounts.forms import RegisterForm


def register(request):
    if request.user.is_authenticated:
        return redirect(reverse('accounts:profile'))
    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect(reverse('accounts:profile'))
    else:
        form = RegisterForm()
    return render(request, 'accounts/register.html', {'form': form})
