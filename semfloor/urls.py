"""
Root URL configuration.

Admin lives under /admin/; everything else is handled by the game app (join screen,
big board, team console, facilitator dashboard, and the state.json polling endpoint).
"""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("game.urls")),
]
