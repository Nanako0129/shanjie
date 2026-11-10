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
        engine::install(engine::load(repo_root()).need("the engine loads from data/ (scripts/fetch-models.sh)"));
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
struct Snap {
    handled: bool,
    commit: Vec<u16>,
    preedit: Vec<u16>,
    selected: i32,
    first: u32,
    total: u32,
    candidates: Vec<Vec<u16>>,
}

/// An engine of the host's own build (`engine::load`: the packs, the vertical window, the rerank off, the learning off), called with no pipe in between.
struct Direct(*mut ShanjieEngine);

impl Direct {
    fn new() -> Direct {
        Direct(engine::load(repo_root()).need("the engine loads from data/ (scripts/fetch-models.sh)"))
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
            let snap = Snap { handled: r.handled != 0, commit: s(r.commit), preedit: s(r.preedit), selected: r.candidate_selected, first: r.candidate_first, total: r.candidate_total, candidates };
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
const REPLAY_KEYS_VERTICAL_EXPECTED: &str = r"";

fn parse_keys(line: &str) -> Vec<Key> {
    line.split(' ')
        .map(|t| match t {
            "space" => kind(SPACE),
            "enter" => kind(ENTER),
            "bksp" => kind(4),
            "esc" => kind(ESC),
            "up" => kind(9),
            "down" => kind(DOWN),
            "pgup" => kind(14),
            "pgdn" => kind(PAGE_DOWN),
            t if t.chars().count() == 1 => chr(t.chars().next().need("a character")),
            _ => fail("an unknown key token"),
        })
        .collect()
}

/// Feeds the keys to a fresh engine and returns everything it sent as text; the composition is empty at the end and a window opened on the way.
fn replay(line: &str) -> Vec<u16> {
    let core = Direct::new();
    let (mut text, mut saw_window) = (Vec::new(), false);
    let mut last = None;
    for k in parse_keys(line) {
        let s = core.key(k);
        text.extend_from_slice(&s.commit);
        saw_window |= s.selected >= 0;
        last = Some(s);
    }
    check(saw_window, "a candidate window opened during the replay");
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
    check(replay(REPLAY_KEYS_VERTICAL) == unescape(REPLAY_KEYS_VERTICAL_EXPECTED), "the replay sends the expected text");
}

/// Prints the one line `replay_keys_vertical` expects. The sentence is public (a row of eval/dev/user-typing.txt), so this is the one test that prints.
#[test]
#[ignore = "run by hand: prints the expected text of replay_keys_vertical"]
fn print_replay_keys_vertical() {
    println!("const REPLAY_KEYS_VERTICAL_EXPECTED: &str = r\"{}\";", escape(&replay(REPLAY_KEYS_VERTICAL)));
}

/// docs/spec/windows-tip.md §3.6: the text service's key predicate against the real core, with a candidate window open. Every virtual key of the mapping
/// table, the digits, letters and punctuation keys, the page keys, and the unmapped ones, with every Shift, Ctrl and Caps Lock combination
/// (Alt and Win keys are never sent): whenever the core handles the key, `OnTestKeyDown` must say it may be eaten, and the page keys map only with no modifier.
#[test]
fn window_keys_match_the_core() {
    use crate::keys::{map_key, may_eat, Mods};
    let core = Direct::new();
    let mut vks: Vec<u32> = vec![0x20, 0x0D, 0x08, 0x2E, 0x1B, 0x25, 0x27, 0x26, 0x28, 0x24, 0x23, 0x09, 0x21, 0x22, 0x70, 0x60, 0x10, 0x11, 0x12, 0xE5];
    vks.extend(0x30..=0x39);
    vks.extend(0x41..=0x5A);
    vks.extend([0xBA, 0xBF, 0xC0, 0xDB, 0xDC, 0xDD, 0xDE, 0xBB, 0xBD, 0xBC, 0xBE]);
    let open = |core: &Direct| {
        core.reset();
        for c in ['s', 'u', '3'] {
            core.key(chr(c));
        }
        let o = core.key(kind(SPACE));
        check(o.selected >= 0 && !o.candidates.is_empty(), "the window opens");
    };
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
            open(&core);
            let s = core.key(k);
            combos += 1;
            if s.handled {
                handled_n += 1;
                check(may_eat(true, &k), &format!("vk {vk:#x} mods {bits}: the core handles it, OnTestKeyDown must say TRUE"));
            }
            if page {
                check(s.handled, &format!("vk {vk:#x}: the core handles a page key with a window open"));
            }
        }
    }
    // The keys of rules 3v-8 are handled and may be eaten.
    for c in '1'..='9' {
        open(&core);
        check(core.key(chr(c)).handled && may_eat(true, &chr(c)), "a digit");
    }
    for k in [SPACE, ENTER, ESC, 4, DOWN, 9, 7, 8, 14, 15] {
        open(&core);
        check(core.key(kind(k)).handled && may_eat(true, &kind(k)), "a window key");
    }
    println!("{combos} combinations with a window open, {handled_n} handled by the core, 0 where the core handles a key OnTestKeyDown refuses");
}
