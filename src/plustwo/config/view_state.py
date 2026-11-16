# SPDX-License-Identifier: Zlib
"""What the interfaces remember: dashboard sections, follow latest."""

from plustwo.config.state import set_state, state_values


# What the tool remembers, as opposed to what the user configures: files in
# git config syntax below $XDG_STATE_HOME/plustwo, read and written with
# git config --file.
DASHBOARD_SECTIONS = ('your-turn', 'drafts', 'wip', 'outgoing', 'incoming')


SECTION_TITLES = {'your-turn': 'Your turn', 'drafts': 'Has drafts', 'wip': 'Work in progress',
                  'outgoing': 'Outgoing', 'incoming': 'Incoming'}


def dashboard_order():
    """The dashboard's sections in the user's order. Unknown names are
    left out, sections missing from the saved order come at the end.
    """
    saved = [name for value in state_values('dashboard', 'dashboard.sections')
             for name in value.split(',') if name in DASHBOARD_SECTIONS]
    return saved + [name for name in DASHBOARD_SECTIONS if name not in saved]


def move_section(name, steps):
    """Moves a dashboard section up (steps < 0) or down and saves the order."""
    order = dashboard_order()
    index = order.index(name)
    target = max(0, min(index + steps, len(order) - 1))
    order.insert(target, order.pop(index))
    set_state('dashboard', 'dashboard.sections', ','.join(order))
    return order


def set_section_order(order):
    """Saves a whole order, e.g. from dragging in the web interface."""
    order = [name for name in order if name in DASHBOARD_SECTIONS]
    set_state('dashboard', 'dashboard.sections',
              ','.join(order + [name for name in DASHBOARD_SECTIONS if name not in order]))


def folded_sections():
    """The dashboard sections the user folded."""
    return {name for value in state_values('dashboard', 'dashboard.folded')
            for name in value.split(',') if name}


def toggle_section(name):
    """Folds or unfolds a dashboard section. Returns whether it is folded."""
    folded = folded_sections() ^ {name}
    set_state('dashboard', 'dashboard.folded', ','.join(sorted(folded)) or None)
    return name in folded


def follows_latest():
    """Whether a right side on the latest patch set moves to a new upload,
    the interfaces' switch, off by default. State, not a setting, since the
    interfaces change it.
    """
    return ['true'] == state_values('view', 'view.follow-latest')[-1:]


def set_follows_latest(on):
    """Turns follows_latest() on or off, for later sessions too."""
    set_state('view', 'view.follow-latest', 'true' if on else None)
