from django.urls import path
from apps.assistant import source_grant_views as views

urlpatterns = [
    path("settings/assistant-sources", views.settings_page, name="studio-assistant-source-grants"),
    path("settings/assistant-sources/sources", views.sources, name="studio-assistant-source-choices"),
    path("settings/assistant-sources/principals", views.principals, name="studio-assistant-source-principals"),
]
