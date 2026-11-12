# Local entry point (docs/spec/release-and-ci.md §7), modeled on syrtis's Makefile. Run from the repo root.
# Build order matters: the Rust staticlib must exist before the Swift package links it.

.PHONY: rust build test test-rust test-golden test-python test-swift bundle installer selftest-bundled clean-bundle

SWIFT_OUT := macos/.build/out
# The bundle folders (docs/spec/macos-shell.md §1.3, docs/spec/install-and-update.md §2.1, §2.6, §6.1): APP_NAME for the shipping bundle ID,
# DEV_APP_NAME for any other (the default local build and the selftest's throwaway IDs). LEGACY_APP_NAME is the earlier name,
# still cleaned up by clean-bundle.
APP_NAME := 善解輸入法.app
DEV_APP_NAME := 善解（開發版）.app
LEGACY_APP_NAME := shanjie.app
INSTALLER_NAME := 安裝善解輸入法.app
SHIPPING_ID := com.nyanako.inputmethod.shanjie
LSREGISTER := /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister

rust:
	cargo build --release --locked -p core

build: rust
	@$(call relink_if_stale,Debug)
	@$(call rebuild_if_header_stale,Debug)
	swift build --package-path macos --force-resolved-versions

# Everything, in the order it always ran. docs/spec/release-and-ci.md §7 says which part a change runs before a commit. CI runs cargo test, swift test
# and only part of test-python, so any Python change runs test-python locally.
test: test-rust test-python test-swift

test-rust:
	cargo test --release --locked

# Only the golden comparisons (eval/golden/): enough for a change that only adds rows to eval/dev/.
test-golden:
	cargo test --release --locked -p cli --test golden

# docs/spec/models.md §9.8: the Python tests use their own tw-clean tables, never the real one (not in the repo); off says so out loud.
test-python: export SHANJIE_TW_CLEAN = off
test-python:
	@if [ -z "$$SHANJIE_MOEDICT" ] || [ -z "$$SHANJIE_HETERO" ]; then echo "NOTE: SHANJIE_MOEDICT and SHANJIE_HETERO are not both set: the two real-CLI reading tests (test_build_acg_pack LiReading, test_build_quote_pack RealCli) will be skipped"; fi
	python3 -m unittest tools/test_build_forms.py
	python3 -m unittest tools/test_build_acg_pack.py
	python3 -m unittest tools/test_build_quote_pack.py
	python3 -B -m unittest tools/test_pack_oov.py
	python3 -m unittest tools/test_build_names.py
	python3 -m unittest tools/test_build_sandhi.py
	python3 -W ignore -m unittest tools/test_it_sources.py tools/test_it_wiki_counts.py tools/test_build_it_pack.py
	python3 -W ignore -m unittest tools/test_build_meme_pack.py
	python3 -W ignore -m unittest tools/test_build_beauty_pack.py
	python3 -W ignore -m unittest tools/test_gen_meme_embedded.py
	python3 tools/gen_meme_embedded.py --check eval/dev/meme/words-all.txt eval/dev/meme/embedded.txt
	python3 -W ignore -m unittest tools/test_build_lm.py tools/test_extra_lexicon.py
	python3 -m unittest tools/test_export_sjtl.py
	python3 -W ignore -m unittest tools/test_kn_cont.py tools/test_extra_overlay.py
	python3 -W ignore -m unittest tools/test_evalstats.py
	python3 -m unittest tools/test_bench.py
	python3 -m unittest tools/test_std_exempt.py
	python3 -W ignore -m unittest tools/test_kpi.py
	python3 -m unittest tools/test_build_english_words.py
	python3 -m unittest tools/test_build_overlay.py
	python3 -W ignore -m unittest tools/test_readings.py
	python3 -W ignore -m unittest tools/test_holdout_guard.py tools/test_merge_guard_rows.py
	python3 -W ignore -m unittest tools/test_build_attribution.py
	python3 -W ignore -m unittest tools/test_build_trigram.py
	python3 -W ignore -m unittest tools/test_build_digits.py
	cd reference/proto && python3 -W ignore -m unittest test_digits
	python3 -W ignore -m unittest tools/test_build_classes.py
	cd reference/proto && python3 -W ignore -m unittest test_predict test_predict2 test_predict3
	python3 -W ignore -m unittest reference/proto/test_fuzzy_ref.py
	python3 -m unittest reference/proto/test_recombine.py
	python3 reference/proto/test_variant_order.py
	python3 -W ignore -m unittest reference/proto/test_colloquial_ref.py
	# eval/ablation/v0.7.0/README.md: the offline gate row's rules, cases taken from the core's unit tests.
	python3 -B eval/ablation/v0.7.0/gate_offline.py --self-test
	# scripts/build-paper-html.sh: the numbering and the checks of the paper's reading page, on a synthetic paper.
	python3 -B scripts/paper_html.py --self-test > /dev/null
	# The tw-conv guard (docs/spec/models.md §9.10, §9.8), last so the other suites report first.
	python3 -W ignore -m unittest tools/test_tw_conv_guard.py

test-swift: rust
	@$(call relink_if_stale,Debug)
	@$(call rebuild_if_header_stale,Debug)
	swift test --package-path macos --force-resolved-versions

# build/$(DEV_APP_NAME) with the development bundle ID by default; build/$(APP_NAME) with
# `make bundle BUNDLE_ID=com.nyanako.inputmethod.shanjie` (scripts/build-app.sh reads BUNDLE_ID and OUT_DIR from the
# environment; make exports command-line variables to it). Both are sandboxed: build only, do not run them locally until CI
# has shown that a build without a migration list moves no data (docs/spec/install-and-update.md §2.6).
bundle: rust
	@$(call relink_if_stale,Release)
	@$(call rebuild_if_header_stale,Release)
	scripts/build-app.sh

# The installer (docs/spec/install-and-update.md §5.2, §7.1, §7.2) around the existing build/$(APP_NAME), the shipping-ID build only
# (run `make bundle BUNDLE_ID=com.nyanako.inputmethod.shanjie` first; not rebuilt here, so the app check-app.sh verified is
# the one wrapped), zipped the way release.yml zips it. Before packaging, the bundle must carry the shipping ID and pass
# scripts/check-entitlements.sh. Never launched; scripts/check-installer.sh inspects it.
installer:
	@[ -d 'build/$(APP_NAME)' ] || { echo "error: build/$(APP_NAME) is missing; run make bundle BUNDLE_ID=com.nyanako.inputmethod.shanjie first (the installer wraps only the shipping-ID build)" >&2; exit 1; }
	@[ "$$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' 'build/$(APP_NAME)/Contents/Info.plist' 2>/dev/null)" = '$(SHIPPING_ID)' ] || { echo "error: build/$(APP_NAME) does not have the bundle ID $(SHIPPING_ID); run make bundle BUNDLE_ID=$(SHIPPING_ID)" >&2; exit 1; }
	scripts/check-entitlements.sh 'build/$(APP_NAME)'
	@V=$$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' 'build/$(APP_NAME)/Contents/Info.plist'); \
	rm -f build/shanjie-$$V.zip; \
	ditto -c -k --keepParent 'build/$(APP_NAME)' build/shanjie-$$V.zip; \
	SHANJIE_VERSION=$$V scripts/build-installer.sh build build/shanjie-$$V.zip

# The selftest from the configuration that ships: release, sandboxed, inside a .app, run from the bundle's own Resources. The
# bundle ID is the one knob and is always a throwaway ID with no migration list (CI passes one per run); the shipping ID's
# selftest runs only in CI, in scripts/test-sandbox-migration.sh and check-app.sh (docs/spec/install-and-update.md §10.2). Nothing is
# installed or registered. The selftest itself writes no files or preferences, but its first run in the sandbox creates
# ~/Library/Containers/<that ID> (docs/spec/macos-shell.md §6). Until CI has shown that a build without a migration list
# moves no data, this is not run on a developer machine (docs/spec/install-and-update.md §2.6).
SELFTEST_BUNDLE_ID ?= com.nyanako.inputmethod.shanjie.selftest
# The folder the ID builds into, by the same rule as scripts/build-app.sh (the shipping ID, else the development name).
SELFTEST_APP = build/selftest/$(if $(filter $(SHIPPING_ID),$(SELFTEST_BUNDLE_ID)),$(APP_NAME),$(DEV_APP_NAME))
selftest-bundled: rust
	@$(call relink_if_stale,Release)
	@$(call rebuild_if_header_stale,Release)
	BUNDLE_ID=$(SELFTEST_BUNDLE_ID) OUT_DIR=build/selftest scripts/build-app.sh
	'$(SELFTEST_APP)/Contents/MacOS/shanjie' --selftest

# Unregisters the local bundles, under the current and the legacy name, from LaunchServices
# (errors ignored: usually never registered or not there) and deletes them, with the entitlement files build-app.sh writes
# next to them. Every OUT_DIR used here and in ci.yml: build, build/selftest, build/default, build/nomanifest. Touches
# nothing else.
CLEAN_DIRS := build build/selftest build/default build/nomanifest
CLEAN_APPS := $(foreach d,$(CLEAN_DIRS),$(d)/$(APP_NAME) $(d)/$(DEV_APP_NAME) $(d)/$(LEGACY_APP_NAME)) build/$(INSTALLER_NAME)
clean-bundle:
	@for a in $(CLEAN_APPS); do \
		echo "$(LSREGISTER) -u $$a"; $(LSREGISTER) -u "$$a" 2>/dev/null || true; \
	done
	rm -rf $(CLEAN_APPS) build/shanjie-*.zip
	rm -f $(foreach d,$(CLEAN_DIRS),$(d)/.entitlements-*.plist)

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
