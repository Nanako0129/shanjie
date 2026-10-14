# Local entry point (docs/verification.md), modeled on syrtis's Makefile. Run from the repo root.
# Build order matters: the Rust staticlib must exist before the Swift package links it.

.PHONY: rust build test test-rust test-golden test-python test-swift bundle installer selftest-bundled clean-bundle

SWIFT_OUT := macos/.build/out
# The bundle folder (docs/contracts/s3b.md section 13.1). LEGACY_APP_NAME is the name before
# section 13, still cleaned up by clean-bundle.
APP_NAME := 善解輸入法.app
LEGACY_APP_NAME := shanjie.app
INSTALLER_NAME := 安裝善解輸入法.app
LSREGISTER := /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister

rust:
	cargo build --release --locked -p core

build: rust
	@$(call relink_if_stale,Debug)
	@$(call rebuild_if_header_stale,Debug)
	swift build --package-path macos

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
	python3 -m unittest tools/test_build_names.py
	python3 -W ignore -m unittest tools/test_build_lm.py experiments/s2/test_extra_lexicon.py experiments/model-v5/test_compare_counts.py experiments/model-v5/test_count_lexicon.py
	python3 experiments/s2-classes/test_cluster.py
	python3 -m unittest discover -s experiments/s7a-tinylm -p test_export_sjtl.py
	python3 -W ignore -m unittest tools/test_kn_cont.py tools/test_extra_overlay.py
	python3 -W ignore -m unittest experiments/p1a-confusion/test_p1a.py
	python3 -W ignore -m unittest experiments/s7f-corpus/test_s7f.py experiments/s7a-tinylm/test_extra.py
	python3 -W ignore -m unittest tools/test_evalstats.py experiments/e2-chateval/test_e2.py experiments/e2-chateval/test_judge.py experiments/e2-chateval/test_d2c_split.py
	python3 -m unittest tools/test_bench.py
	python3 -m unittest tools/test_build_english_words.py
	python3 -m unittest tools/test_build_overlay.py
	python3 -W ignore -m unittest experiments/model-v6/test_force_classes.py
	python3 -W ignore -m unittest tools/test_triage.py
	python3 -W ignore -m unittest tools/test_build_classes.py experiments/s2/test_expected.py
	cd experiments/sp && python3 -W ignore -m unittest test_predict test_predict2 test_predict3
	cd experiments/s7a-tinylm && python3 -W ignore -m unittest test_fusegrid test_runsets
	python3 -W ignore -m unittest experiments/x0c/test_x0c.py
	cd experiments/fuzzy && python3 -W ignore -m unittest test_fuzzy
	python3 -m unittest reference/proto/test_recombine.py

test-swift: rust
	@$(call relink_if_stale,Debug)
	@$(call rebuild_if_header_stale,Debug)
	swift test --package-path macos

# The shipping bundle: build/$(APP_NAME) with the shipping bundle ID.
bundle: rust
	@$(call relink_if_stale,Release)
	@$(call rebuild_if_header_stale,Release)
	scripts/build-app.sh

# The installer (docs/contracts/s3c-installer.md section 3) around the existing build/$(APP_NAME)
# (run `make bundle` first; not rebuilt here, so the app check-app.sh verified is the one wrapped),
# zipped the way release.yml zips it. Never launched; scripts/check-installer.sh inspects it.
installer:
	@[ -d 'build/$(APP_NAME)' ] || { echo "error: build/$(APP_NAME) is missing; run make bundle first" >&2; exit 1; }
	@V=$$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' 'build/$(APP_NAME)/Contents/Info.plist'); \
	rm -f build/shanjie-$$V.zip; \
	ditto -c -k --keepParent 'build/$(APP_NAME)' build/shanjie-$$V.zip; \
	SHANJIE_VERSION=$$V scripts/build-installer.sh build build/shanjie-$$V.zip

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
	'build/selftest/$(APP_NAME)/Contents/MacOS/shanjie' --selftest

# Unregisters the local bundles, under the current and the legacy name, from LaunchServices
# (errors ignored: usually never registered or not there) and deletes them. Touches nothing else.
CLEAN_APPS := $(foreach d,build build/selftest,$(d)/$(APP_NAME) $(d)/$(LEGACY_APP_NAME)) build/$(INSTALLER_NAME)
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
