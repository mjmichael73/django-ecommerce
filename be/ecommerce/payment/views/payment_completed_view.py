from django.shortcuts import render


def payment_completed(request):
    return render(request, 'payment/completed.html')
