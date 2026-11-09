//! docs/spec/windows-tip.md §5.11 item 10, against a real server: the production `server::start` on a test pipe name with
//! the real engine (the models of data/, fetched by scripts/fetch-models.sh as for the core's tests), and a client that opens the pipe
//! with the text service's access and identification-only flags. The two tests share the server and the rate limit of this process
//! (one PID), so they run one at a time.
use crate::pipeio::Pipe;
use crate::{engine, server, token};
use shanjie_win_proto::{self as proto, ClientMsg, Key, Out, ServerMsg};
use std::path::Path;
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
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().and_then(Path::parent).need("repository root");
        engine::install(engine::load(root).need("the engine loads from data/ (scripts/fetch-models.sh)"));
        name.encode_utf16().chain([0]).collect()
    })
}

fn w(s: &str) -> Vec<u16> {
    s.encode_utf16().collect()
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
        match self.call(&ClientMsg::Key { ctx, key: Key { kind: 1, ch: ch as u32, modifiers: 0 } }) {
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
