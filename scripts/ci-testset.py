#!/usr/bin/env python3
"""The set of tests a CI run executed, read from job logs, for comparing two layouts of ci.yml's jobs
(docs/spec/release-and-ci.md §6): split or merge jobs, then `diff` the two listings.

    gh run view --job <id> --log > job.log        # one file per job
    scripts/ci-testset.py <log>... > tests.txt    # every job of one platform together
    scripts/ci-testset.py --self-test

Prints, sorted, one line per test, `<package>/<target> TAB <test name> TAB ok|ignored`, and one line per test
executable, `<package>/<target> TAB (filtered out) TAB <n>` (so an executable with no tests is listed too). stderr gets the totals.
Exits 1 when the headers and the results do not pair up, a count differs from its `test result:` line, a test failed,
or an executable ran twice in the logs given (two shards running the same file).

How a log is read:
- A test executable is a `Running <source> (<path>)` line or a `Doc-tests <crate>` line (cargo, stderr); its tests are the
  next unclaimed `running N tests` ... `test result:` block (libtest, stdout). The two streams interleave in the log, so
  headers and blocks are paired by order, never by adjacency.
- A test that runs its own executable as a child puts the child's `running 1 test` ... `test result:` inside the block;
  the child's lines repeat names of the block and are counted once.
- The package is the `-p` of the cargo command that ran, when it has exactly one: the step's `Run cargo ...` line, or a
  `+ cargo ...` line of `set -x` in a step with several commands. Otherwise (no `-p`, several, or no command seen) it
  follows cargo's output order: `unittests src/lib.rs` starts the package whose library the executable is; `src/main.rs`
  or `src/bin/*.rs` starts a package only when the executable is named after a workspace package without a library (else
  it is a binary of the current package); `tests/*.rs` belongs to the current package. Which packages have a library
  comes from `cargo metadata --no-deps`. The hash in the executable's path is not used.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

PREFIX = re.compile(r'^(?:[^\t]*\t[^\t]*\t)?﻿?\d{4}-\d\d-\d\dT[\d:.]+Z ?')   # `gh run view --log`: job, step, timestamp
COLOUR = re.compile(r'(?:\x1b|\^\[)\[[0-9;]*m')                                    # gh writes ESC as the two characters ^[
STEP = re.compile(r'^##\[group\]Run (.*)$')
XTRACE = re.compile(r'^\++ (cargo .*)$')
RUNNING = re.compile(r'^\s*Running (?:unittests )?(\S+) \((\S+)\)$')
DOC = re.compile(r'^\s*Doc-tests (\S+)$')
BEGIN = re.compile(r'^running \d+ tests?$')
RESULT = re.compile(r'^test result: \w+\. (\d+) passed; (\d+) failed; (\d+) ignored; \d+ measured; (\d+) filtered out')
TEST = re.compile(r'^test (.+) \.\.\. (ok|ignored|FAILED)(?:, .*)?$')
TEST_CUT = re.compile(r'^test (.+?) \.\.\. ')       # the status comes on a later line: a child's stderr got in between
STATUS = re.compile(r'^(ok|ignored|FAILED)(?:, .*)?$')
EXE = re.compile(r'(?:^|[/\\])([^/\\]+)-[0-9a-f]{16}(?:\.exe)?$')
LIB_KINDS = {'lib', 'rlib', 'dylib', 'cdylib', 'staticlib', 'proc-macro'}
FILTERED = '(filtered out)'


def workspace(packages):
    """cargo metadata's packages -> ({library executable name: package}, {package without a library, as an executable name: package})."""
    libs, nolib = {}, {}
    for p in packages:
        names = [t['name'] for t in p['targets'] if LIB_KINDS & set(t['kind'])]
        for n in names:
            libs[n.replace('-', '_')] = p['name']
        if not names:
            nolib[p['name'].replace('-', '_')] = p['name']
    return libs, nolib


def parse(lines, libs, nolib, where):
    """One job log -> ([(executable, test name, status)], [(executable, filtered out)], [error])."""
    errors = []
    headers, blocks = [], []
    cmd_pkg = cur = None
    depth, tests, child, cut = 0, [], 0, None
    for raw in lines:
        s = COLOUR.sub('', PREFIX.sub('', raw.rstrip('\n'))).rstrip('\r␍')
        m = STEP.match(s) or XTRACE.match(s)
        if m:
            cmd_pkg = cur = None
            args = m.group(1).split()
            if args[0] == 'cargo':
                if '--' in args:
                    args = args[:args.index('--')]
                pk = [b for a, b in zip(args, args[1:]) if a in ('-p', '--package')]
                if len(pk) == 1:
                    cmd_pkg = pk[0]
            continue
        m = RUNNING.match(s)
        if m:
            src = m.group(1).replace('\\', '/')
            e = EXE.search(m.group(2))
            if not e:
                errors.append(f'{where}: no executable name in: {s.strip()}')
                continue
            exe = e.group(1)
            if cmd_pkg is None:
                if src == 'src/lib.rs':
                    cur = libs.get(exe)
                elif src.startswith('src/') and exe in nolib:
                    cur = nolib[exe]
            pkg = cmd_pkg or cur
            if pkg is None:
                errors.append(f'{where}: no package for: {s.strip()}')
                pkg = '?'
            stem = src.rsplit('/', 1)[-1].removesuffix('.rs')
            if src == 'src/lib.rs':
                target = 'lib'
            elif src.startswith('src/'):
                target = f'bin:{exe}'      # the executable's name: the same with and without -p
            elif src.startswith('tests/'):
                target = f'test:{stem}'
            else:
                target = f'{src.split("/")[0]}:{stem}'
            headers.append(f'{pkg}/{target}')
            continue
        m = DOC.match(s)
        if m:
            pkg = cmd_pkg or libs.get(m.group(1))
            if pkg is None:
                errors.append(f'{where}: no package for: {s.strip()}')
                pkg = '?'
            headers.append(f'{pkg}/doc')
            continue
        if BEGIN.match(s):
            depth += 1
            if depth == 1:
                tests, child, cut = [], 0, None
            continue
        if depth == 0:
            if RESULT.match(s):
                errors.append(f'{where}: a `test result:` line outside a block: {s}')
            continue
        m = RESULT.match(s)
        if m:
            passed, failed, ignored, filtered = map(int, m.groups())
            depth -= 1
            if depth:
                child += passed + failed + ignored
                continue
            status = {}
            for name, st in tests:
                if status.setdefault(name, st) != st:
                    errors.append(f'{where}: {name}: both {status[name]} and {st} in one executable')
            if len(status) != passed + failed + ignored or len(tests) != len(status) + child:
                errors.append(f'{where}: block {len(blocks) + 1}: {len(status)} tests on {len(tests)} lines, but the result line says '
                              f'{passed} passed, {failed} failed, {ignored} ignored (and {child} in child runs)')
            blocks.append((sorted(status.items()), filtered))
            continue
        m = TEST.match(s)
        if m:
            tests.append(m.groups())
            continue
        m = TEST_CUT.match(s)
        if m:
            cut = m.group(1)
            continue
        m = STATUS.match(s)
        if m and cut is not None:
            tests.append((cut, m.group(1)))
            cut = None
    if depth:
        errors.append(f'{where}: the log ends inside a block')
    if len(headers) != len(blocks):
        errors.append(f'{where}: {len(headers)} executables but {len(blocks)} result blocks')
    rows, exes = [], []
    for h, (tests, filtered) in zip(headers, blocks):
        exes.append((h, filtered))
        rows += [(h, name, st) for name, st in tests]
    return rows, exes, errors


def listing(logs, libs, nolib):
    """[(name, lines)] -> (sorted output lines, totals line, [error])."""
    rows, exes, errors = [], [], []
    for where, lines in logs:
        r, x, e = parse(lines, libs, nolib, where)
        rows += r
        exes += x
        errors += e
    seen = set()
    for h, _ in exes:
        if h in seen:
            errors.append(f'{h} ran twice')
        seen.add(h)
    errors += [f'{h}: {name} FAILED' for h, name, st in rows if st == 'FAILED']
    out = sorted([f'{h}\t{name}\t{st}' for h, name, st in rows] + [f'{h}\t{FILTERED}\t{n}' for h, n in exes])
    count = lambda st: sum(1 for r in rows if r[2] == st)
    totals = f'{len(exes)} executables, {count("ok")} passed, {count("ignored")} ignored, {sum(n for _, n in exes)} filtered out'
    return out, totals, errors


def self_test():
    stamp = 'job\tUNKNOWN STEP\t2026-01-01T00:00:00.0000000Z '
    libs, nolib = workspace([
        {'name': 'core', 'targets': [{'name': 'core', 'kind': ['rlib', 'staticlib']}, {'name': 'bench', 'kind': ['example']}]},
        {'name': 'cli', 'targets': [{'name': 'cli', 'kind': ['lib']}, {'name': 'shanjie-eval', 'kind': ['bin']}, {'name': 'shanjie-replay', 'kind': ['bin']}]},
        {'name': 'shanjie-win-proto', 'targets': [{'name': 'shanjie_win_proto', 'kind': ['lib']}]},
        {'name': 'shanjie-host', 'targets': [{'name': 'shanjie-host', 'kind': ['bin']}]},
        {'name': 'shanjie-tip', 'targets': [{'name': 'shanjie_tip', 'kind': ['cdylib', 'rlib']}]},
    ])
    assert nolib == {'shanjie_host': 'shanjie-host'} and libs['shanjie_tip'] == 'shanjie-tip', (libs, nolib)

    def running(src, exe, win=False):
        line = f'^[[1m^[[92m     Running^[[0m {"" if src.startswith("tests") else "unittests "}{src} (target/release/deps/{exe}-0123456789abcdef)'
        return line.replace('/', '\\').replace(')', '.exe)') if win else line

    def doc(crate):
        return f'\x1b[1m\x1b[92m   Doc-tests\x1b[0m {crate}'

    def block(tests, filtered=0):
        n = len(tests)
        ok = sum(1 for t in tests if t[1] == 'ok')
        return (['', f'running {n} test{"" if n == 1 else "s"}'] + [f'test {name} ... {st}' for name, st in tests]
                + ['', f'test result: ok. {ok} passed; 0 failed; {n - ok} ignored; 0 measured; {filtered} filtered out; finished in 0.01s', ''])

    def log(*parts):
        return [stamp + line for part in parts for line in ([part] if isinstance(part, str) else part)]

    def ahead(pairs):
        """Each header one block early, as cargo's stderr runs ahead of libtest's stdout."""
        out = [pairs[0][0]]
        for (_, b), nxt in zip(pairs, pairs[1:] + [(None, None)]):
            out += ([nxt[0]] if nxt[0] else []) + b
        return out

    # A test of shanjie-host that runs the executable again for one test, and one whose status is cut off by the child's stderr.
    host = (['', 'running 3 tests', 'test r2::child ... ok', '', 'running 1 test', 'test r2::child ... ok', '',
             'test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 2 filtered out; finished in 0.00s', '',
             'test r2::parent ... the child wrote this to stderr', 'ok', 'test r2::by_hand ... ignored, run by hand', '',
             'test result: ok. 2 passed; 0 failed; 1 ignored; 0 measured; 0 filtered out; finished in 0.10s', ''])
    cli = [(running('src/lib.rs', 'cli'), block([('judge::tests::a', 'ok')])),
           (running('src/main.rs', 'shanjie_eval'), block([('tests::flags', 'ok')])),
           (running('src/bin/replay.rs', 'shanjie_replay'), block([])),
           (running('tests/colloquial.rs', 'colloquial'), block([('chat', 'ok'), ('slow', 'ignored')])),
           (running('tests/reading_decode.rs', 'reading_decode'), block([('rows', 'ok')]))]
    core_lib = [(running('src/lib.rs', 'core'), block([('lm::tests::u', 'ok'), ('tests::timing', 'ignored')]))]
    cap = [(running('tests/engine_cap.rs', 'engine_cap'), block([('cap_40', 'ok'), ('cap_51', 'ok')]))]
    rest = [(running('tests/colloquial.rs', 'colloquial'), block([('chat', 'ok')])),
            (running('tests/reading_decode.rs', 'reading_decode'), block([('rows', 'ok'), ('table', 'ok')]))]
    host_main = [(running('src/main.rs', 'shanjie_host'), host)]
    tip = [(running('src/lib.rs', 'shanjie_tip'), block([('keys::tests::k', 'ok')]))]
    proto = [(running('src/lib.rs', 'shanjie_win_proto'), block([('wire', 'ok')]))]
    docs = {c: [(doc(c), block([('src/lib.rs - f (line 3)', 'ok')] if c == 'core' else []))] for c in ('cli', 'core', 'shanjie_tip', 'shanjie_win_proto')}

    # Before the split: the whole workspace in one command without -p (cargo's order: cli, core, shanjie-host, shanjie-tip, shanjie-win-proto, then the doc tests).
    before = log('##[group]Run cargo test --release --locked',
                 ahead(cli + core_lib + rest[:1] + cap + rest[1:] + host_main + tip + proto + docs['cli'] + docs['core'] + docs['shanjie_tip'] + docs['shanjie_win_proto']))
    # After: one -p per command, several commands of one step shown by `set -x`.
    after_cli = log('##[group]Run cargo test -p cli --release --locked', ahead(cli + docs['cli']))
    after_share = log('##[group]Run set -euo pipefail', '^[[36;1mcargo test -p cli --this-echo-of-the-script-is-not-a-command^[[0m',
                      '++ scripts/ci-shard.sh macos share',
                      '+ cargo test -p core --release --locked --lib --bins --test engine_cap', ahead(core_lib + cap),
                      '+ cargo test -p core --release --locked --doc', ahead(docs['core']),
                      '##[group]Run cargo test -p shanjie-win-proto --release --locked', ahead(proto + docs['shanjie_win_proto']),
                      '##[group]Run cargo test -p shanjie-tip --release --locked', ahead(tip + docs['shanjie_tip']),
                      '##[group]Run cargo test -p shanjie-host --release --locked', ahead(host_main))
    after_rest = log('##[group]Run set -euo pipefail', '+ cargo test -p core --release --locked --test colloquial --test reading_decode -- --skip rows_never', ahead(rest))

    want, totals, errors = listing([('before', before)], libs, nolib)
    assert not errors, errors
    assert totals == '16 executables, 15 passed, 3 ignored, 0 filtered out', totals
    for line in ('cli/test:colloquial\tchat\tok', 'core/test:colloquial\tchat\tok', 'cli/test:reading_decode\trows\tok', 'core/test:reading_decode\ttable\tok',
                 'cli/bin:shanjie_eval\ttests::flags\tok', f'cli/bin:shanjie_replay\t{FILTERED}\t0', 'core/doc\tsrc/lib.rs - f (line 3)\tok',
                 f'shanjie-tip/doc\t{FILTERED}\t0', 'shanjie-host/bin:shanjie_host\tr2::parent\tok', 'shanjie-host/bin:shanjie_host\tr2::by_hand\tignored',
                 f'shanjie-host/bin:shanjie_host\t{FILTERED}\t0', 'shanjie-win-proto/lib\twire\tok', 'core/lib\ttests::timing\tignored'):
        assert line in want, (line, want)
    assert sum(1 for line in want if line.startswith('shanjie-host/') and '\tr2::child\t' in line) == 1, want

    shards = [('cli', after_cli), ('share', after_share), ('rest', after_rest)]
    got, totals2, errors = listing(shards, libs, nolib)
    assert not errors, errors
    assert got == want and totals2 == totals, [x for x in set(got) ^ set(want)]

    # The same file in two shards is reported, and the listing shows the tests twice.
    twice = log('##[group]Run set -euo pipefail', '+ cargo test -p core --release --locked --test engine_cap', ahead(cap))
    got, _, errors = listing(shards + [('rest2', twice)], libs, nolib)
    assert errors == ['core/test:engine_cap ran twice'], errors
    assert got.count('core/test:engine_cap\tcap_40\tok') == 2, got

    # The `windows` job's step with three -p: Windows paths, each package found from the output order.
    win = log('##[group]Run cargo test --release --locked -p shanjie-win-proto -p shanjie-tip -p shanjie-host',
              ahead([(running('src/main.rs', 'shanjie_host', win=True), host), (running('src/lib.rs', 'shanjie_tip', win=True), tip[0][1]),
                     (running('src/lib.rs', 'shanjie_win_proto', win=True), proto[0][1])] + docs['shanjie_tip'] + docs['shanjie_win_proto']))
    got, totals, errors = listing([('windows', win)], libs, nolib)
    assert not errors, errors
    assert totals == '5 executables, 4 passed, 1 ignored, 0 filtered out', totals
    assert got == sorted(line for line in want if line.startswith('shanjie-')), got

    # --skip leaves its count in the listing.
    got, totals, errors = listing([('skip', log('##[group]Run cargo test -p core --test engine_cap -- --skip cap_51', ahead([(cap[0][0], block([('cap_40', 'ok')], filtered=1))])))], libs, nolib)
    assert not errors and got == ['core/test:engine_cap\t(filtered out)\t1', 'core/test:engine_cap\tcap_40\tok'], (got, errors)

    # Failures of the reading itself: a missing result block, a count that differs, a failed test, a target with no package.
    _, _, errors = listing([('short', after_cli[:-7])], libs, nolib)
    assert any('executables but' in e or 'ends inside a block' in e for e in errors), errors
    _, _, errors = listing([('count', [line for line in after_rest if 'test table' not in line])], libs, nolib)
    assert len(errors) == 1 and 'the result line says 2 passed' in errors[0], errors
    failed = log('##[group]Run cargo test -p core --test engine_cap', running('tests/engine_cap.rs', 'engine_cap'), 'running 1 test', 'test cap_40 ... FAILED',
                 'test result: FAILED. 0 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s')
    _, _, errors = listing([('failed', failed)], libs, nolib)
    assert errors == ['core/test:engine_cap: cap_40 FAILED'], errors
    _, _, errors = listing([('nopkg', log('##[group]Run set -euo pipefail', ahead(rest)))], libs, nolib)
    assert len(errors) == 2 and all('no package for' in e for e in errors), errors
    print('ci-testset.py: self-test ok')


def main(argv):
    if argv == ['--self-test']:
        return self_test()
    if not argv or any(a.startswith('-') for a in argv):
        sys.exit(__doc__)
    root = Path(__file__).resolve().parent.parent
    meta = subprocess.run(['cargo', 'metadata', '--no-deps', '--format-version', '1', '--offline'], cwd=root, check=True, capture_output=True, text=True)
    libs, nolib = workspace(json.loads(meta.stdout)['packages'])
    logs = [(a, Path(a).read_text(encoding='utf-8', errors='replace').splitlines()) for a in argv]
    out, totals, errors = listing(logs, libs, nolib)
    print('\n'.join(out))
    print(totals, file=sys.stderr)
    for e in errors:
        print(f'error: {e}', file=sys.stderr)
    sys.exit(1 if errors else 0)


if __name__ == '__main__':
    main(sys.argv[1:])
