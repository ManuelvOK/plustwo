# Installs plustwo, with git-change as a second name. The default is a system-wide install below
# /usr/local. Use PREFIX=$(HOME)/.local for a per-user one.
#
#   make check          compiles the sources, finds syntax errors
#   make test           checks the diff rendering against git and delta.
#                       TEST_REPOS=a:b also checks the history of a and b,
#                       which are only read.
#   make install        copies the files to $(LIBDIR), links plustwo and
#                       git-change into $(BINDIR)
#   make link           links plustwo in this checkout into $(BINDIR), under
#                       both names, for development
#   make uninstall      removes what install or link put there

PREFIX = /usr/local
BINDIR = $(PREFIX)/bin
LIBDIR = $(PREFIX)/lib/plustwo

# plustwo imports the interfaces and reads the guide (AGENTS.md) from the
# directory it really lives in, following symbolic links, the web interface
# its page from web/ there. So the files stay together and bin only gets
# links. git-change is the old name, which hooks of older repositories call.
SOURCES = plustwo plustwo_tui.py plustwo_web.py
WEB = web/index.html web/app.js web/style.css
NAMES = plustwo git-change

.PHONY: all check test install link uninstall

all: check

check:
	python3 -c 'import sys; [compile(open(f).read(), f, "exec") for f in sys.argv[1:]]' $(SOURCES)

test: check
	python3 -m unittest discover -s tests

install: check
	install -Dm755 plustwo $(DESTDIR)$(LIBDIR)/plustwo
	install -Dm644 plustwo_tui.py $(DESTDIR)$(LIBDIR)/plustwo_tui.py
	install -Dm644 plustwo_web.py $(DESTDIR)$(LIBDIR)/plustwo_web.py
	install -Dm644 -t $(DESTDIR)$(LIBDIR)/web $(WEB)
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
