# Local entry point (docs/verification.md), modeled on syrtis's Makefile. Run from the repo root.
# Build order matters: the Rust staticlib must exist before the Swift package links it.

.PHONY: rust build test bundle installer selftest-bundled clean-bundle

SWIFT_OUT := macos/.build/out
# The bundle folders (docs/contracts/s3b.md section 13.1, app-sandbox.md section 2.3): APP_NAME for
# the shipping bundle ID, DEV_APP_NAME for any other (the default local build and the selftest's
# throwaway IDs). LEGACY_APP_NAME is the name before section 13, still cleaned up by clean-bundle.
APP_NAME := 善解輸入法.app
DEV_APP_NAME := 善解（開發版）.app
LEGACY_APP_NAME := shanjie.app
INSTALLER_NAME := 安裝善解輸入法.app
LSREGISTER := /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister

rust:
	cargo build --release --locked -p core

build: rust
	@$(call relink_if_stale,Debug)
	@$(call rebuild_if_header_stale,Debug)
	swift build --package-path macos

test: rust
	@$(call relink_if_stale,Debug)
	@$(call rebuild_if_header_stale,Debug)
	cargo test --release --locked
	swift test --package-path macos

# build/$(DEV_APP_NAME) with the development bundle ID by default; build/$(APP_NAME) with
# `make bundle BUNDLE_ID=com.nyanako.inputmethod.shanjie` (scripts/build-app.sh reads BUNDLE_ID and
# OUT_DIR from the environment; make exports command-line variables). Both are sandboxed.
bundle: rust
	@$(call relink_if_stale,Release)
	@$(call rebuild_if_header_stale,Release)
	scripts/build-app.sh

# The installer (docs/contracts/s3c-installer.md section 3) around the existing build/$(APP_NAME),
# the shipping-ID build only (run `make bundle BUNDLE_ID=com.nyanako.inputmethod.shanjie` first; not
# rebuilt here, so the app check-app.sh verified is the one wrapped), zipped the way release.yml
# zips it. Never launched; scripts/check-installer.sh inspects it.
installer:
	@[ -d 'build/$(APP_NAME)' ] || { echo "error: build/$(APP_NAME) is missing; run make bundle BUNDLE_ID=com.nyanako.inputmethod.shanjie first (the installer wraps only the shipping-ID build)" >&2; exit 1; }
	@V=$$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' 'build/$(APP_NAME)/Contents/Info.plist'); \
	rm -f build/shanjie-$$V.zip; \
	ditto -c -k --keepParent 'build/$(APP_NAME)' build/shanjie-$$V.zip; \
	SHANJIE_VERSION=$$V scripts/build-installer.sh build build/shanjie-$$V.zip

# The selftest from the configuration that ships: release, sandboxed, inside a .app, run from the
# bundle's own Resources. The bundle ID is the one knob and is always a throwaway ID with no
# migration list (CI passes one per run); the shipping ID's selftest runs only in CI, in
# scripts/test-sandbox-migration.sh and check-app.sh (app-sandbox.md section 2.6). Nothing is
# installed or registered. The selftest itself writes no files or preferences, but its first run in
# the sandbox creates ~/Library/Containers/<that ID> (docs/contracts/s3b.md section 9). Until CI has
# shown that a build without a migration list moves no data, this is not run on a maintainer's
# machine (app-sandbox.md section 5).
SELFTEST_BUNDLE_ID ?= com.nyanako.inputmethod.shanjie.selftest
selftest-bundled: rust
	@$(call relink_if_stale,Release)
	@$(call rebuild_if_header_stale,Release)
	BUNDLE_ID=$(SELFTEST_BUNDLE_ID) OUT_DIR=build/selftest scripts/build-app.sh
	'build/selftest/$(DEV_APP_NAME)/Contents/MacOS/shanjie' --selftest

# Unregisters the local bundles, under the current and the legacy name, from LaunchServices
# (errors ignored: usually never registered or not there) and deletes them. Touches nothing else.
CLEAN_APPS := $(foreach d,build build/selftest,$(d)/$(APP_NAME) $(d)/$(DEV_APP_NAME) $(d)/$(LEGACY_APP_NAME)) build/$(INSTALLER_NAME)
clean-bundle:
	@for a in $(CLEAN_APPS); do \
		echo "$(LSREGISTER) -u $$a"; $(LSREGISTER) -u "$$a" 2>/dev/null || true; \
	done
	rm -rf $(CLEAN_APPS) build/shanjie-*.zip

# SwiftPM may not treat the Rust staticlib as an input. Measured 2026-10-03 with Swift 6.4: it
# does relink when libcore.a changes, so this guard is a backstop for other toolchains; it costs
# one stat. The tests link the archive too.
define relink_if_stale
	if [ target/release/libcore.a -nt $(SWIFT_OUT)/Products/$(1)/Shanjie ]; then \
		rm -f $(SWIFT_OUT)/Products/$(1)/Shanjie; \
		rm -rf $(SWIFT_OUT)/Products/$(1)/ShanjieKitTests.xctest; \
	fi
endef

# The C header is imported through the CShanjie Clang module, whose precompiled form is cached;
# a header-only change could compile against the previous declarations. Drop the module caches
# and the build products of every target that imports it. (A content change could not be
# measured without editing core/; a timestamp-only touch triggers no recompile.)
define rebuild_if_header_stale
	if [ core/include/shanjie.h -nt $(SWIFT_OUT)/Products/$(1)/Shanjie ]; then \
		rm -rf $(SWIFT_OUT)/ModuleCache.noindex \
			$(SWIFT_OUT)/Intermediates.noindex/SwiftExplicitPrecompiledModules \
			$(SWIFT_OUT)/Intermediates.noindex/ExplicitPrecompiledModules \
			$(SWIFT_OUT)/Intermediates.noindex/Shanjie.build/$(1) \
			$(SWIFT_OUT)/Products/$(1)/Shanjie $(SWIFT_OUT)/Products/$(1)/ShanjieKitTests.xctest; \
	fi
endef
