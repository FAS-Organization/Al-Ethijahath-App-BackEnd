from django.urls import path

from . import views

urlpatterns = [
    path('profile/', views.profile_view),
    path('sites/', views.sites_view),
    path('sites/<int:pk>/', views.site_detail_view),
]