"""
Access control for facilitator-only views.

The student surfaces (join, board, console, state.json) are public. The facilitator
dashboard and every round-control action require a logged-in staff user — i.e. the
instructor, who signs in through the Django admin login. This closes the gap flagged
during the Phase 3 build, where anyone with the dashboard URL could drive the rounds.
"""
from django.contrib.auth.decorators import user_passes_test


def _is_facilitator(user):
    return user.is_active and user.is_staff


# Redirects anonymous or non-staff users to the admin login page.
facilitator_required = user_passes_test(_is_facilitator, login_url="/admin/login/")
