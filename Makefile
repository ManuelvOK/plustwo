# Installs git change. The default is a system-wide install below
# /usr/local. Use PREFIX=$(HOME)/.local for a per-user one.
#
#   make check          compiles the sources, finds syntax errors
#   make install        copies the files to $(LIBDIR), links git-change
#                       into $(BINDIR)
#   make link           links git-change in this checkout into $(BINDIR),
#                       for development
#   make uninstall      removes what install or link put there

PREFIX = /usr/local
BINDIR = $(PREFIX)/bin
LIBDIR = $(PREFIX)/lib/plustwo

# git-change imports the interface and reads the guide (AGENTS.md) from the
# directory it really lives in, following symbolic links. So the files stay
# together and bin only gets a link.
SOURCES = git-change git_change_tui.py

.PHONY: all check install link uninstall

all: check

check:
	python3 -c 'import sys; [compile(open(f).read(), f, "exec") for f in sys.argv[1:]]' $(SOURCES)

install: check
	install -Dm755 git-change $(DESTDIR)$(LIBDIR)/git-change
	install -Dm644 git_change_tui.py $(DESTDIR)$(LIBDIR)/git_change_tui.py
	install -Dm644 AGENTS.md $(DESTDIR)$(LIBDIR)/AGENTS.md
	install -d $(DESTDIR)$(BINDIR)
	ln -sf $(LIBDIR)/git-change $(DESTDIR)$(BINDIR)/git-change

link: check
	install -d $(BINDIR)
	ln -sf $(CURDIR)/git-change $(BINDIR)/git-change

uninstall:
	rm -f $(DESTDIR)$(BINDIR)/git-change
	rm -rf $(DESTDIR)$(LIBDIR)
