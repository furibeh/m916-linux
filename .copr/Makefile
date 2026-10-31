# Build a source RPM from the current checkout.
# COPR ("make_srpm" method): make -f .copr/Makefile srpm outdir=DIR spec=SPEC
# Locally: make -f .copr/Makefile srpm outdir=/some/dir
# COPR runs this as root in a bare chroot, so the tools are installed first and
# everything else is computed by the shell afterwards, not by make up front.
TOP := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)
spec ?= $(TOP)/packaging/m916-linux.spec
outdir ?= $(TOP)
NAME := m916-linux

srpm:
	command -v git >/dev/null && command -v rpmbuild >/dev/null || dnf -y install git rpm-build
	set -e; \
	version=$$(rpmspec -q --srpm --qf '%{version}' $(spec)); \
	src=$$(mktemp -d); \
	git -c safe.directory='*' -C $(TOP) archive --format=tar.gz \
	    --prefix=$(NAME)-$$version/ -o $$src/$(NAME)-$$version.tar.gz HEAD; \
	rpmbuild -bs --define "_sourcedir $$src" --define "_srcrpmdir $(outdir)" $(spec); \
	rm -rf $$src

.PHONY: srpm
