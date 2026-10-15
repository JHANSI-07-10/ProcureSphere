from django.urls import path

from .views import change_password_view, login_page_view, logout_page_view

app_name = "accounts"

urlpatterns = [
    path("login/", login_page_view, name="login"),
    path("logout/", logout_page_view, name="logout"),
    path("accounts/change-password/", change_password_view, name="change_password"),
]
