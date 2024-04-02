from django.shortcuts import render


def payment_canceled(request):
    return render(request, 'payment/canceled.html')
