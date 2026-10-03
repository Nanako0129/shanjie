# Local entry point (docs/verification.md), modeled on syrtis's Makefile. Run from the repo root.
# Build order matters: the Rust staticlib must exist before the Swift package links it.

.PHONY: rust build test bundle selftest-bundled clean-bundle

SWIFT_OUT := macos/.build/out
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

# The shipping bundle: build/shanjie.app with the shipping bundle ID.
bundle: rust
	@$(call relink_if_stale,Release)
	@$(call rebuild_if_header_stale,Release)
	scripts/build-app.sh

# The selftest from the configuration that ships: release, inside a .app, run from the bundle's
# own Resources. The bundle ID is the one knob. Locally it defaults to a throwaway ID, the WEAKER
# gate: a value keyed on the shipping ID itself would not be exercised. CI passes an empty value,
# which means build-app.sh's default, i.e. the shipping ID, deliberately not spelled out again
# here so a rename has one place to change. Nothing is installed or registered either way; the
# selftest writes no files or preferences (docs/contracts/s3b.md section 9).
SELFTEST_BUNDLE_ID ?= com.nyanako.inputmethod.shanjie.selftest
selftest-bundled: rust
	@$(call relink_if_stale,Release)
	@$(call rebuild_if_header_stale,Release)
	BUNDLE_ID=$(SELFTEST_BUNDLE_ID) OUT_DIR=build/selftest scripts/build-app.sh
	build/selftest/shanjie.app/Contents/MacOS/shanjie --selftest

# Unregisters the two local bundles from LaunchServices (errors ignored: usually never
# registered) and deletes them. Touches nothing else.
clean-bundle:
	-$(LSREGISTER) -u build/shanjie.app 2>/dev/null
	-$(LSREGISTER) -u build/selftest/shanjie.app 2>/dev/null
	rm -rf build/shanjie.app build/selftest/shanjie.app

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
