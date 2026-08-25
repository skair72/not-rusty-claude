# not-rusty-claude - one entry point for the extract -> rewrite -> run pipeline.
#
# A bare `make` prints help and changes nothing. The first-run path is:
#
#     make setup     private Bun 1.3.14, sha256-verified, not on PATH
#     make binary    this platform's Claude Code binary, sha256-verified
#     make build     extract + rewrite into build/extract
#     make smoke     run the artifact once
#     make test      the pytest suite
#
# or `make first-run`, which is those five in that order.
#
# NOTHING here installs onto PATH, edits a shell profile, or creates a file
# named `claude`: a `claude` on PATH would shadow the real CLI, which is the
# same reason scripts/build.sh installs nothing.
#
# WHERE THINGS GO
#   ~/.bun-1.3.14/bun                  the private Bun (tests/conftest.py looks
#                                      here by default, so `make setup` makes
#                                      `make test`'s bun row light up)
#   ~/.cache/not-rusty-claude/         downloaded Claude binaries (~325 MB each)
#   ./build/extract/                   the artifacts
#   Only ./build is touched inside the repo, and only `make distclean` deletes
#   the two directories under $HOME.
#
# COMPATIBILITY - the reason this file looks plainer than it could
#   macOS ships GNU Make 3.81 (2006), so this is written in that dialect: no
#   .ONESHELL, no `::=`, no `!=` shell assignment, no .RECIPEPREFIX, no
#   grouped (&:) targets, no 4.x-only functions. Developed and run against GNU
#   Make 4.3 on Linux; the 3.81 constraints are enforced by
#   tests/test_makefile.py, not by having been executed on 3.81 here.
#
#   BSD vs GNU userland: macOS has no sha256sum and no timeout(1), and BSD
#   sed/stat/du take different flags. So checksumming, JSON parsing and the
#   smoke test's watchdog all go through python3 - already a hard dependency of
#   this repo - and the only other tools used are POSIX sh, curl and unzip,
#   all present on a stock macOS.
#
#   Two shell-syntax rules this file follows deliberately, because breaking
#   either one fails in a confusing way rather than loudly:
#     - no `case ... esac` inside a $(shell ...) assignment. Make counts
#       parentheses when it parses a function call, so the `)` ending a case
#       pattern closes $(shell early. Recipes are unaffected - Make does not
#       parse their parens - so `case` is used freely below the tab.
#     - no `#` anywhere inside a recipe. Recipe lines joined with a trailing
#       backslash keep their backslash-newline, and a `#` comment would eat
#       the continuation that follows it.

SHELL := /bin/sh
.SUFFIXES:
.NOTPARALLEL:
.DEFAULT_GOAL := help

MAKEFILE_PATH := $(abspath $(lastword $(MAKEFILE_LIST)))
ROOT          := $(patsubst %/,%,$(dir $(MAKEFILE_PATH)))

# 1.3.14 is the last Zig Bun and what scripts/build.sh names as MIN_BUN.
BUN_VERSION := 1.3.14
BUN_DIR     ?= $(HOME)/.bun-$(BUN_VERSION)
# BUN_BIN is honoured if it is already in the environment; `setup` still
# installs into BUN_DIR, so the two can legitimately differ.
BUN_BIN     ?= $(BUN_DIR)/bun

OUT_DIR   ?= $(ROOT)/build
CACHE_DIR ?= $(HOME)/.cache/not-rusty-claude

RELEASES := https://downloads.claude.ai/claude-code-releases

# Measured 2026-08-24: $(RELEASES)/latest -> 2.1.241 and $(RELEASES)/stable ->
# 2.1.231. They are different versions, so the channel is a real choice:
#   make binary CHANNEL=latest
#   make binary VERSION=2.1.231
CHANNEL ?= stable
VERSION ?=

UNAME_S := $(shell uname -s)
UNAME_M := $(shell uname -m)

# uname -m -> the platform strings the release endpoint uses. Override with
# PLATFORM= when uname does not describe the binary you want.
PLATFORM ?= $(shell \
  s=`uname -s`; m=`uname -m`; \
  if [ "$$s" = Darwin ]; then \
    if [ "$$m" = arm64 ]; then echo darwin-arm64; \
    elif [ "$$m" = x86_64 ]; then echo darwin-x64; \
    else echo "unsupported:$$s/$$m"; fi; \
  elif [ "$$s" = Linux ]; then \
    if [ "$$m" = x86_64 ]; then echo linux-x64; \
    elif [ "$$m" = aarch64 ] || [ "$$m" = arm64 ]; then echo linux-arm64; \
    else echo "unsupported:$$s/$$m"; fi; \
  else echo "unsupported:$$s/$$m"; fi)

# Bun's asset names are NOT the endpoint's platform strings: Bun spells 64-bit
# ARM "aarch64" where the release endpoint spells it "arm64". Verified
# 2026-08-24 that all four of bun-{darwin,linux}-{x64,aarch64}.zip exist under
# the 1.3.14 tag (HTTP 200), and that bun-darwin-aarch64.zip unzips to exactly
# one executable at bun-darwin-aarch64/bun.
#
# $(subst), not $(patsubst %-arm64,...): the pattern form is anchored at the
# end, so it renamed darwin-arm64 but left PLATFORM=linux-arm64-musl as
# bun-linux-arm64-musl - a name that does not exist. Bun's own
# SHASUMS256.txt for 1.3.14, fetched 2026-08-25, lists
# bun-linux-aarch64-musl.zip and no bun-linux-arm64-musl.zip, and
# tests/test_makefile.py accepts linux-arm64-musl as a PLATFORM.
BUN_ASSET := bun-$(subst -arm64,-aarch64,$(PLATFORM))
BUN_URL   := https://github.com/oven-sh/bun/releases/download/bun-v$(BUN_VERSION)/$(BUN_ASSET).zip

# `setup` downloads an EXECUTABLE and then runs it, and so does everything
# after it: this bun executes the extracted Claude code, the smoke test and
# `make ab`. That is a strictly larger trust surface than the read-only
# binary `make binary` fetches - and it was the download with NO integrity
# check at all. Its only check was `bun --version` == $(BUN_VERSION), a string
# any substituted executable can print, so a swapped asset or an intercepted
# transfer installed silently and then ran everything.
#
# These are Bun's own sha256 values, copied from the SHASUMS256.txt GitHub
# publishes beside the bun-v1.3.14 assets, fetched 2026-08-25. The
# bun-darwin-aarch64.zip row was additionally confirmed end to end here:
# downloading that asset (23586433 bytes, per its content-length) and hashing
# it gave exactly d8b96221828ad6f97ac7ac0ab7e95872341af763001e8803e8267652c2652620.
# Six rows because those are the six PLATFORM strings this Makefile can emit
# or tests/test_makefile.py accepts; `make doctor` prints the one in force.
BUN_SHASUMS_URL := https://github.com/oven-sh/bun/releases/download/bun-v$(BUN_VERSION)/SHASUMS256.txt

BUN_SHA256_1.3.14_bun-darwin-aarch64     := d8b96221828ad6f97ac7ac0ab7e95872341af763001e8803e8267652c2652620
BUN_SHA256_1.3.14_bun-darwin-x64         := 4183df3374623e5bab315c547cfa0974533cd457d86b73b639f7a87974cd6633
BUN_SHA256_1.3.14_bun-linux-aarch64      := a27ffb63a8310375836e0d6f668ae17fa8d8d18b88c37c821c65331973a19a3b
BUN_SHA256_1.3.14_bun-linux-x64          := 951ee2aee855f08595aeec6225226a298d3fea83a3dcd6465c09cbccdf7e848f
BUN_SHA256_1.3.14_bun-linux-aarch64-musl := b98e0ad3625c5c00d1d5b5ff55605c7adddbfae151861e68ade57b2d3b8703bb
BUN_SHA256_1.3.14_bun-linux-x64-musl     := 14bd9aedeebf1dba67e8def9531c89bc989ecfdf1de42e5bfcaf1b8cd9294719

# Keyed by version as well as asset so that `make setup BUN_VERSION=x.y.z`
# cannot silently check a 1.3.14 hash against a different release's zip: an
# unknown version simply has no pinned row and takes the fetch path below.
# `:=`, not `?=`, so only an explicit `make setup BUN_SHA256=...` on the
# command line can replace a pin - never something already in the environment.
BUN_SHA256 := $(BUN_SHA256_$(BUN_VERSION)_$(BUN_ASSET))

# The download is saved per version, with a fixed-name symlink beside it so
# `build`/`test` have one path to look for. Neither name is `claude`.
BINARY_LINK := $(CACHE_DIR)/claude-$(PLATFORM).bin

# -rs makes pytest print a SKIPPED line per skip, with the reason and the
# count, which is what turns the pre-run table below into something checkable
# instead of something to take on trust. Overridable:
#   make test PYTEST_ARGS='-k macho -vv'
PYTEST_ARGS ?= -q -rs
AB_ARGS     ?=

# Running the artifact under Node instead of Bun (scripts/bun-shim.cjs).
#
# Node >= 24 only: the bundle uses `using` declarations and 22/23 fail
# `node --check` on it - measured, so the recipes below check the major rather
# than letting it fail as a SyntaxError 25 MB into a minified file.
#
# ws and undici are Bun builtins Node does not have, and the artifact imports
# both. They go in CACHE_DIR, not in this checkout: this repo has no
# package.json and gains no npm dependency, and nothing is installed globally.
MIN_NODE_MAJOR      := 24
NODE_BIN            ?= $(shell command -v node 2>/dev/null)
NODE_DIR            := $(CACHE_DIR)/node
NODE_MODULES        := $(NODE_DIR)/node_modules
NODE_WS_VERSION     ?= 8.21.3
NODE_UNDICI_VERSION ?= 7.29.0
NODE_ARGS           ?= mcp list

# The binary `build` extracts from. Empty means: the one `make binary`
# downloaded, else let scripts/build.sh find one itself.
CLAUDE_BINARY ?=

# `clean` says what it is deliberately NOT deleting. `distclean` sets this to 0
# through a target-specific variable (inherited by its `clean` prerequisite, and
# supported well before 3.81) so it does not announce KEPT for the two
# directories it is about to remove one line later.
KEEP_NOTICE ?= 1

.PHONY: help doctor setup binary build smoke node-deps node-run test ab clean distclean first-run

help:
	@printf '%s\n' \
	  'not-rusty-claude - make targets' \
	  '' \
	  '  help        this list; the default target, and it changes nothing' \
	  '  doctor      report what this host has: platform, bun, python, binaries' \
	  '  setup       download Bun $(BUN_VERSION) into $(BUN_DIR), verify its sha256 (not on PATH)' \
	  '  binary      download this platform'"'"'s Claude Code binary and verify its sha256' \
	  '  build       run scripts/build.sh -> $(OUT_DIR)/extract' \
	  '  smoke       run the built artifact once (mcp list) under bun' \
	  '  node-deps   download ws + undici into $(NODE_MODULES) (needs npm)' \
	  '  node-run    run the built artifact under node + scripts/bun-shim.cjs' \
	  '  test        run the pytest suite, saying up front what will run vs skip' \
	  '  ab          scripts/ab-equivalence.sh, the three-way A/B (Linux only)' \
	  '  first-run   setup + binary + build + smoke + test, in that order' \
	  '  clean       delete build artifacts and python caches; keeps downloads' \
	  '  distclean   clean, PLUS the downloaded binaries and the private bun' \
	  '' \
	  'Variables (make VAR=value):' \
	  '  PLATFORM=$(PLATFORM)' \
	  '  CHANNEL=$(CHANNEL)          stable | latest' \
	  '  VERSION=            exact version, overrides CHANNEL' \
	  '  BUN_BIN=$(BUN_BIN)' \
	  '  CACHE_DIR=$(CACHE_DIR)' \
	  '  OUT_DIR=$(OUT_DIR)' \
	  '  CLAUDE_BINARY=      binary for `build` (default: the downloaded one)' \
	  '  PYTEST_ARGS=$(PYTEST_ARGS)         passed to pytest' \
	  '  AB_ARGS=            passed to scripts/ab-equivalence.sh' \
	  '  NODE_BIN=$(NODE_BIN)' \
	  '  NODE_ARGS=$(NODE_ARGS)       what `node-run` runs' \
	  '' \
	  'Also honoured from the environment: NRC_TEST_ELF, NRC_TEST_MACHO,' \
	  'NRC_TEST_NODE, NRC_TEST_NODE_MODULES, NRC_TEST_ARTIFACT, BUN_BIN,' \
	  'OUT_DIR, NRC_NO_IMAGE_SHIM.'

doctor:
	@set -eu; \
	echo '==> host'; \
	printf '    %-22s %s\n' 'uname'     '$(UNAME_S) $(UNAME_M)'; \
	printf '    %-22s %s\n' 'platform'  '$(PLATFORM)'; \
	printf '    %-22s %s\n' 'make'      "$$(make --version 2>/dev/null | head -1)"; \
	printf '    %-22s %s\n' 'sh'        '$(SHELL)'; \
	echo '==> tools'; \
	for t in python3 curl unzip uv node; do \
	  p="$$(command -v $$t 2>/dev/null || true)"; \
	  printf '    %-22s %s\n' "$$t" "$${p:-MISSING}"; \
	done; \
	printf '    %-22s %s\n' 'python3 version' "$$(python3 -V 2>&1 || echo MISSING)"; \
	echo '==> bun'; \
	if [ -x '$(BUN_BIN)' ]; then \
	  printf '    %-22s %s (%s)\n' 'bun' '$(BUN_BIN)' "$$('$(BUN_BIN)' --version 2>/dev/null || echo '?')"; \
	else \
	  printf '    %-22s %s\n' 'bun' 'not installed - run: make setup'; \
	fi; \
	printf '    %-22s %s\n' 'bun asset' '$(BUN_ASSET).zip'; \
	if [ -n '$(BUN_SHA256)' ]; then \
	  printf '    %-22s %s\n' 'pinned sha256' '$(BUN_SHA256)'; \
	else \
	  printf '    %-22s %s\n' 'pinned sha256' 'none pinned - setup will read $(BUN_SHASUMS_URL)'; \
	fi; \
	echo '==> node (scripts/bun-shim.cjs runs the artifact without bun)'; \
	if [ -n '$(NODE_BIN)' ] && [ -x '$(NODE_BIN)' ]; then \
	  printf '    %-22s %s (%s, needs >= $(MIN_NODE_MAJOR))\n' 'node' '$(NODE_BIN)' "$$('$(NODE_BIN)' -p process.versions.node 2>/dev/null || echo '?')"; \
	else \
	  printf '    %-22s %s\n' 'node' 'not found - set NODE_BIN='; \
	fi; \
	if [ -f '$(NODE_MODULES)/ws/package.json' ] && [ -f '$(NODE_MODULES)/undici/package.json' ]; then \
	  printf '    %-22s %s\n' 'ws + undici' '$(NODE_MODULES)'; \
	else \
	  printf '    %-22s %s\n' 'ws + undici' 'missing - run: make node-deps'; \
	fi; \
	echo '==> claude binaries'; \
	if [ -e '$(BINARY_LINK)' ]; then \
	  printf '    %-22s %s -> %s\n' 'downloaded' '$(BINARY_LINK)' "$$(readlink '$(BINARY_LINK)' || echo '?')"; \
	else \
	  printf '    %-22s %s\n' 'downloaded' 'none - run: make binary'; \
	fi; \
	for p in "$${NRC_TEST_ELF:-}" /usr/bin/claude; do \
	  if [ -n "$$p" ] && [ -f "$$p" ]; then printf '    %-22s %s\n' 'ELF candidate' "$$p"; break; fi; \
	done; \
	for p in "$${NRC_TEST_MACHO:-}" /tmp/ccmac/package/claude-darwin-arm64.bin /tmp/ccmac/package/claude; do \
	  if [ -n "$$p" ] && [ -f "$$p" ]; then printf '    %-22s %s\n' 'Mach-O candidate' "$$p"; break; fi; \
	done; \
	echo '==> artifacts'; \
	if [ -f '$(OUT_DIR)/extract/cli.original.cjs' ]; then \
	  printf '    %-22s %s\n' 'built' '$(OUT_DIR)/extract/cli.original.cjs'; \
	else \
	  printf '    %-22s %s\n' 'built' 'none - run: make build'; \
	fi

# Idempotent by re-running the installed bun: an interrupted unzip can leave a
# file that exists and is executable but is not a working bun, and "the file is
# there" would call that done.
setup:
	@set -eu; \
	case '$(PLATFORM)' in unsupported:*) \
	  echo 'error: no Bun $(BUN_VERSION) mapping for $(UNAME_S) $(UNAME_M); set PLATFORM=' >&2; exit 1;; \
	esac; \
	if [ -x '$(BUN_DIR)/bun' ] && v="$$('$(BUN_DIR)/bun' --version 2>/dev/null)" && [ "$$v" = '$(BUN_VERSION)' ]; then \
	  echo "==> bun $(BUN_VERSION) already at $(BUN_DIR)/bun - nothing to download"; \
	else \
	  want='$(BUN_SHA256)'; \
	  if [ -z "$$want" ]; then \
	    echo '==> no pinned sha256 for $(BUN_ASSET).zip at bun $(BUN_VERSION); asking SHASUMS256.txt'; \
	    want="$$(curl -fsSL '$(BUN_SHASUMS_URL)' | python3 -c 'import sys; w=sys.argv[1]; print(next((f[0] for f in (l.split() for l in sys.stdin) if len(f) > 1 and f[1].lstrip("*") == w), ""))' '$(BUN_ASSET).zip')"; \
	  fi; \
	  python3 -c 'import re,sys; sys.exit(0 if re.match(r"^[0-9a-f]{64}$$", sys.argv[1] or "") else 1)' "$$want" \
	    || { echo 'error: no published sha256 for $(BUN_ASSET).zip at bun $(BUN_VERSION) - refusing to install an unverified bun, because this is the executable that runs every artifact this repo builds. Pass BUN_SHA256=<64 hex> from a source you trust.' >&2; exit 1; }; \
	  echo '==> downloading $(BUN_ASSET).zip'; \
	  tmp="$$(mktemp -d "$${TMPDIR:-/tmp}/nrc-bun.XXXXXX")"; \
	  trap 'rm -rf "$$tmp"' EXIT INT TERM; \
	  curl -fL --retry 3 --progress-bar -o "$$tmp/bun.zip" '$(BUN_URL)'; \
	  echo '==> verifying sha256'; \
	  got="$$(python3 -c 'import hashlib,sys; h=hashlib.sha256(); f=open(sys.argv[1],"rb"); [h.update(c) for c in iter(lambda: f.read(1<<20), b"")]; print(h.hexdigest())' "$$tmp/bun.zip")"; \
	  if [ "$$got" != "$$want" ]; then \
	    rm -rf "$$tmp"; \
	    echo '' >&2; \
	    echo 'error: CHECKSUM MISMATCH - the download was DELETED, nothing was installed.' >&2; \
	    echo "  expected sha256 $$want" >&2; \
	    echo "  got      sha256 $$got" >&2; \
	    echo '  This bun would have EXECUTED the extracted Claude code and every' >&2; \
	    echo '  smoke test. Do not use it. Retry; if it repeats, stop and investigate.' >&2; \
	    exit 1; \
	  fi; \
	  echo "==> sha256 OK: $$want"; \
	  unzip -q -j -o "$$tmp/bun.zip" '*/bun' -d "$$tmp"; \
	  [ -f "$$tmp/bun" ] || { echo 'error: $(BUN_ASSET).zip contained no bun executable' >&2; exit 1; }; \
	  chmod 0755 "$$tmp/bun"; \
	  if [ '$(UNAME_S)' = Darwin ]; then \
	    xattr -d com.apple.quarantine "$$tmp/bun" 2>/dev/null || true; \
	    xattr -c "$$tmp/bun" 2>/dev/null || true; \
	  fi; \
	  mkdir -p '$(BUN_DIR)'; \
	  mv "$$tmp/bun" '$(BUN_DIR)/bun'; \
	  v="$$('$(BUN_DIR)/bun' --version 2>/dev/null || true)"; \
	  if [ "$$v" != '$(BUN_VERSION)' ]; then \
	    rm -f '$(BUN_DIR)/bun'; \
	    echo "error: installed bun reported '$$v', expected '$(BUN_VERSION)'; removed it" >&2; \
	    exit 1; \
	  fi; \
	  echo "==> installed bun $$v at $(BUN_DIR)/bun"; \
	fi; \
	echo '    it is NOT on PATH and nothing was added to any shell profile.'; \
	echo '    everything below uses it automatically; by hand it is:'; \
	echo '      $(BUN_DIR)/bun <script>'

# Verify-then-move. A binary is only ever named claude-<platform>-<version>.bin
# and is left non-executable: the pipeline READS it, and a file called claude
# that runs would be exactly the thing this repo refuses to create.
binary:
	@set -eu; \
	case '$(PLATFORM)' in unsupported:*) \
	  echo 'error: cannot map $(UNAME_S) $(UNAME_M) to a release platform; set PLATFORM=' >&2; exit 1;; \
	esac; \
	case '$(PLATFORM)' in win32-*) \
	  echo 'error: PLATFORM=$(PLATFORM) is a Windows build; tools/extract_bun.py refuses PE input ("not supported"), so this pipeline has nothing to do with it.' >&2; exit 1;; \
	esac; \
	ver='$(VERSION)'; \
	if [ -z "$$ver" ]; then \
	  echo '==> resolving $(CHANNEL)'; \
	  ver="$$(curl -fsS '$(RELEASES)/$(CHANNEL)' | tr -d '[:space:]')"; \
	fi; \
	python3 -c 'import re,sys; sys.exit(0 if re.match(r"^[0-9]+\.[0-9]+\.[0-9]+", sys.argv[1]) else 1)' "$$ver" \
	  || { echo "error: '$$ver' is not a version - is the endpoint reachable from here?" >&2; exit 1; }; \
	echo "==> version $$ver ($(PLATFORM))"; \
	mkdir -p '$(CACHE_DIR)'; \
	man="$$(curl -fsS "$(RELEASES)/$$ver/manifest.json")"; \
	meta="$$(printf '%s' "$$man" | python3 -c 'import json,sys; p=json.load(sys.stdin)["platforms"].get(sys.argv[1]); sys.exit("platform not in this manifest") if p is None else print(p["checksum"], p["size"])' '$(PLATFORM)')"; \
	set -- $$meta; \
	sum="$${1:-}"; size="$${2:-}"; \
	python3 -c 'import re,sys; sys.exit(0 if re.match(r"^[0-9a-f]{64}$$", sys.argv[1] or "") else 1)' "$$sum" \
	  || { echo "error: manifest gave no usable sha256 for $(PLATFORM)" >&2; exit 1; }; \
	dest='$(CACHE_DIR)'/claude-'$(PLATFORM)'-"$$ver".bin; \
	if [ -f "$$dest" ] && [ "$$(python3 -c 'import hashlib,sys; h=hashlib.sha256(); f=open(sys.argv[1],"rb"); [h.update(c) for c in iter(lambda: f.read(1<<20), b"")]; print(h.hexdigest())' "$$dest")" = "$$sum" ]; then \
	  echo "==> already downloaded and verified: $$dest"; \
	else \
	  echo "==> downloading $$size bytes -> $$dest"; \
	  rm -f "$$dest.part"; \
	  curl -fL --retry 3 --progress-bar -o "$$dest.part" "$(RELEASES)/$$ver/$(PLATFORM)/claude"; \
	  echo '==> verifying sha256'; \
	  got="$$(python3 -c 'import hashlib,sys; h=hashlib.sha256(); f=open(sys.argv[1],"rb"); [h.update(c) for c in iter(lambda: f.read(1<<20), b"")]; print(h.hexdigest())' "$$dest.part")"; \
	  gotsize="$$(python3 -c 'import os,sys; print(os.path.getsize(sys.argv[1]))' "$$dest.part")"; \
	  if [ "$$got" != "$$sum" ] || [ "$$gotsize" != "$$size" ]; then \
	    rm -f "$$dest.part"; \
	    echo '' >&2; \
	    echo 'error: CHECKSUM MISMATCH - the download was DELETED, nothing was kept.' >&2; \
	    echo "  expected sha256 $$sum ($$size bytes)" >&2; \
	    echo "  got      sha256 $$got ($$gotsize bytes)" >&2; \
	    echo '  Do not use this file. Retry; if it repeats, stop and investigate.' >&2; \
	    exit 1; \
	  fi; \
	  chmod 0644 "$$dest.part"; \
	  mv "$$dest.part" "$$dest"; \
	  echo "==> sha256 OK: $$sum"; \
	fi; \
	rm -f '$(BINARY_LINK)'; \
	ln -s "$$(basename "$$dest")" '$(BINARY_LINK)'; \
	echo "==> $(BINARY_LINK) -> $$(basename "$$dest")"; \
	echo '    left non-executable on purpose: it is build input, not a CLI to run.'; \
	echo '    next: make build'

build:
	@set -eu; \
	cd '$(ROOT)'; \
	bin='$(CLAUDE_BINARY)'; \
	if [ -z "$$bin" ] && [ -e '$(BINARY_LINK)' ]; then bin='$(BINARY_LINK)'; fi; \
	if [ -n "$$bin" ] && [ ! -f "$$bin" ]; then echo "error: no such binary: $$bin" >&2; exit 1; fi; \
	if [ -n "$$bin" ]; then echo "==> building from $$bin"; else echo '==> no downloaded binary; letting scripts/build.sh find one (make binary gets you one)'; fi; \
	if [ -x '$(BUN_BIN)' ]; then \
	  OUT_DIR='$(OUT_DIR)' BUN_BIN='$(BUN_BIN)' ./scripts/build.sh $$bin; \
	else \
	  OUT_DIR='$(OUT_DIR)' ./scripts/build.sh $$bin; \
	fi

# The exact command scripts/build.sh prints at the end of a build, run for you
# with a throwaway CLAUDE_CONFIG_DIR. `mcp list` is the smoke test rather than
# `--version` because --version can answer without loading much. python3 is the
# watchdog because macOS has no timeout(1). Measured here 2026-08-24: 2.1 s,
# exit 0, printing "No MCP servers configured."
smoke:
	@set -eu; \
	art='$(OUT_DIR)/extract/cli.original.cjs'; \
	[ -f "$$art" ] || { echo "error: no artifact at $$art - run: make build" >&2; exit 1; }; \
	[ -x '$(BUN_BIN)' ] || { echo 'error: no bun at $(BUN_BIN) - run: make setup' >&2; exit 1; }; \
	cfg="$$(mktemp -d "$${TMPDIR:-/tmp}/nrc-smoke.XXXXXX")"; \
	trap 'rm -rf "$$cfg"' EXIT INT TERM; \
	echo "==> $(BUN_BIN) $$art mcp list"; \
	DISABLE_AUTOUPDATER=1 CLAUDE_CONFIG_DIR="$$cfg" \
	  python3 -c 'import subprocess,sys; sys.exit(subprocess.run(sys.argv[1:], timeout=180).returncode)' \
	  '$(BUN_BIN)' "$$art" mcp list; \
	echo '==> smoke OK (exit 0). Your real ~/.claude was not touched.'

# ws and undici only. Measured on the 2.1.231 artifact: 12 bare specifiers are
# not Node builtins, and exactly three of those are Bun builtins Node lacks -
# ws, undici and bun:ffi. bun:ffi needs nothing here: its one call site is
# behind a macOS check AND inside a try/catch. The other nine (react, ajv/*,
# playwright-core...) are dead or optional paths that fail under Bun too.
#
# npm does the integrity checking here, which is a weaker guarantee than the
# pinned sha256 above for bun: the registry vouches for its own tarballs.
# Versions are pinned so the fetch is at least reproducible; both were the
# current release when measured on 2026-08-25.
node-deps:
	@set -eu; \
	if [ -f '$(NODE_MODULES)/ws/package.json' ] && [ -f '$(NODE_MODULES)/undici/package.json' ]; then \
	  echo '==> ws and undici already in $(NODE_MODULES)'; \
	else \
	  command -v npm >/dev/null 2>&1 || { \
	    echo 'error: npm not found. Install Node >= $(MIN_NODE_MAJOR) (npm ships with it), or put ws and undici in a node_modules yourself and pass NRC_TEST_NODE_MODULES=<dir> to make test.' >&2; \
	    exit 1; }; \
	  mkdir -p '$(NODE_DIR)'; \
	  echo '==> npm install ws@$(NODE_WS_VERSION) undici@$(NODE_UNDICI_VERSION) -> $(NODE_MODULES)'; \
	  cd '$(NODE_DIR)' && npm install --no-save --no-audit --no-fund --loglevel=error \
	    'ws@$(NODE_WS_VERSION)' 'undici@$(NODE_UNDICI_VERSION)'; \
	fi; \
	echo '    nothing was installed globally, and this checkout has no package.json.'

# The Node counterpart of `smoke`. Same command, same throwaway config dir, so
# the two are directly comparable by eye; tests/test_node_runtime.py is what
# compares them byte for byte.
node-run: node-deps
	@set -eu; \
	art='$(OUT_DIR)/extract/cli.original.cjs'; \
	[ -f "$$art" ] || { echo "error: no artifact at $$art - run: make build" >&2; exit 1; }; \
	node='$(NODE_BIN)'; \
	[ -n "$$node" ] && [ -x "$$node" ] || { echo 'error: no node found - install Node >= $(MIN_NODE_MAJOR), or set NODE_BIN=' >&2; exit 1; }; \
	v="$$("$$node" -p process.versions.node 2>/dev/null || echo 0)"; \
	major="$${v%%.*}"; \
	[ "$$major" -ge $(MIN_NODE_MAJOR) ] 2>/dev/null || { \
	  echo "error: $$node is Node $$v; the Claude bundle uses ES explicit resource management and needs Node >= $(MIN_NODE_MAJOR)" >&2; exit 1; }; \
	cfg="$$(mktemp -d "$${TMPDIR:-/tmp}/nrc-node.XXXXXX")"; \
	trap 'rm -rf "$$cfg"' EXIT INT TERM; \
	echo "==> $$node --require scripts/bun-shim.cjs $$art $(NODE_ARGS)"; \
	DISABLE_AUTOUPDATER=1 CLAUDE_CONFIG_DIR="$$cfg" NODE_PATH='$(NODE_MODULES)' \
	  python3 -c 'import subprocess,sys; sys.exit(subprocess.run(sys.argv[1:], timeout=300).returncode)' \
	  "$$node" --require '$(ROOT)/scripts/bun-shim.cjs' "$$art" $(NODE_ARGS); \
	echo '==> node-run OK (exit 0). Your real ~/.claude was not touched.'

# A handful of inputs decide how much of the suite actually executes, and every
# skip in this repo comes from one of them: tests/conftest.py is the only file
# that calls pytest.skip, through the real_elf_binary, real_macho_binary,
# bun_bin, node_bin, ws_module, undici_module and built_artifact fixtures
# (measured 2026-08-25). A fresh Mac has none of them, so a first run is mostly
# skips - which reads like a broken checkout unless something says otherwise
# first. Hence the table.
#
# The exact per-row counts are deliberately NOT hardcoded here. They were 6 /
# 7 / 3 when measured on 2026-08-24, but the suite is actively growing and a
# stale number in a Makefile is worse than no number: -rs makes pytest print
# the real count and reason for every skip at the end of the run instead.
#
# Auto-wiring: on macOS a downloaded binary IS the Mach-O specimen, and on
# Linux it is the ELF one, so `make binary` feeds `make test` without the user
# having to know either env var. An env var that is already set always wins,
# and conftest.py's own defaults are tried before the download.
test:
	@set -eu; \
	cd '$(ROOT)'; \
	elf="$${NRC_TEST_ELF:-}"; elfsrc='NRC_TEST_ELF'; \
	if [ -z "$$elf" ]; then \
	  if [ -f /usr/bin/claude ]; then elf=/usr/bin/claude; elfsrc='default'; \
	  else \
	    case '$(PLATFORM)' in linux-*) if [ -e '$(BINARY_LINK)' ]; then elf='$(BINARY_LINK)'; elfsrc='make binary'; fi;; esac; \
	  fi; \
	fi; \
	macho="$${NRC_TEST_MACHO:-}"; msrc='NRC_TEST_MACHO'; \
	if [ -z "$$macho" ]; then \
	  for p in /tmp/ccmac/package/claude-darwin-arm64.bin /tmp/ccmac/package/claude; do \
	    if [ -f "$$p" ]; then macho="$$p"; msrc='default'; break; fi; \
	  done; \
	  if [ -z "$$macho" ]; then \
	    case '$(PLATFORM)' in darwin-*) if [ -e '$(BINARY_LINK)' ]; then macho='$(BINARY_LINK)'; msrc='make binary'; fi;; esac; \
	  fi; \
	fi; \
	bun="$${BUN_BIN:-}"; bsrc='BUN_BIN'; \
	if [ -z "$$bun" ]; then \
	  if [ -x '$(BUN_DIR)/bun' ]; then bun='$(BUN_DIR)/bun'; bsrc='default'; \
	  else bun="$$(command -v bun 2>/dev/null || true)"; bsrc='PATH'; fi; \
	fi; \
	node="$${NRC_TEST_NODE:-}"; nsrc='NRC_TEST_NODE'; \
	if [ -z "$$node" ]; then node='$(NODE_BIN)'; nsrc='PATH'; fi; \
	nver=''; \
	if [ -n "$$node" ] && [ -x "$$node" ]; then nver="$$("$$node" -p process.versions.node 2>/dev/null || true)"; fi; \
	mods="$${NRC_TEST_NODE_MODULES:-$(NODE_MODULES)}"; \
	if command -v uv >/dev/null 2>&1; then \
	  runner='uv run --no-project --with pytest python -m pytest'; rsrc='uv (no venv, installs nothing globally)'; \
	elif python3 -c 'import pytest' >/dev/null 2>&1; then \
	  runner='python3 -m pytest'; rsrc="python3 -m pytest ($$(python3 -V 2>&1))"; \
	else \
	  echo 'error: no test runner. Install uv (https://docs.astral.sh/uv/) or: python3 -m pip install pytest' >&2; \
	  exit 1; \
	fi; \
	echo '==> test inputs (a missing one skips its tests; it is not a failure)'; \
	if [ -n "$$elf" ] && [ -f "$$elf" ]; then printf '    %-22s RUN    %s [%s]\n' 'ELF Claude binary' "$$elf" "$$elfsrc"; \
	  else printf '    %-22s SKIP   not found - set NRC_TEST_ELF, or `make binary` on Linux\n' 'ELF Claude binary'; fi; \
	if [ -n "$$macho" ] && [ -f "$$macho" ]; then printf '    %-22s RUN    %s [%s]\n' 'Mach-O Claude binary' "$$macho" "$$msrc"; \
	  else printf '    %-22s SKIP   not found - set NRC_TEST_MACHO, or `make binary` on macOS\n' 'Mach-O Claude binary'; fi; \
	if [ -n "$$bun" ] && [ -x "$$bun" ]; then printf '    %-22s RUN    %s [%s]\n' 'bun $(BUN_VERSION)' "$$bun" "$$bsrc"; \
	  else printf '    %-22s SKIP   not found - run `make setup`\n' 'bun $(BUN_VERSION)'; fi; \
	case "$$nver" in \
	  ''|0) printf '    %-22s SKIP   not found - set NRC_TEST_NODE\n' 'node >= $(MIN_NODE_MAJOR)';; \
	  1?.*|2[0-3].*) printf '    %-22s SKIP   %s is Node %s, too old to parse the bundle\n' 'node >= $(MIN_NODE_MAJOR)' "$$node" "$$nver";; \
	  *) printf '    %-22s RUN    %s (%s) [%s]\n' 'node >= $(MIN_NODE_MAJOR)' "$$node" "$$nver" "$$nsrc";; \
	esac; \
	if [ -f "$$mods/ws/package.json" ] && [ -f "$$mods/undici/package.json" ]; then \
	  printf '    %-22s RUN    %s\n' 'ws + undici' "$$mods"; \
	  else printf '    %-22s SKIP   not in %s - run `make node-deps`\n' 'ws + undici' "$$mods"; fi; \
	if [ -f '$(OUT_DIR)/extract/cli.original.cjs' ]; then \
	  printf '    %-22s RUN    %s\n' 'built artifact' '$(OUT_DIR)/extract/cli.original.cjs'; \
	  else printf '    %-22s SKIP   not built - run `make build`\n' 'built artifact'; fi; \
	printf '    %-22s %s\n' 'runner' "$$rsrc"; \
	echo '    Every skip this suite can produce comes from one of the rows'; \
	echo '    above; the SKIPPED lines pytest prints at the end give the counts.'; \
	echo '==> running'; \
	if [ -n "$$elf" ]; then NRC_TEST_ELF="$$elf"; export NRC_TEST_ELF; fi; \
	if [ -n "$$macho" ]; then NRC_TEST_MACHO="$$macho"; export NRC_TEST_MACHO; fi; \
	if [ -n "$$bun" ]; then BUN_BIN="$$bun"; export BUN_BIN; fi; \
	if [ -n "$$node" ]; then NRC_TEST_NODE="$$node"; export NRC_TEST_NODE; fi; \
	NRC_TEST_NODE_MODULES="$$mods"; export NRC_TEST_NODE_MODULES; \
	$$runner tests/ $(PYTEST_ARGS)

ab:
	@set -eu; \
	cd '$(ROOT)'; \
	if [ '$(UNAME_S)' != Linux ]; then \
	  echo 'error: `make ab` is Linux-only - scripts/ab-equivalence.sh reads /proc/net/tcp and /proc/<pid>/fd to prove each run opens no non-loopback socket, and refuses to run unguarded on $(UNAME_S); run it on a Linux host instead.' >&2; \
	  exit 1; \
	fi; \
	if [ -x '$(BUN_BIN)' ]; then BUN_BIN='$(BUN_BIN)' ./scripts/ab-equivalence.sh $(AB_ARGS); \
	else ./scripts/ab-equivalence.sh $(AB_ARGS); fi

# Deletes only what a rebuild recreates in seconds. The downloaded binaries are
# ~325 MB each and are NOT touched here - `make distclean` is the target that
# asks for that.
clean:
	@set -eu; \
	rm -rf '$(OUT_DIR)/extract'; \
	rm -rf '$(OUT_DIR)'/.extract.stage.* '$(OUT_DIR)'/.extract.prev.*; \
	rmdir '$(OUT_DIR)' 2>/dev/null || true; \
	find '$(ROOT)' -name .git -type d -prune -o -name __pycache__ -type d -prune -exec rm -rf {} + ; \
	rm -rf '$(ROOT)/.pytest_cache'; \
	echo '==> removed build artifacts and python caches'; \
	if [ '$(KEEP_NOTICE)' = 1 ] && [ -d '$(CACHE_DIR)' ]; then echo "    KEPT $(CACHE_DIR) ($$(du -sh '$(CACHE_DIR)' 2>/dev/null | cut -f1)) - make distclean removes it"; fi; \
	if [ '$(KEEP_NOTICE)' = 1 ] && [ -d '$(BUN_DIR)' ]; then echo "    KEPT $(BUN_DIR) ($$(du -sh '$(BUN_DIR)' 2>/dev/null | cut -f1)) - make distclean removes it"; fi

distclean: KEEP_NOTICE = 0
distclean: clean
	@set -eu; \
	for d in '$(CACHE_DIR)' '$(BUN_DIR)'; do \
	  if [ -d "$$d" ]; then \
	    echo "==> deleting $$d ($$(du -sh "$$d" 2>/dev/null | cut -f1))"; \
	    rm -rf "$$d"; \
	  fi; \
	done; \
	echo '==> distclean done; `make first-run` re-downloads everything'

first-run: setup binary build smoke test
	@echo '==> first-run complete: bun installed, binary verified, artifact built, smoke passed, suite run'
