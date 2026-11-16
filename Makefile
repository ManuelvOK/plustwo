# Installs plustwo, with git-change as a second name. The default is a system-wide install below
# /usr/local. Use PREFIX=$(HOME)/.local for a per-user one.
#
#   make check          compiles the sources, checks the layers' imports,
#                       the types (mypy) and the style (tools/check_style.py)
#   make test           checks the diff rendering against git and delta.
#                       TEST_REPOS=a:b also checks the history of a and b,
#                       which are only read.
#   make install        copies the launcher, the package and the guide to
#                       $(LIBDIR), links plustwo and git-change into $(BINDIR)
#   make link           links plustwo in this checkout into $(BINDIR), under
#                       both names, for development
#   make uninstall      removes what install or link put there

PREFIX = /usr/local
BINDIR = $(PREFIX)/bin
LIBDIR = $(PREFIX)/lib/plustwo

# The launcher plustwo finds the package in src/ next to its real path,
# following symbolic links, and the guide (AGENTS.md) next to itself. So
# the files stay together and bin only gets links. git-change is the old
# name, which hooks of older repositories call.
SOURCES = $(sort $(shell find src -name '*.py' -not -path '*/__pycache__/*')) $(TOOLS)
TOOLS = tools/check_layers.py tools/check_style.py
# The layers the rule pass has reached, checked for style and full types
STYLED_LAYERS = git config model diff review author cli tui web
STYLED = plustwo $(TOOLS) $(wildcard src/plustwo/*.py) \
  $(foreach layer,$(STYLED_LAYERS),$(wildcard src/plustwo/$(layer)/*.py))
PAGE = $(wildcard src/plustwo/web/page/*)
NAMES = plustwo git-change

.PHONY: all check test install link uninstall

all: check

check:
	@echo 'compile'
	@python3 -c 'import sys; [compile(open(f).read(), f, "exec") for f in sys.argv[1:]]' \
	  plustwo $(SOURCES)
	@echo 'layers'
	@python3 tools/check_layers.py docs/architecture.md src/plustwo
	@echo 'mypy'
	@mypy --config-file mypy.ini --no-error-summary src/plustwo $(TOOLS)
	@echo 'style'
	@python3 tools/check_style.py $(STYLED)

# The tests get state and settings directories of their own, so they never
# register scratch repositories with the user's dashboard.
test: check
	XDG_STATE_HOME=$(CURDIR)/.test/state XDG_CONFIG_HOME=$(CURDIR)/.test/config \
	  python3 -m unittest discover -s tests

install: check
	install -Dm755 plustwo $(DESTDIR)$(LIBDIR)/plustwo
	for file in $(filter src/%,$(SOURCES)) $(PAGE); do \
	  install -Dm644 $$file $(DESTDIR)$(LIBDIR)/$$file; \
	done
	install -Dm644 AGENTS.md $(DESTDIR)$(LIBDIR)/AGENTS.md
	install -d $(DESTDIR)$(BINDIR)
	for name in $(NAMES); do \
	  ln -sf $(LIBDIR)/plustwo $(DESTDIR)$(BINDIR)/$$name; \
	done

link: check
	install -d $(BINDIR)
	for name in $(NAMES); do \
	  ln -sf $(CURDIR)/plustwo $(BINDIR)/$$name; \
	done

uninstall:
	for name in $(NAMES); do \
	  rm -f $(DESTDIR)$(BINDIR)/$$name; \
	done
	rm -rf $(DESTDIR)$(LIBDIR)
