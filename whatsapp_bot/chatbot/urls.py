from django.urls import path, re_path
from chatbot import views

urlpatterns = [
    re_path(r'^webhook/?$', views.webhook, name='webhook'),
]
