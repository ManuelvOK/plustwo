# SPDX-License-Identifier: Zlib
"""What the interfaces remember: dashboard sections, follow latest."""

from plustwo.config.state import set_state, state_values


# The dashboard's sections, in their default order
_DASHBOARD_SECTIONS = ('your-turn', 'drafts', 'wip', 'outgoing', 'incoming')

# Each dashboard section, mapped to its title
SECTION_TITLES = {'your-turn': 'Your turn', 'drafts': 'Has drafts', 'wip': 'Work in progress',
                  'outgoing': 'Outgoing', 'incoming': 'Incoming'}


def dashboard_order() -> list[str]:
    """Reads the order of the dashboard's sections.

    Returns:
        The sections in the user's order. Unknown names are left out,
        sections missing from the saved order come at the end.
    """
    saved = [name for value in state_values('dashboard', 'dashboard.sections')
             for name in value.split(',') if name in _DASHBOARD_SECTIONS]
    return saved + [name for name in _DASHBOARD_SECTIONS if name not in saved]


def move_section(name: str, n_steps: int) -> list[str]:
    """Moves a dashboard section and saves the order.

    Args:
        name: the section's name.
        n_steps: how far to move it: down if positive, up if negative.

    Returns:
        The new order.
    """
    order = dashboard_order()
    index = order.index(name)
    target = max(0, min(index + n_steps, len(order) - 1))
    order.insert(target, order.pop(index))
    set_state('dashboard', 'dashboard.sections', ','.join(order))
    return order


def set_section_order(order: list[str]) -> None:
    """Saves a whole order, as from dragging in the web interface.

    Args:
        order: section names. Unknown ones are left out, missing ones go
            to the end.
    """
    order = [name for name in order if name in _DASHBOARD_SECTIONS]
    set_state('dashboard', 'dashboard.sections',
              ','.join(order + [name for name in _DASHBOARD_SECTIONS if name not in order]))


def folded_sections() -> set[str]:
    """Reads which dashboard sections the user folded.

    Returns:
        The folded sections' names.
    """
    return {name for value in state_values('dashboard', 'dashboard.folded')
            for name in value.split(',') if name}


def toggle_section(name: str) -> bool:
    """Folds or unfolds a dashboard section.

    Args:
        name: the section's name.

    Returns:
        Whether the section is folded now.
    """
    folded = folded_sections() ^ {name}
    set_state('dashboard', 'dashboard.folded', ','.join(sorted(folded)) or None)
    return name in folded


def follows_latest() -> bool:
    """Reads the interfaces' follow-latest switch, off by default. It is
    state, not a setting, since the interfaces change it.

    Returns:
        Whether a right side on the latest patch set moves to a new upload.
    """
    return ['true'] == state_values('view', 'view.follow-latest')[-1:]


def set_follows_latest(do_follow: bool) -> None:
    """Turns follows_latest() on or off, for later sessions too.

    Args:
        do_follow: whether a right side on the latest patch set moves to a
            new upload.
    """
    set_state('view', 'view.follow-latest', 'true' if do_follow else None)
