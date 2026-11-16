# SPDX-License-Identifier: Zlib
"""The changes of all registered repositories."""

from plustwo.config.user import current_user
from plustwo.config.view_state import dashboard_order
from plustwo.model.change import Change


def dashboard(changes: list[Change], user: str | None = None
              ) -> list[tuple[str, list[tuple[int, Change]]]]:
    """Sorts changes into the sections of a dashboard like Gerrit's. A
    change shows in every section it belongs to.

    Args:
        changes: the changes, in overview order.
        user: the user the dashboard is for, None for current_user().

    Returns:
        (section, [(number, change)]) per section in the user's order,
        empty sections too. The number is the change's place in changes,
        from 1.
    """
    user = user or current_user()
    numbered = list(enumerate(changes, start=1))
    return [(section, [(number, change) for number, change in numbered
                       if section in change.sections(user)])
            for section in dashboard_order()]
