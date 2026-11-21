from django.apps import AppConfig


class WebConfig(AppConfig):
    name = 'web'

    def ready(self):
        from . import portal_events  # Register customer-facing workflow events.
