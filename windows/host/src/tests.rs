//! docs/spec/windows-tip.md §5.11 item 10, against a real server: the production `server::start` on a test pipe name with
//! the real engine (the models of data/, fetched by scripts/fetch-models.sh as for the core's tests), and a client that opens the pipe
//! with the text service's access and identification-only flags. The tests that use the server share it and the rate limit of this process
//! (one PID), so they run one at a time. The candidate window tests (docs/spec/windows-tip.md §3.15) also drive the core directly (`Direct`: the engine
//! of `engine::load`, the host's own build, no pipe) to say what the host must hand over, and to check the DLL's key predicate against it.
use crate::pipeio::Pipe;
use crate::{engine, server, token};
use shanjie_core::ffi::{self as core_ffi, ShanjieEngine, ShanjieKey, ShanjieOutput};
use shanjie_win_proto::{self as proto, ClientMsg, Key, Out, ServerMsg};
use std::ffi::CStr;
use std::path::Path;
use std::ptr;
use std::sync::{Mutex, OnceLock, PoisonError};
use std::time::{Duration, Instant};
use windows::core::PCWSTR;
use windows::Win32::Storage::FileSystem::{CreateFileW, FILE_FLAGS_AND_ATTRIBUTES, FILE_FLAG_OVERLAPPED, FILE_SHARE_MODE, OPEN_EXISTING, SECURITY_IDENTIFICATION, SECURITY_SQOS_PRESENT};

static ONE_AT_A_TIME: Mutex<()> = Mutex::new(());

/// The R2 guard installs a silent panic hook for the whole process (guard.rs), so a plain `assert!` or `expect` fails with no
/// message. Each check names its step on stderr first (libtest prints captured stderr of a failed test); the names are test labels,
/// never composition text.
fn fail(what: &str) -> ! {
    eprintln!("check failed: {what}");
    panic!("check failed");
}

fn check(ok: bool, what: &str) {
    if !ok {
        fail(what);
    }
}

trait Need<T> {
    fn need(self, what: &str) -> T;
}

impl<T> Need<T> for Option<T> {
    fn need(self, what: &str) -> T {
        self.unwrap_or_else(|| fail(what))
    }
}

impl<T, E: std::fmt::Debug> Need<T> for Result<T, E> {
    fn need(self, what: &str) -> T {
        match self {
            Ok(v) => v,
            Err(e) => {
                eprintln!("error: {e:?}");
                fail(what)
            }
        }
    }
}

fn server_name() -> &'static [u16] {
    static NAME: OnceLock<Vec<u16>> = OnceLock::new();
    NAME.get_or_init(|| {
        let name = format!(r"\\.\pipe\shanjie-test-host-{}", std::process::id());
        check(server::start(&name, 8, token::process_facts().need("own token")), "the test server starts");
        // The shared server runs the current rules (key scheme 0, named here): the tests that use it were written for them.
        engine::install(engine::load(repo_root(), 0).need("the engine loads from data/ (scripts/fetch-models.sh)"), 0);
        name.encode_utf16().chain([0]).collect()
    })
}

fn w(s: &str) -> Vec<u16> {
    s.encode_utf16().collect()
}

/// A character key (kind 1, no modifier) and a key of the other kinds of shanjie.h.
fn chr(c: char) -> Key {
    Key { kind: 1, ch: c as u32, modifiers: 0 }
}

fn kind(kind: u32) -> Key {
    Key { kind, ch: 0, modifiers: 0 }
}

const SPACE: u32 = 2;
const ENTER: u32 = 3;
const ESC: u32 = 6;
const DOWN: u32 = 10;
const PAGE_DOWN: u32 = 15;

fn repo_root() -> &'static Path {
    Path::new(env!("CARGO_MANIFEST_DIR")).parent().and_then(Path::parent).need("repository root")
}

fn deadline() -> Option<Instant> {
    Some(Instant::now() + Duration::from_secs(10))
}

struct Client {
    pipe: Pipe,
    seq: u64,
}

impl Client {
    /// Connects and says `Hello`; returns the host's answer.
    fn hello() -> (Client, ServerMsg) {
        let name = server_name();
        // FILE_READ_DATA | FILE_WRITE_DATA | SYNCHRONIZE | READ_CONTROL, identification only: the text service's flags (client.rs).
        let access = 0x0000_0001 | 0x0000_0002 | 0x0010_0000 | 0x0002_0000;
        let flags = FILE_FLAGS_AND_ATTRIBUTES(FILE_FLAG_OVERLAPPED.0 | SECURITY_SQOS_PRESENT.0 | SECURITY_IDENTIFICATION.0);
        // SAFETY: NUL-terminated name.
        let h = unsafe { CreateFileW(PCWSTR(name.as_ptr()), access, FILE_SHARE_MODE(0), None, OPEN_EXISTING, flags, None) }.need("the test pipe opens");
        let mut c = Client { pipe: Pipe::new(h).need("event"), seq: 0 };
        let m = c.call(&ClientMsg::Hello { build_hash: proto::BUILD_HASH, pid: std::process::id(), tid: 0 });
        (c, m)
    }

    /// A connection that got `Welcome`. Slots of connections just closed are given back when the host sees the close, so a
    /// `Refuse { 2 }` is retried for a moment.
    fn welcomed() -> Client {
        let until = Instant::now() + Duration::from_secs(10);
        loop {
            match Client::hello() {
                (c, ServerMsg::Welcome { .. }) => return c,
                (_, ServerMsg::Refuse { reason: proto::REFUSE_LIMIT }) if Instant::now() < until => std::thread::sleep(Duration::from_millis(20)),
                _ => fail("the host did not welcome the client"),
            }
        }
    }

    fn send(&mut self, m: &ClientMsg) -> u64 {
        self.seq += 1;
        self.pipe.write_all(&proto::encode_client(self.seq, m).need("encode"), deadline()).need("write");
        self.seq
    }

    fn call(&mut self, m: &ClientMsg) -> ServerMsg {
        let seq = self.send(m);
        let f = self.pipe.read_frame(deadline()).need("an answer");
        let (s, m) = proto::decode_server(&f).need("a well-formed answer");
        check(s == seq, "the answer carries the seq of the request");
        m
    }

    fn key(&mut self, ctx: u64, ch: char) -> Out {
        self.keyk(ctx, chr(ch))
    }

    fn keyk(&mut self, ctx: u64, key: Key) -> Out {
        match self.call(&ClientMsg::Key { ctx, key }) {
            ServerMsg::Out(o) => {
                check(o.ran && !o.core_error && proto::out_ok(&o), "a usable Out");
                o
            }
            _ => fail("the host did not answer the key with an Out"),
        }
    }
}

#[test]
fn another_connection_cannot_reach_a_composition_through_its_ctx() {
    let _one = ONE_AT_A_TIME.lock().unwrap_or_else(PoisonError::into_inner);
    let mut a = Client::welcomed();
    let mut b = Client::welcomed();
    let ctx = 7;
    a.key(ctx, 's');
    let o = a.key(ctx, 'u');
    check(o.preedit == w("ㄋㄧ"), "A composes ㄋㄧ");
    // B's Release and Reset name A's ctx but are B's: A's composition is untouched.
    b.send(&ClientMsg::Release { ctx });
    b.send(&ClientMsg::Reset { ctx });
    // Release and Reset have no answer; give B's connection thread time to act on them before A's next key.
    std::thread::sleep(Duration::from_millis(300));
    let o = a.key(ctx, '3');
    check(!o.took_over, "A still owns the engine after B's Release and Reset");
    check(o.preedit == w("你"), "A's composition went on");
    // B's key with A's ctx is B's own: it takes the engine (A's state is discarded, §5.5 item 8) and sees none of A's text.
    let ob = b.key(ctx, 'c');
    check(ob.took_over && ob.commit.is_empty() && ob.preedit == w("ㄏ"), "B gets only its own key");
    // A sees that it lost the engine.
    check(a.key(ctx, 'l').took_over, "A is told the engine changed hands");
}

#[test]
fn a_rate_limited_client_gets_a_usable_connection_next() {
    let _one = ONE_AT_A_TIME.lock().unwrap_or_else(PoisonError::into_inner);
    let mut held: Vec<Client> = (0..4).map(|_| Client::welcomed()).collect();
    let (fifth, m) = Client::hello();
    check(m == ServerMsg::Refuse { reason: proto::REFUSE_LIMIT }, "the fifth connection of one client is refused with reason 2");
    drop(fifth);
    drop(held.pop());
    let mut c = Client::welcomed();
    let o = c.key(1, 's');
    check(o.preedit == w("ㄋ"), "the next connection types");
}

// ---- the candidate window (docs/spec/windows-tip.md §3.15) ----

/// What one core output holds, copied and freed at once.
#[derive(Clone, PartialEq)]
struct Snap {
    handled: bool,
    commit: Vec<u16>,
    preedit: Vec<u16>,
    cursor: u32,
    selected: i32,
    first: u32,
    total: u32,
    candidates: Vec<Vec<u16>>,
}

/// An engine of the host's own build (`engine::load`: the packs, the vertical window, the rerank off, the learning off), called with no pipe in between.
struct Direct(*mut ShanjieEngine);

impl Direct {
    /// Key scheme 0, named: the existing tests were written for the current rules.
    fn new() -> Direct {
        Direct::with_scheme(0)
    }

    fn with_scheme(scheme: u32) -> Direct {
        Direct(engine::load(repo_root(), scheme).need("the engine loads from data/ (scripts/fetch-models.sh)"))
    }

    fn key(&self, k: Key) -> Snap {
        let mut o: *mut ShanjieOutput = ptr::null_mut();
        // SAFETY: a live engine of this struct; `o` is a valid out pointer.
        let rc = unsafe { core_ffi::shanjie_engine_key(self.0, ShanjieKey { kind: k.kind, ch: k.ch, modifiers: k.modifiers }, &mut o) };
        check(rc == 0 && !o.is_null(), "the core accepts the key");
        // SAFETY: a live output; its strings are valid NUL-terminated UTF-8 until freed, and it is freed once, after the copy.
        unsafe {
            let r = &*o;
            let s = |p: *const std::os::raw::c_char| CStr::from_ptr(p).to_string_lossy().encode_utf16().collect::<Vec<u16>>();
            let candidates = if r.candidates.is_null() { Vec::new() } else { (0..r.candidate_count as usize).map(|i| s(*r.candidates.add(i))).collect() };
            let snap = Snap { handled: r.handled != 0, commit: s(r.commit), preedit: s(r.preedit), cursor: r.cursor_utf16, selected: r.candidate_selected, first: r.candidate_first, total: r.candidate_total, candidates };
            core_ffi::shanjie_output_free(o);
            snap
        }
    }

    fn reset(&self) {
        let mut o: *mut ShanjieOutput = ptr::null_mut();
        // SAFETY: a live engine; the snapshot is freed at once.
        unsafe {
            check(core_ffi::shanjie_engine_reset(self.0, 1, &mut o) == 0, "the core resets");
            core_ffi::shanjie_output_free(o);
        }
    }
}

impl Drop for Direct {
    fn drop(&mut self) {
        // SAFETY: the engine of `new`, freed once.
        unsafe { core_ffi::shanjie_engine_free(self.0) };
    }
}

/// The host's answer is the core's output: the rows, the selection, first and total, the text and the caret.
fn same(o: &Out, d: &Snap, step: &str) {
    check(o.handled == d.handled && o.commit == d.commit && o.preedit == d.preedit, &format!("{step}: handled, commit and preedit equal the core's"));
    check(o.candidate_selected == d.selected, &format!("{step}: candidate_selected equals the core's"));
    if d.selected >= 0 {
        check(o.candidates == d.candidates && o.candidate_first == d.first && o.candidate_total == d.total, &format!("{step}: rows, first and total equal the core's"));
    } else {
        check(o.candidates.is_empty() && o.candidate_first == 0 && o.candidate_total == 0, &format!("{step}: a closed window carries nothing"));
    }
}

#[test]
fn the_window_the_host_sends_is_the_one_the_core_gives() {
    let _one = ONE_AT_A_TIME.lock().unwrap_or_else(PoisonError::into_inner);
    let mut a = Client::welcomed();
    let core = Direct::new();
    let ctx = 5;
    // Compose 你, then Space opens the vertical window.
    for c in ['s', 'u', '3'] {
        same(&a.key(ctx, c), &core.key(chr(c)), "compose");
    }
    let (o, d) = (a.keyk(ctx, kind(SPACE)), core.key(kind(SPACE)));
    same(&o, &d, "Space");
    check(o.candidate_selected == 0 && !o.candidates.is_empty() && o.candidates.len() <= 9 && o.candidate_total as usize >= o.candidates.len(), "Space opens a window at its first row");
    check(d.selected == 0 && d.first == 0, "the core opened it at the first row");
    // Down moves, Page Down (kind 15) turns the page when the list is longer than one page, Esc closes.
    same(&a.keyk(ctx, kind(DOWN)), &core.key(kind(DOWN)), "Down");
    let (o, d) = (a.keyk(ctx, kind(PAGE_DOWN)), core.key(kind(PAGE_DOWN)));
    same(&o, &d, "Page Down");
    let (o, d) = (a.keyk(ctx, kind(ESC)), core.key(kind(ESC)));
    same(&o, &d, "Esc");
    check(o.candidate_selected == -1 && o.candidates.is_empty() && !o.preedit.is_empty(), "Esc closes the window and keeps the composition");
    // The same through the number key: Down opens, 2 picks the second row (the core's output equal at each step).
    let (o, d) = (a.keyk(ctx, kind(DOWN)), core.key(kind(DOWN)));
    same(&o, &d, "Down opens");
    check(o.candidate_selected >= 0, "Down opens the window");
    same(&a.key(ctx, '2'), &core.key(chr('2')), "digit");
}

#[test]
fn a_connection_never_gets_the_candidates_of_a_window_it_does_not_own() {
    let _one = ONE_AT_A_TIME.lock().unwrap_or_else(PoisonError::into_inner);
    let mut a = Client::welcomed();
    let mut b = Client::welcomed();
    let ctx = 9;
    for c in ['s', 'u', '3'] {
        a.key(ctx, c);
    }
    let oa = a.keyk(ctx, kind(SPACE));
    check(oa.candidate_selected >= 0 && oa.candidates.len() > 1, "A has a window open");
    let hit = |o: &Out, what: &str| {
        check(o.candidate_selected == -1 && o.candidates.is_empty() && o.candidate_first == 0 && o.candidate_total == 0, &format!("{what}: no window"));
        for c in &oa.candidates {
            check(!o.commit.windows(c.len()).any(|x| x == c.as_slice()) && !o.preedit.windows(c.len()).any(|x| x == c.as_slice()), &format!("{what}: no row of A's window in the text"));
        }
    };
    // B names A's ctx with a Release and a Reset (no effect), then types with it: B takes the engine and sees none of A's rows.
    b.send(&ClientMsg::Release { ctx });
    b.send(&ClientMsg::Reset { ctx });
    std::thread::sleep(Duration::from_millis(300));
    let ob = b.key(ctx, 'c');
    check(ob.took_over, "B took the engine");
    hit(&ob, "B's first key");
    // B's own window holds B's rows, none of A's.
    b.key(ctx, 'l');
    b.key(ctx, '3');
    let obw = b.keyk(ctx, kind(SPACE));
    check(obw.candidate_selected >= 0, "B has a window of its own");
    for c in &obw.candidates {
        check(!oa.candidates.contains(c), "B's window shares no row with A's");
    }
    // A, back on the engine, is told, and gets none of B's rows.
    let oa2 = a.key(ctx, 'x');
    check(oa2.took_over, "A is told the engine changed hands");
    for c in &obw.candidates {
        check(!oa2.candidates.contains(c), "A gets no row of B's window");
    }
}

/// The keys of the VM run and of `replay_keys_vertical`, one token each, separated by spaces: a character is its keycap on the standard
/// layout, `space`, `enter`, `esc`, `down`, `up`, `pgup`, `pgdn`, `bksp` are the other keys. The text: ㄇㄧㄥˊ ㄊㄧㄢ ㄗㄠˇ ㄕㄤˋ (明天早上, from the first row of
/// `eval/dev/user-typing.txt`), Down opens the window, two more Downs, `3` picks the third row, ㄨㄛˇ ㄧㄠˋ, Space opens the window, Enter picks the selected row, Enter sends the
/// composition. It covers opening, Down, a number pick and an Enter pick. The VM driver reads this same line.
const REPLAY_KEYS_VERTICAL: &str = "a u / 6 w u 0 space y l 3 g ; 4 down down down 3 j i 3 u l 4 space enter enter";

/// The text the core sends for `REPLAY_KEYS_VERTICAL`, UTF-16 units as `\uXXXX`. EMPTY until it is filled in: run
/// `cargo test -p shanjie-host print_replay_keys_vertical -- --ignored --nocapture` once (on the machine that has the models) and paste the `const` line it prints over this one (a raw string).
const REPLAY_KEYS_VERTICAL_EXPECTED: &str = r"\u660e\u5929\u65e9\u5c1a\u6211\u8981";

fn parse_keys(line: &str) -> Vec<Key> {
    line.split(' ')
        .map(|t| {
            // `S+` Shift, `C+` Control (MOD_SHIFT 1, MOD_CONTROL 2 of shanjie.h), in front of any other token.
            let (modifiers, t) = match (t.strip_prefix("S+"), t.strip_prefix("C+")) {
                (Some(r), _) => (1, r),
                (_, Some(r)) => (2, r),
                _ => (0, t),
            };
            let k = match t {
                "space" => kind(SPACE),
                "enter" => kind(ENTER),
                "bksp" => kind(4),
                "del" => kind(5),
                "esc" => kind(ESC),
                "left" => kind(7),
                "right" => kind(8),
                "up" => kind(9),
                "down" => kind(DOWN),
                "home" => kind(11),
                "end" => kind(12),
                "tab" => kind(13),
                "pgup" => kind(14),
                "pgdn" => kind(PAGE_DOWN),
                t if t.chars().count() == 1 => chr(t.chars().next().need("a character")),
                _ => fail("an unknown key token"),
            };
            Key { modifiers, ..k }
        })
        .collect()
}

/// Feeds the keys to a fresh engine and returns everything it sent as text; the composition is empty at the end, and a window opened on the way iff `want_window`.
fn replay(line: &str, want_window: bool) -> Vec<u16> {
    replay_on(&Direct::new(), line, want_window)
}

fn replay_on(core: &Direct, line: &str, want_window: bool) -> Vec<u16> {
    let (mut text, mut saw_window) = (Vec::new(), false);
    let mut last = None;
    for k in parse_keys(line) {
        let s = core.key(k);
        text.extend_from_slice(&s.commit);
        saw_window |= s.selected >= 0;
        last = Some(s);
    }
    check(saw_window == want_window, "a candidate window opened during the replay exactly when expected");
    check(last.is_some_and(|l| l.preedit.is_empty() && l.selected == -1), "the replay ends with nothing composed and no window");
    text
}

fn escape(u: &[u16]) -> String {
    u.iter().map(|x| format!("\\u{x:04x}")).collect()
}

fn unescape(s: &str) -> Vec<u16> {
    s.split("\\u").skip(1).map(|h| u16::from_str_radix(h, 16).need("hex digits")).collect()
}

#[test]
fn replay_keys_vertical() {
    check(!REPLAY_KEYS_VERTICAL_EXPECTED.is_empty(), "REPLAY_KEYS_VERTICAL_EXPECTED is not filled in: run print_replay_keys_vertical --ignored --nocapture and paste its line");
    check(replay(REPLAY_KEYS_VERTICAL, true) == unescape(REPLAY_KEYS_VERTICAL_EXPECTED), "the replay sends the expected text");
}

/// Prints the one line `replay_keys_vertical` expects. The sentence is public (a row of eval/dev/user-typing.txt), so this is the one test that prints.
#[test]
#[ignore = "run by hand: prints the expected text of replay_keys_vertical"]
fn print_replay_keys_vertical() {
    println!("const REPLAY_KEYS_VERTICAL_EXPECTED: &str = r\"{}\";", escape(&replay(REPLAY_KEYS_VERTICAL, true)));
}

/// The first sitting of the warm-up measurement (docs/spec/windows-tip.md §5.2): ㄨㄛˇ ㄇㄣˊ ㄑㄩˋ ㄔ ㄈㄢˋ, no syllable in common with the warm-up's, sent with Enter and no window.
const FIRST_KEYS: &str = "j i 3 a p 6 f m 4 t space z 0 4 enter";
/// The text the core sends for `FIRST_KEYS`, UTF-16 units as `\uXXXX`.
const FIRST_KEYS_EXPECTED: &str = r"\u6211\u5011\u53bb\u5403\u98ef";

#[test]
fn replay_first_keys() {
    check(replay(FIRST_KEYS, false) == unescape(FIRST_KEYS_EXPECTED), "the replay sends the expected text");
}

/// After `engine::warm_with` the engine holds nothing (no composition, no window, nothing to commit), and the next replay sends what a cold engine sends.
#[test]
fn warm_up_leaves_no_trace() {
    let core = Direct::new();
    // SAFETY: a live engine of this test, used on this thread only.
    check(unsafe { engine::warm_with(core.0, &engine::WARM_KEYS) }, "the warm-up resets");
    let o = core.key(kind(ENTER));
    check(o.commit.is_empty() && o.preedit.is_empty() && o.selected == -1, "nothing is composed or committed after the warm-up");
    check(replay_on(&core, REPLAY_KEYS_VERTICAL, true) == unescape(REPLAY_KEYS_VERTICAL_EXPECTED), "the replay after the warm-up sends the expected text");
    check(replay_on(&core, FIRST_KEYS, false) == unescape(FIRST_KEYS_EXPECTED), "the first keys after the warm-up send the expected text");
}

/// A key the core refuses in the middle of the warm-up (a lone surrogate is not a Unicode scalar: return code 2) stops it with a composition pending; the reset still empties the engine.
#[test]
fn warm_up_failing_midway_still_resets() {
    let core = Direct::new();
    let bad = Key { kind: 1, ch: 0xD800, modifiers: 0 };
    let mut o: *mut ShanjieOutput = ptr::null_mut();
    // SAFETY: a live engine; the output, if any, is freed at once.
    let rc = unsafe {
        let rc = core_ffi::shanjie_engine_key(core.0, ShanjieKey { kind: bad.kind, ch: bad.ch, modifiers: bad.modifiers }, &mut o);
        if !o.is_null() {
            core_ffi::shanjie_output_free(o);
        }
        rc
    };
    check(rc != 0, "the core refuses the injected key");
    // SAFETY: as above.
    check(unsafe { engine::warm_with(core.0, &[chr('a'), chr('u'), bad, chr('6')]) }, "the reset after the failed warm-up works");
    let o = core.key(kind(ENTER));
    check(o.commit.is_empty() && o.preedit.is_empty() && o.selected == -1, "nothing is composed or committed after the failed warm-up");
}

/// The virtual keys of the text service's mapping table, the digits, letters and punctuation keys, the page keys, and the unmapped ones.
fn all_vks() -> Vec<u32> {
    let mut vks: Vec<u32> = vec![0x20, 0x0D, 0x08, 0x2E, 0x1B, 0x25, 0x27, 0x26, 0x28, 0x24, 0x23, 0x09, 0x21, 0x22, 0x70, 0x60, 0x10, 0x11, 0x12, 0xE5];
    vks.extend(0x30..=0x39);
    vks.extend(0x41..=0x5A);
    vks.extend([0xBA, 0xBF, 0xC0, 0xDB, 0xDC, 0xDD, 0xDE, 0xBB, 0xBD, 0xBC, 0xBE]);
    vks
}

/// An unfinished syllable ㄋ.
fn open_syllable(core: &Direct) {
    core.reset();
    core.key(chr('s'));
}

/// The composition ㄋㄧˇ (你).
fn open_composition(core: &Direct) {
    core.reset();
    for c in ['s', 'u', '3'] {
        core.key(chr(c));
    }
}

/// Opens the window of ㄋㄧˇ: Down opens it in both schemes (in scheme 1 Space types a space unit).
fn open_window(core: &Direct) {
    core.reset();
    for c in ['s', 'u', '3'] {
        core.key(chr(c));
    }
    let o = core.key(kind(DOWN));
    check(o.selected >= 0 && !o.candidates.is_empty(), "the window opens");
}

/// docs/spec/windows-tip.md §3.6: the text service's key predicate against the real core, with a candidate window open, in both key schemes. Every virtual key of the mapping
/// table, the digits, letters and punctuation keys, the page keys, and the unmapped ones, with every Shift, Ctrl and Caps Lock combination
/// (Alt and Win keys are never sent): whenever the core handles the key, `OnTestKeyDown` must say it may be eaten, and the page keys map only with no modifier.
#[test]
fn window_keys_match_the_core() {
    use crate::keys::{map_key, may_eat, Mods};
    let vks = all_vks();
    for scheme in [0u32, 1] {
        let core = Direct::with_scheme(scheme);
        let (mut combos, mut handled_n) = (0u32, 0u32);
        for &vk in &vks {
            for bits in 0..8u32 {
                let mods = Mods { shift: bits & 1 != 0, ctrl: bits & 2 != 0, caps: bits & 4 != 0, ..Mods::default() };
                let page = vk == 0x21 || vk == 0x22;
                let k = map_key(vk, mods, true);
                if page {
                    check(k.is_some() == (bits == 0), &format!("vk {vk:#x} mods {bits}: a page key maps only with no modifier"));
                    check(map_key(vk, mods, false).is_none(), &format!("vk {vk:#x} mods {bits}: a page key never maps without a window"));
                }
                let Some(k) = k else { continue };
                open_window(&core);
                let s = core.key(k);
                combos += 1;
                if s.handled {
                    handled_n += 1;
                    check(may_eat(scheme as u8, false, true, &k), &format!("scheme {scheme} vk {vk:#x} mods {bits}: the core handles it, OnTestKeyDown must say TRUE"));
                }
                if page {
                    check(s.handled, &format!("vk {vk:#x}: the core handles a page key with a window open"));
                }
            }
        }
        // The keys of rules 3v-8 are handled and may be eaten.
        for c in '1'..='9' {
            open_window(&core);
            check(core.key(chr(c)).handled && may_eat(scheme as u8, false, true, &chr(c)), "a digit");
        }
        for k in [SPACE, ENTER, ESC, 4, DOWN, 9, 7, 8, 14, 15] {
            open_window(&core);
            check(core.key(kind(k)).handled && may_eat(scheme as u8, false, true, &kind(k)), "a window key");
        }
        println!("scheme {scheme}: {combos} combinations with a window open, {handled_n} handled by the core, 0 where the core handles a key OnTestKeyDown refuses");
    }
}

/// docs/spec/windows-tip.md §3.6 (A3): `may_eat(scheme, composing, window, key)` against the real core, both schemes, every virtual key of the table with every Shift, Ctrl and
/// Caps Lock combination. With an empty composition both ways: no key `may_eat` says TRUE for that the core passes on (not handled, nothing committed), and no key the core
/// takes (handled, or something committed) that it says FALSE for. With an unfinished syllable, a composition or a window open: only the way "the core takes it, so `may_eat` is TRUE".
#[test]
fn may_eat_matches_the_core_in_both_schemes() {
    use crate::keys::{map_key, may_eat, may_eat_either, Mods};
    let vks = all_vks();
    for scheme in [0u32, 1] {
        let core = Direct::with_scheme(scheme);
        let sc = scheme as u8;
        let states: [(&str, fn(&Direct), bool, bool); 4] = [
            ("empty", Direct::reset, false, false),
            ("syllable", open_syllable, true, false),
            ("composition", open_composition, true, false),
            ("window", open_window, true, true),
        ];
        let (mut combos, mut eaten, mut taken) = (0u32, 0u32, 0u32);
        for (name, prepare, composing, window) in states {
            for &vk in &vks {
                for bits in 0..8u32 {
                    let mods = Mods { shift: bits & 1 != 0, ctrl: bits & 2 != 0, caps: bits & 4 != 0, ..Mods::default() };
                    let Some(k) = map_key(vk, mods, window) else { continue };
                    prepare(&core);
                    let s = core.key(k);
                    let core_takes = s.handled || !s.commit.is_empty();
                    let eat = may_eat(sc, composing, window, &k);
                    combos += 1;
                    eaten += u32::from(eat);
                    taken += u32::from(core_takes);
                    let what = format!("scheme {scheme}, {name}, vk {vk:#x}, mods {bits}");
                    check(!core_takes || eat, &format!("{what}: the core takes the key, OnTestKeyDown must say TRUE"));
                    if !composing && !window {
                        check(!eat || core_takes, &format!("{what}: OnTestKeyDown says TRUE, the core passes the key on (an IMM32 host would lose it)"));
                        // A thread with no connection yet refuses without connecting exactly the keys neither scheme takes.
                        check(may_eat_either(&k) || !eat, &format!("{what}: may_eat_either is TRUE whenever the scheme's answer is"));
                    }
                }
            }
        }
        println!("scheme {scheme}: {combos} combinations, {eaten} eaten by may_eat, {taken} taken by the core, 0 disagreements");
    }
}

// ---- key scheme 1 (docs/spec/keys-and-composition.md §4.1.1, docs/spec/windows-tip.md §3.4, §5.2, §5.3) ----

/// The segments of the VM runs, one per row of the scheme 1 rules and the cells run in the Run dialog, one token each separated by spaces (the tokens of `parse_keys`; `S+` and `C+` are Shift and
/// Control). Every segment starts from an empty composition. `REPLAY_KEYS_MS` (scheme 1, expectations) and `print_vm_keys_scheme0` (scheme 0, the expected values of the switch-back run)
/// both read this table, and the VM driver copies its key strings from it: a string is defined here once. The last three are the cells of the Run dialog, whose
/// composition cannot be read back (the strings stop before the Enter that would run the text).
const VM_KEY_STRINGS: &[(&str, &str)] = &[
    ("enter-on-syllable", "s enter"),
    ("shift-enter-on-syllable", "s S+enter"),
    ("space-on-syllable", "s space"),
    ("shift-a-on-syllable", "s S+a"),
    ("shift-a-after-unit", "s u 3 s S+a"),
    ("shift-comma-on-syllable", "s S+,"),
    ("shift-period-on-syllable", "s S+."),
    ("shift-comma-after-unit", "s u 3 s S+,"),
    ("shift-comma-in-composition", "s u 3 S+,"),
    ("shift-period-in-composition", "s u 3 S+."),
    ("shift-comma-empty", "S+,"),
    ("shift-period-empty", "S+."),
    ("space-in-composition", "s u 3 space j i 3 down"),
    ("tab-in-composition", "s u 3 tab"),
    ("shift-tab-in-composition", "s u 3 S+tab"),
    ("shift-a-in-composition", "s u 3 S+a j i 3 bksp"),
    ("shift-a-in-composition-backspaces", "s u 3 S+a j i 3 bksp bksp"),
    ("tone-3", "s u 3 3"),
    ("tone-4", "s u 3 4"),
    ("tone-6", "s u 3 6"),
    ("tone-7", "s u 3 7"),
    ("up-in-composition", "s u 3 up"),
    ("up-twice-in-composition", "s u 3 up up"),
    ("up-three-times-in-composition", "s u 3 up up up"),
    ("up-then-down-in-composition", "s u 3 up down"),
    ("down-esc-then-up-in-composition", "s u 3 down esc up"),
    ("up-twice-then-enter", "s u 3 up up enter"),
    ("up-on-wo3", "j i 3 up"),
    ("up-twice-on-wo3", "j i 3 up up"),
    ("up-three-times-on-wo3", "j i 3 up up up"),
    ("up-on-qu4", "f m 4 up"),
    ("up-twice-on-qu4", "f m 4 up up"),
    ("up-three-times-on-qu4", "f m 4 up up up"),
    ("up-on-ni3-wo3", "s u 3 j i 3 up"),
    ("up-twice-on-ni3-wo3", "s u 3 j i 3 up up"),
    ("up-three-times-on-ni3-wo3", "s u 3 j i 3 up up up"),
    ("up-on-wo3-men2", "j i 3 a p 6 up"),
    ("up-twice-on-wo3-men2", "j i 3 a p 6 up up"),
    ("up-three-times-on-wo3-men2", "j i 3 a p 6 up up up"),
    ("down-on-wo3-men2", "j i 3 a p 6 down"),
    ("shift-enter-in-composition", "s u 3 S+enter"),
    ("ctrl-comma-empty", "C+,"),
    ("ctrl-period-empty", "C+."),
    ("ctrl-comma-on-syllable", "s C+,"),
    ("ctrl-period-in-composition", "s u 3 C+."),
    ("ctrl-comma-in-window", "s u 3 down C+,"),
    ("backspace-in-window", "s u 3 down bksp"),
    ("up-in-window-at-first-row", "s u 3 down up"),
    ("up-in-window-across-a-page", "s u 3 down pgdn up"),
    ("space-in-window-keeps-no-position", "s u 3 down down space"),
    ("page-down-in-window-twice", "s u 3 down pgdn pgdn"),
    ("page-down-in-window-after-the-last-page", "s u 3 down end pgdn"),
    ("space-in-window-after-the-last-page", "s u 3 down end space"),
    ("page-up-in-window-at-first-page", "s u 3 down pgup"),
    ("down-in-window-across-a-page", "s u 3 down down down down down down down down down down"),
    ("down-in-window-at-the-last-row", "s u 3 down end down"),
    ("tab-in-window", "s u 3 down tab"),
    ("shift-tab-in-window", "s u 3 down S+tab"),
    ("right-in-window", "s u 3 down right"),
    ("left-in-window", "s u 3 down down left"),
    ("home-in-window", "s u 3 down home"),
    ("delete-in-window", "s u 3 down del"),
    ("end-in-window", "s u 3 down end"),
    ("shift-enter-in-window", "s u 3 down down S+enter"),
    ("zhuyin-a-in-window", "s u 3 down a"),
    ("zhuyin-semicolon-in-window", "s u 3 down ;"),
    ("zhuyin-zero-in-window", "s u 3 down 0"),
    ("zhuyin-minus-in-window", "s u 3 down -"),
    ("shift-a-in-window", "s u 3 down S+a"),
    ("shift-comma-in-window", "s u 3 down S+,"),
    ("shift-period-in-window", "s u 3 down S+."),
    // The cells of the Run dialog (docs/windows/ms-zhuyin-ref.md): the same strings as above, named for the dialog.
    ("run-dialog-space-then-zhuyin", "s u 3 space j i 3 down"),
    ("run-dialog-shift-a-after-unit", "s u 3 s S+a"),
    ("run-dialog-shift-comma-after-unit", "s u 3 s S+,"),
];

/// What a segment of `REPLAY_KEYS_MS` ends with.
enum Want {
    /// Everything the segment committed, the last composition, whether the last key was handled, and whether a window is open at the end. A text is that of Microsoft Zhuyin's measurement
    /// (docs/windows/ms-zhuyin-ref.md), except where the scheme 1 rules differ on purpose: Shift+Enter in a composition (sent and handled), and the characters
    /// of the K18 Up swaps, which come from this lexicon's lists (the positions are Microsoft Zhuyin's).
    Text { commit: &'static str, preedit: &'static str, handled: bool, window: bool },
    /// A rule about the window, judged on the outputs just before and after the last key: no character is written down, the window's own total says where the rule points.
    Window(fn(&Snap, &Snap) -> bool),
}

use Want::{Text, Window};

/// First row of the last page of a list of `total`.
fn last_first(total: u32) -> u32 {
    (total.max(1) - 1) / 9 * 9
}
/// K18 (rules 16u, 15d): the window has just opened (none before the key), on the first row of the first page, and its first item is the text the composition
/// shows over that item's span (the composition ends with it; the cursor is at the end in these segments).
fn opens_with_the_shown_text_first(before: &Snap, after: &Snap) -> bool {
    before.selected < 0 && after.selected == 0 && after.first == 0 && after.candidates.first().is_some_and(|c| !c.is_empty() && after.preedit.ends_with(c))
}
/// The window, with a second page at least, is untouched by the key: the outputs equal.
fn window_stays(before: &Snap, after: &Snap) -> bool {
    before.selected == 0 && before.total > 9 && after == before
}
/// From the first row of a list of three pages or more: the last row of the last page.
fn jumps_to_the_last_row(before: &Snap, after: &Snap) -> bool {
    before.selected == 0 && before.first == 0 && before.total > 18 && after.first == last_first(before.total) && after.first + after.selected as u32 == before.total - 1
}
/// From the second page: the row before it, on the first page, still a whole page from 0.
fn up_to_the_previous_page(before: &Snap, after: &Snap) -> bool {
    before.first == 9 && before.selected == 0 && after.first == 0 && after.selected == 8
}
/// From the second row of the first page: the first row of the next page, the position inside the page is not kept.
fn next_page_first_row(before: &Snap, after: &Snap) -> bool {
    before.first == 0 && before.selected == 1 && before.total > 9 && after.first == 9 && after.selected == 0
}
/// From the last page: the first row of the first page.
fn wraps_to_the_first_page(before: &Snap, after: &Snap) -> bool {
    before.first == last_first(before.total) && before.first > 0 && after.first == 0 && after.selected == 0
}
/// From the first page: the first row of the last page.
fn page_up_wraps_to_the_last_page(before: &Snap, after: &Snap) -> bool {
    before.first == 0 && before.total > 9 && after.first == last_first(before.total) && after.selected == 0
}
/// From the second page, a list of three pages or more: the first row of the third page (scheme 0 clamps the last move to the last nine rows).
fn page_down_to_the_third_page(before: &Snap, after: &Snap) -> bool {
    before.first == 9 && before.selected == 0 && before.total > 18 && after.first == 18 && after.selected == 0
}
/// The ninth row, then Down: the first row of the next page.
fn down_across_the_page(before: &Snap, after: &Snap) -> bool {
    before.first == 0 && before.selected == 8 && before.total > 9 && after.first == 9 && after.selected == 0
}
/// The very last row, then Down: back to the first row of the first page.
fn down_at_the_last_row(before: &Snap, after: &Snap) -> bool {
    before.first == last_first(before.total) && before.first + before.selected as u32 == before.total - 1 && after.first == 0 && after.selected == 0
}
/// The window, on its second row, is untouched by the key (scheme 0 moves the selection to the first row for Left).
fn window_stays_on_the_second_row(before: &Snap, after: &Snap) -> bool {
    before.selected == 1 && before.first == 0 && before.total > 9 && after == before
}
/// From the first row: the last row of the last page.
fn end_goes_to_the_last_row(before: &Snap, after: &Snap) -> bool {
    before.selected == 0 && before.total > 9 && after.first == last_first(before.total) && after.first + after.selected as u32 == before.total - 1
}

/// Scheme 1's expectations, one per segment of `VM_KEY_STRINGS` (not the Run dialog's three, which repeat strings above), by name. The two cells a Microsoft Zhuyin measurement and the scheme 1 rules
/// part on are named in `Want::Text`. Each row of the scheme 1 rules (`m` rows of docs/spec/keys-and-composition.md §4.1.1) is touched by at least one segment; the 128-unit limit is
/// `core/tests/engine_scheme.rs`'s.
const REPLAY_KEYS_MS: &[(&str, Want)] = &[
    ("enter-on-syllable", Text { commit: "", preedit: "ㄋ", handled: true, window: false }),
    ("shift-enter-on-syllable", Text { commit: "", preedit: "ㄋ", handled: true, window: false }),
    ("space-on-syllable", Text { commit: "", preedit: "ㄋ", handled: true, window: false }),
    ("shift-a-on-syllable", Text { commit: "A", preedit: "", handled: true, window: false }),
    ("shift-a-after-unit", Text { commit: "", preedit: "你A", handled: true, window: false }),
    ("shift-comma-on-syllable", Text { commit: "<", preedit: "", handled: true, window: false }),
    ("shift-period-on-syllable", Text { commit: ">", preedit: "", handled: true, window: false }),
    ("shift-comma-after-unit", Text { commit: "", preedit: "你<", handled: true, window: false }),
    ("shift-comma-in-composition", Text { commit: "", preedit: "你<", handled: true, window: false }),
    ("shift-period-in-composition", Text { commit: "", preedit: "你>", handled: true, window: false }),
    ("shift-comma-empty", Text { commit: "", preedit: "", handled: false, window: false }),
    ("shift-period-empty", Text { commit: "", preedit: "", handled: false, window: false }),
    ("space-in-composition", Text { commit: "", preedit: "你 我", handled: true, window: true }),
    ("tab-in-composition", Text { commit: "", preedit: "你", handled: true, window: false }),
    ("shift-tab-in-composition", Text { commit: "", preedit: "你", handled: true, window: false }),
    ("shift-a-in-composition", Text { commit: "", preedit: "你A", handled: true, window: false }),
    ("shift-a-in-composition-backspaces", Text { commit: "", preedit: "你", handled: true, window: false }),
    ("tone-3", Text { commit: "", preedit: "你ˇ", handled: true, window: false }),
    ("tone-4", Text { commit: "", preedit: "你ˋ", handled: true, window: false }),
    ("tone-6", Text { commit: "", preedit: "你ˊ", handled: true, window: false }),
    ("tone-7", Text { commit: "", preedit: "你˙", handled: true, window: false }),
    // K18 (rules 16u, 15d): the swaps are the 3rd and the 2nd item of [shown, the syllable's singles], the 3rd press and Down open with the shown text first.
    // The characters are this lexicon's (ㄨㄛˇ lists 䂺 before 婐 and 捰, so its swaps differ from Microsoft Zhuyin's 捰, 婐; the positions are the same).
    ("up-in-composition", Text { commit: "", preedit: "擬", handled: true, window: false }),
    ("up-twice-in-composition", Text { commit: "", preedit: "妳", handled: true, window: false }),
    ("up-three-times-in-composition", Window(opens_with_the_shown_text_first)),
    ("up-then-down-in-composition", Window(opens_with_the_shown_text_first)),
    ("down-esc-then-up-in-composition", Text { commit: "", preedit: "擬", handled: true, window: false }),
    ("up-twice-then-enter", Text { commit: "妳", preedit: "", handled: true, window: false }),
    ("up-on-wo3", Text { commit: "", preedit: "婐", handled: true, window: false }),
    ("up-twice-on-wo3", Text { commit: "", preedit: "䂺", handled: true, window: false }),
    ("up-three-times-on-wo3", Window(opens_with_the_shown_text_first)),
    ("up-on-qu4", Text { commit: "", preedit: "覷", handled: true, window: false }),
    ("up-twice-on-qu4", Text { commit: "", preedit: "趣", handled: true, window: false }),
    ("up-three-times-on-qu4", Window(opens_with_the_shown_text_first)),
    ("up-on-ni3-wo3", Text { commit: "", preedit: "你婐", handled: true, window: false }),
    ("up-twice-on-ni3-wo3", Text { commit: "", preedit: "你䂺", handled: true, window: false }),
    ("up-three-times-on-ni3-wo3", Window(opens_with_the_shown_text_first)),
    ("up-on-wo3-men2", Text { commit: "", preedit: "我捫", handled: true, window: false }),
    ("up-twice-on-wo3-men2", Text { commit: "", preedit: "我門", handled: true, window: false }),
    ("up-three-times-on-wo3-men2", Window(opens_with_the_shown_text_first)),
    ("down-on-wo3-men2", Window(opens_with_the_shown_text_first)),
    ("shift-enter-in-composition", Text { commit: "你", preedit: "", handled: true, window: false }),
    ("ctrl-comma-empty", Text { commit: "", preedit: "，", handled: true, window: false }),
    ("ctrl-period-empty", Text { commit: "", preedit: "。", handled: true, window: false }),
    ("ctrl-comma-on-syllable", Text { commit: "", preedit: "，", handled: true, window: false }),
    ("ctrl-period-in-composition", Text { commit: "", preedit: "你。", handled: true, window: false }),
    ("ctrl-comma-in-window", Text { commit: "", preedit: "你，", handled: true, window: false }),
    ("backspace-in-window", Window(window_stays)),
    ("up-in-window-at-first-row", Window(jumps_to_the_last_row)),
    ("up-in-window-across-a-page", Window(up_to_the_previous_page)),
    ("space-in-window-keeps-no-position", Window(next_page_first_row)),
    ("page-down-in-window-twice", Window(page_down_to_the_third_page)),
    ("page-down-in-window-after-the-last-page", Window(wraps_to_the_first_page)),
    ("space-in-window-after-the-last-page", Window(wraps_to_the_first_page)),
    ("page-up-in-window-at-first-page", Window(page_up_wraps_to_the_last_page)),
    ("down-in-window-across-a-page", Window(down_across_the_page)),
    ("down-in-window-at-the-last-row", Window(down_at_the_last_row)),
    ("tab-in-window", Window(window_stays)),
    ("shift-tab-in-window", Window(window_stays)),
    ("right-in-window", Window(window_stays)),
    ("left-in-window", Window(window_stays_on_the_second_row)),
    ("home-in-window", Window(window_stays)),
    ("delete-in-window", Window(window_stays)),
    ("end-in-window", Window(end_goes_to_the_last_row)),
    ("shift-enter-in-window", Text { commit: "", preedit: "你", handled: true, window: false }),
    ("zhuyin-a-in-window", Window(window_stays)),
    ("zhuyin-semicolon-in-window", Window(window_stays)),
    ("zhuyin-zero-in-window", Window(window_stays)),
    ("zhuyin-minus-in-window", Window(window_stays)),
    ("shift-a-in-window", Window(window_stays)),
    ("shift-comma-in-window", Window(window_stays)),
    ("shift-period-in-window", Window(window_stays)),
];

fn keys_of(name: &str) -> &'static str {
    VM_KEY_STRINGS.iter().find(|(n, _)| *n == name).map(|(_, k)| *k).need("a segment of VM_KEY_STRINGS")
}

/// What the wire would carry for a core output: the same fields the host copies (`engine::send`), so `proto::out_ok` can judge it.
fn out_of(s: &Snap) -> Out {
    let open = s.selected >= 0;
    Out {
        ran: true,
        core_error: false,
        took_over: false,
        handled: s.handled,
        commit: s.commit.clone(),
        preedit: s.preedit.clone(),
        cursor_utf16: s.cursor,
        candidate_selected: s.selected,
        candidate_first: if open { s.first } else { 0 },
        candidate_total: if open { s.total } else { 0 },
        candidates: if open { s.candidates.clone() } else { Vec::new() },
    }
}

/// One segment on a fresh state of `core`: (everything committed, the output before the last key, the output of the last key); every output is judged by `proto::out_ok`.
fn run_segment(core: &Direct, keys: &str) -> (Vec<u16>, Snap, Snap) {
    core.reset();
    let (mut commit, mut before, mut last) = (Vec::new(), None, None);
    for k in parse_keys(keys) {
        let s = core.key(k);
        check(proto::out_ok(&out_of(&s)), "the output of a key passes the client's check");
        commit.extend_from_slice(&s.commit);
        before = last.take();
        last = Some(s);
    }
    let last = last.need("a segment has a key");
    (commit, before.unwrap_or_else(|| last.clone()), last)
}

/// docs/spec/keys-and-composition.md §4.1.1 on the host's own engine with key scheme 1: every segment of `REPLAY_KEYS_MS` ends as its expectation says, and every output passes `proto::out_ok`.
#[test]
fn replay_keys_ms() {
    let core = Direct::with_scheme(1);
    for (name, want) in REPLAY_KEYS_MS {
        let (commit, before, last) = run_segment(&core, keys_of(name));
        match want {
            Text { commit: c, preedit: p, handled, window } => {
                check(commit == w(c), &format!("{name}: what was committed"));
                check(last.preedit == w(p), &format!("{name}: the composition"));
                check(last.handled == *handled, &format!("{name}: whether the last key was handled"));
                check((last.selected >= 0) == *window, &format!("{name}: whether a window is open"));
            }
            Window(rule) => check(rule(&before, &last), &format!("{name}: the window moves as the rule says")),
        }
    }
    // Every segment of the table has an expectation and the other way round; the three of the Run dialog repeat strings of the others.
    for (name, _) in VM_KEY_STRINGS {
        check(name.starts_with("run-dialog-") || REPLAY_KEYS_MS.iter().any(|(n, _)| n == name), &format!("{name}: has an expectation"));
    }
    for (name, _) in REPLAY_KEYS_MS {
        check(VM_KEY_STRINGS.iter().any(|(n, _)| n == name), &format!("{name}: is in VM_KEY_STRINGS"));
    }
    check(VM_KEY_STRINGS.iter().filter(|(n, _)| n.starts_with("run-dialog-")).all(|(_, k)| VM_KEY_STRINGS.iter().filter(|(_, k2)| k2 == k).count() >= 2), "a Run dialog cell repeats a string above");
}

/// The expected values of the VM run in scheme 0 (docs/spec/windows-tip.md §4.6): every segment of `VM_KEY_STRINGS` on the real core in key scheme 0, printed as
/// `name<TAB>commit<TAB>handled<TAB>preedit<TAB>window first,selected,total<TAB>trace`, texts as `\uXXXX`. The trace has one entry per key, `H` (the core handled it) or `P` (it passed the key on
/// to the application) followed by what that key committed; the VM driver uses it to tell what a passed-on key (Tab, Backspace, Shift+A, ...) adds to or removes from the Notepad text.
/// Run once on the build machine before the VM run and give the output to the run.
#[test]
#[ignore = "run by hand: prints the scheme 0 outputs of the VM key strings"]
fn print_vm_keys_scheme0() {
    print_vm_keys(0);
}

/// The expected values of the VM run in scheme 1 (the A4 run): the same print as `print_vm_keys_scheme0` on an engine of key scheme 1, the outputs `replay_keys_ms` ties to the Microsoft
/// Zhuyin measurement. `run_segment` resets the engine before each segment, as in scheme 0.
#[test]
#[ignore = "run by hand: prints the scheme 1 outputs of the VM key strings"]
fn print_vm_keys_scheme1() {
    print_vm_keys(1);
}

fn print_vm_keys(scheme: u32) {
    let core = Direct::with_scheme(scheme);
    for (name, keys) in VM_KEY_STRINGS {
        let (commit, _, last) = run_segment(&core, keys);
        let win = if last.selected >= 0 { format!("{},{},{}", last.first, last.selected, last.total) } else { "-".to_string() };
        // The same keys once more, one entry per key (deterministic: `run_segment` resets first, and so does this).
        core.reset();
        let trace: Vec<String> = parse_keys(keys).into_iter().map(|k| {
            let s = core.key(k);
            format!("{}{}", if s.handled { 'H' } else { 'P' }, escape(&s.commit))
        }).collect();
        println!("{name}\t{}\t{}\t{}\t{win}\t{}", escape(&commit), last.handled, escape(&last.preedit), trace.join(" "));
    }
}

/// After `engine::warm_with` an engine of key scheme 1 holds nothing either, and the next replay sends what a cold scheme 1 engine sends.
#[test]
fn warm_up_leaves_no_trace_in_scheme_1() {
    let core = Direct::with_scheme(1);
    // SAFETY: a live engine of this test, used on this thread only.
    check(unsafe { engine::warm_with(core.0, &engine::WARM_KEYS) }, "the warm-up resets");
    let o = core.key(kind(ENTER));
    check(o.commit.is_empty() && o.preedit.is_empty() && o.selected == -1, "nothing is composed or committed after the warm-up");
    // The same segments, in the same order, on an engine that was never warmed: a trace of the warm-up would show in the first answers that differ.
    let cold = Direct::with_scheme(1);
    for (name, _) in REPLAY_KEYS_MS {
        check(run_segment(&core, keys_of(name)).2 == run_segment(&cold, keys_of(name)).2, &format!("{name}: after the warm-up the engine answers as one that never warmed up"));
    }
}

/// A key the core refuses in the middle of the warm-up stops it with a composition pending; the reset still empties a scheme 1 engine.
#[test]
fn warm_up_failing_midway_still_resets_in_scheme_1() {
    let core = Direct::with_scheme(1);
    let bad = Key { kind: 1, ch: 0xD800, modifiers: 0 };
    // SAFETY: a live engine used on this thread only.
    check(unsafe { engine::warm_with(core.0, &[chr('a'), chr('u'), bad, chr('6')]) }, "the reset after the failed warm-up works");
    let o = core.key(kind(ENTER));
    check(o.commit.is_empty() && o.preedit.is_empty() && o.selected == -1, "nothing is composed or committed after the failed warm-up");
}

/// docs/spec/windows-tip.md §5.2: the registry value to the scheme and the source of event 71. The read itself (`RegGetValueW`) is the one call `server::run` makes.
#[test]
fn the_key_scheme_value_is_read_as_the_spec_says() {
    use crate::server::key_scheme_from;
    const ERROR_FILE_NOT_FOUND: u32 = 2;
    const ERROR_PATH_NOT_FOUND: u32 = 3;
    const ERROR_MORE_DATA: u32 = 234;
    const ERROR_UNSUPPORTED_TYPE: u32 = 1630;
    check(key_scheme_from(0, 0) == (0, 1), "a valid 0 is kept");
    check(key_scheme_from(0, 1) == (1, 1), "a valid 1 is kept");
    for v in [2, 3, 255, 0x8000_0000, u32::MAX] {
        check(key_scheme_from(0, v) == (1, 2), "a DWORD other than 0 or 1 gives 1, invalid");
    }
    check(key_scheme_from(ERROR_FILE_NOT_FOUND, 0) == (1, 0) && key_scheme_from(ERROR_PATH_NOT_FOUND, 0) == (1, 0), "no value gives 1, no value");
    for rc in [ERROR_MORE_DATA, ERROR_UNSUPPORTED_TYPE, 5, 87] {
        check(key_scheme_from(rc, 0) == (1, 2) && key_scheme_from(rc, 1) == (1, 2), "any other error gives 1, invalid");
    }
}

/// `Welcome` carries the key scheme of the installed engine (the shared server's is 0).
#[test]
fn the_welcome_carries_the_scheme_of_the_engine() {
    let _one = ONE_AT_A_TIME.lock().unwrap_or_else(PoisonError::into_inner);
    let until = Instant::now() + Duration::from_secs(10);
    let _c = loop {
        match Client::hello() {
            (c, m) if m == ServerMsg::Welcome { build_hash: proto::BUILD_HASH, key_scheme: 0 } => break c,
            (_, ServerMsg::Refuse { reason: proto::REFUSE_LIMIT }) if Instant::now() < until => std::thread::sleep(Duration::from_millis(20)),
            _ => fail("the shared server's Welcome says scheme 0"),
        }
    };
    check(engine::key_scheme() == 0, "the installed engine's scheme");
}
