# Build a source RPM from the current checkout.
# COPR ("make_srpm" method): make -f .copr/Makefile srpm outdir=DIR spec=SPEC
# Locally: make -f .copr/Makefile srpm outdir=/some/dir
TOP := $(shell git rev-parse --show-toplevel 2>/dev/null || pwd)
spec ?= $(TOP)/packaging/m916-linux.spec
outdir ?= $(TOP)
NAME := m916-linux

srpm:
	command -v git >/dev/null && command -v rpmbuild >/dev/null || dnf -y install git rpm-build
	$(eval VERSION := $(shell rpmspec -q --srpm --qf '%{version}' $(spec)))
	$(eval SRC := $(shell mktemp -d))
	git -c safe.directory='*' -C $(TOP) archive --format=tar.gz \
	    --prefix=$(NAME)-$(VERSION)/ -o $(SRC)/$(NAME)-$(VERSION).tar.gz HEAD
	rpmbuild -bs --define "_sourcedir $(SRC)" --define "_srcrpmdir $(outdir)" $(spec)
	rm -rf $(SRC)

.PHONY: srpm
