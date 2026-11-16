# SPDX-License-Identifier: Zlib
"""The changes of all registered repositories."""

from plustwo.config.user import current_user
from plustwo.config.view_state import dashboard_order


def dashboard(changes, user=None):
    """The dashboard like Gerrit's: [(section, [(number, change)])] in the
    user's order, every section, also empty ones. A change shows in every
    section it belongs to. number is its place in changes, from 1.
    """
    user = user or current_user()
    numbered = list(enumerate(changes, start=1))
    return [(section, [(number, change) for number, change in numbered
                       if section in change.sections(user)])
            for section in dashboard_order()]
