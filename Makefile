# Local entry point (docs/verification.md), modeled on syrtis's Makefile. Run from the repo root.
# Build order matters: the Rust staticlib must exist before the Swift package links it.

.PHONY: rust build test test-rust test-golden test-python test-swift bundle installer selftest-bundled clean-bundle

SWIFT_OUT := macos/.build/out
# The bundle folders (docs/contracts/s3b.md section 13.1, app-sandbox.md section 2.3): APP_NAME for the shipping bundle ID,
# DEV_APP_NAME for any other (the default local build and the selftest's throwaway IDs). LEGACY_APP_NAME is the name before
# section 13, still cleaned up by clean-bundle.
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

# Everything, in the order it always ran. CLAUDE.md says which part a change runs before a commit. CI runs cargo test, swift test
# and only part of test-python: the experiments/ tests run only here, so any Python change runs test-python locally.
test: test-rust test-python test-swift

test-rust:
	cargo test --release --locked

# Only the golden comparisons (eval/golden/): enough for a change that only adds rows to eval/dev/.
test-golden:
	cargo test --release --locked -p cli --test golden

test-python:
	python3 -m unittest tools/test_build_acg_pack.py
	python3 -B -m unittest tools/test_pack_oov.py
	python3 -m unittest tools/test_build_names.py
	python3 -W ignore -m unittest tools/test_it_sources.py tools/test_it_wiki_counts.py tools/test_build_it_pack.py
	python3 -W ignore -m unittest tools/test_build_lm.py experiments/s2/test_extra_lexicon.py experiments/model-v5/test_compare_counts.py experiments/model-v5/test_count_lexicon.py
	python3 experiments/s2-classes/test_cluster.py
	python3 -m unittest discover -s experiments/s7a-tinylm -p test_export_sjtl.py
	python3 -W ignore -m unittest tools/test_kn_cont.py tools/test_extra_overlay.py
	python3 -W ignore -m unittest experiments/p1a-confusion/test_p1a.py
	python3 -W ignore -m unittest experiments/s7f-corpus/test_s7f.py experiments/s7a-tinylm/test_extra.py
	python3 -W ignore -m unittest tools/test_evalstats.py experiments/e2-chateval/test_e2.py experiments/e2-chateval/test_judge.py experiments/e2-chateval/test_d2c_split.py
	python3 -m unittest tools/test_bench.py
	python3 -W ignore -m unittest tools/test_kpi.py
	python3 -m unittest tools/test_build_english_words.py
	python3 -m unittest tools/test_build_overlay.py
	python3 -W ignore -m unittest tools/test_readings.py
	python3 -W ignore -m unittest experiments/sj-lm-7/test_ptt_clean.py experiments/sj-lm-7/test_clean_subs.py experiments/sj-lm-7/test_dedupe_eval.py experiments/sj-lm-7/test_moore_lewis.py
	python3 -W ignore -m unittest experiments/youtube-events/test_youtube_events.py
	python3 -W ignore -m unittest tools/test_oc_ref.py
	python3 -W ignore -m unittest tools/test_holdout_guard.py
	python3 -W ignore -m unittest tools/test_build_attribution.py
	python3 -W ignore -m unittest experiments/model-v6/test_force_classes.py
	python3 -W ignore -m unittest tools/test_triage.py
	python3 -W ignore -m unittest tools/test_build_trigram.py
	python3 -W ignore -m unittest tools/test_build_digits.py
	cd experiments/digits && python3 -W ignore -m unittest test_digits
	python3 -W ignore -m unittest tools/test_build_classes.py experiments/s2/test_expected.py experiments/s2/test_build_tune.py experiments/s2/test_trad_phrases.py
	cd experiments/sp && python3 -W ignore -m unittest test_predict test_predict2 test_predict3
	cd experiments/s7a-tinylm && python3 -W ignore -m unittest test_fusegrid test_runsets
	python3 -W ignore -m unittest experiments/x0c/test_x0c.py
	cd experiments/fuzzy && python3 -W ignore -m unittest test_fuzzy
	python3 -m unittest reference/proto/test_recombine.py
	python3 -W ignore -m unittest experiments/newword/test_hijack.py
	python3 -W ignore experiments/diag-near-miss/test_d23.py
	python3 -W ignore experiments/p4-colloquial/test_p4.py
	python3 -W ignore experiments/p4c-colloquial/test_p4c.py
	python3 -W ignore experiments/p4d-colloquial/test_p4d.py
	python3 -W ignore experiments/p5-reading-channel/test_p5.py

test-swift: rust
	@$(call relink_if_stale,Debug)
	@$(call rebuild_if_header_stale,Debug)
	swift test --package-path macos --force-resolved-versions

# build/$(DEV_APP_NAME) with the development bundle ID by default; build/$(APP_NAME) with
# `make bundle BUNDLE_ID=com.nyanako.inputmethod.shanjie` (scripts/build-app.sh reads BUNDLE_ID and OUT_DIR from the
# environment; make exports command-line variables to it). Both are sandboxed: build only, do not run them locally until CI
# has shown that a build without a migration list moves no data (app-sandbox.md section 5).
bundle: rust
	@$(call relink_if_stale,Release)
	@$(call rebuild_if_header_stale,Release)
	scripts/build-app.sh

# The installer (docs/contracts/s3c-installer.md section 3) around the existing build/$(APP_NAME), the shipping-ID build only
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
# selftest runs only in CI, in scripts/test-sandbox-migration.sh and check-app.sh (app-sandbox.md section 2.6). Nothing is
# installed or registered. The selftest itself writes no files or preferences, but its first run in the sandbox creates
# ~/Library/Containers/<that ID> (docs/contracts/s3b.md section 9). Until CI has shown that a build without a migration list
# moves no data, this is not run on a maintainer's machine (app-sandbox.md section 5).
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
