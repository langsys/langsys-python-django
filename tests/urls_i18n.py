"""A URLconf that routes by language prefix, for the SRV-6 path-locale test."""

from django.conf.urls.i18n import i18n_patterns
from django.urls import path

from tests.test_request_locale import where_am_i

urlpatterns = i18n_patterns(path("where/", where_am_i))
