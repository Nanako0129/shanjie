//! The wire format between the shanjie TSF client (the DLL) and the resident host (docs/contracts/win-resident.md section 2.3).
//!
//! Fixed width, little endian, no self-describing serialisation. Every message is `u32 len` (the whole message, these four bytes
//! included), `u16 proto`, `u16 type`, `u64 seq`, then the body of its type. A string is a `u32` count of UTF-16 units and the units;
//! a boolean is one byte, 0 or 1.
//!
//! The parsing rules of both ends live here: the length is checked before anything is allocated (`frame_len`), types are
//! white-listed per direction, a string may not run past the body, a body may not have bytes left over, and a host connection starts
//! with exactly one `Hello` (`HostSession`). Every failure is `None` or `Accept::Drop`: the caller drops the connection without an
//! answer. `out_ok` holds the client's extra checks of an `Out`.
//!
//! R2: the messages carry typed text, so nothing here derives `Debug` (no composition reaches a log through `{:?}`).

pub const PROTO: u16 = 1;
pub const HEADER_LEN: usize = 16;
pub const MAX_LEN: usize = 64 * 1024;

// Client to host.
pub const T_HELLO: u16 = 1;
pub const T_KEY: u16 = 2;
pub const T_RELEASE: u16 = 3;
pub const T_RESET: u16 = 4;
// Host to client.
pub const T_WELCOME: u16 = 0x81;
pub const T_REFUSE: u16 = 0x82;
pub const T_NOT_READY: u16 = 0x83;
pub const T_BUSY: u16 = 0x84;
pub const T_OUT: u16 = 0x85;
/// Reserved for pushes from the host (background rerank, the candidate window); not sent in this slice, dropped by the client.
pub const T_PUSHED: u16 = 0x86;

/// `Refuse` reasons; a later code is only another number.
pub const REFUSE_PROTO: u32 = 1;
pub const REFUSE_LIMIT: u32 = 2;
pub const REFUSE_CLIENT: u32 = 3;

/// Identifies a build. Compared, never a reason to refuse: FNV-1a of `SHANJIE_BUILD_ID` when the build sets it, else of the crate version.
pub const BUILD_HASH: u64 = fnv1a(match option_env!("SHANJIE_BUILD_ID") {
    Some(s) => s.as_bytes(),
    None => env!("CARGO_PKG_VERSION").as_bytes(),
});

const fn fnv1a(b: &[u8]) -> u64 {
    let mut h = 0xcbf2_9ce4_8422_2325u64;
    let mut i = 0;
    while i < b.len() {
        h ^= b[i] as u64;
        h = h.wrapping_mul(0x0100_0000_01b3);
        i += 1;
    }
    h
}

/// `\\.\pipe\shanjie-dev-<user SID>-<session id>` (section 2.2). No protocol version in the name: two ends of different versions
/// still meet, so the host can answer `Refuse { 1 }`.
pub fn pipe_name(user_sid: &str, session: u32) -> String {
    format!(r"\\.\pipe\shanjie-dev-{user_sid}-{session}")
}

/// `ShanjieKey` of core/include/shanjie.h.
#[derive(Clone, Copy, PartialEq, Eq)]
pub struct Key {
    pub kind: u32,
    pub ch: u32,
    pub modifiers: u32,
}

#[derive(PartialEq)]
pub enum ClientMsg {
    /// The first message of a connection (its protocol version is the header's). `pid` and `tid` are information, never authority.
    Hello { build_hash: u64, pid: u32, tid: u32 },
    /// `ctx` means something only inside the connection that sends it: the host's owner is (connection, ctx).
    Key { ctx: u64, key: Key },
    Release { ctx: u64 },
    /// Sent after the DLL caught a panic.
    Reset { ctx: u64 },
}

/// The answer to a `Key`: what WIN-TIP's `engine::key` gave the text service. `core_error` means the core returned non-zero: the
/// client edits nothing and treats the key as not handled. `candidates` is reserved (always empty in this slice).
#[derive(PartialEq)]
pub struct Out {
    pub ran: bool,
    pub core_error: bool,
    pub took_over: bool,
    pub handled: bool,
    pub commit: Vec<u16>,
    pub preedit: Vec<u16>,
    pub cursor_utf16: u32,
    pub candidate_selected: i32,
    pub candidates: Vec<Vec<u16>>,
}

#[derive(PartialEq)]
pub enum ServerMsg {
    Welcome { build_hash: u64 },
    /// The layout of this message never changes across protocol versions: it is read whatever the header's version says.
    Refuse { reason: u32 },
    NotReady,
    Busy,
    Out(Out),
    Pushed(Vec<u8>),
}

// ---- encoding ----

struct Enc(Vec<u8>);

impl Enc {
    fn new(proto: u16, ty: u16, seq: u64) -> Self {
        let mut v = Vec::with_capacity(64);
        v.extend_from_slice(&[0; 4]);
        v.extend_from_slice(&proto.to_le_bytes());
        v.extend_from_slice(&ty.to_le_bytes());
        v.extend_from_slice(&seq.to_le_bytes());
        Self(v)
    }
    fn flag(&mut self, b: bool) {
        self.0.push(b as u8);
    }
    fn u32(&mut self, n: u32) {
        self.0.extend_from_slice(&n.to_le_bytes());
    }
    fn u64(&mut self, n: u64) {
        self.0.extend_from_slice(&n.to_le_bytes());
    }
    fn text(&mut self, s: &[u16]) {
        // A count that does not fit makes the message longer than MAX_LEN, which `finish` refuses.
        self.u32(s.len() as u32);
        for u in s {
            self.0.extend_from_slice(&u.to_le_bytes());
        }
    }
    /// `None` when the message would be longer than `MAX_LEN`.
    fn finish(mut self) -> Option<Vec<u8>> {
        let n = self.0.len();
        if n > MAX_LEN {
            return None;
        }
        self.0[..4].copy_from_slice(&(n as u32).to_le_bytes());
        Some(self.0)
    }
}

pub fn encode_client(seq: u64, m: &ClientMsg) -> Option<Vec<u8>> {
    let ty = match m {
        ClientMsg::Hello { .. } => T_HELLO,
        ClientMsg::Key { .. } => T_KEY,
        ClientMsg::Release { .. } => T_RELEASE,
        ClientMsg::Reset { .. } => T_RESET,
    };
    let mut e = Enc::new(PROTO, ty, seq);
    match m {
        ClientMsg::Hello { build_hash, pid, tid } => {
            e.u64(*build_hash);
            e.u32(*pid);
            e.u32(*tid);
        }
        ClientMsg::Key { ctx, key } => {
            e.u64(*ctx);
            e.u32(key.kind);
            e.u32(key.ch);
            e.u32(key.modifiers);
        }
        ClientMsg::Release { ctx } | ClientMsg::Reset { ctx } => e.u64(*ctx),
    }
    e.finish()
}

pub fn encode_server(seq: u64, m: &ServerMsg) -> Option<Vec<u8>> {
    let ty = match m {
        ServerMsg::Welcome { .. } => T_WELCOME,
        ServerMsg::Refuse { .. } => T_REFUSE,
        ServerMsg::NotReady => T_NOT_READY,
        ServerMsg::Busy => T_BUSY,
        ServerMsg::Out(_) => T_OUT,
        ServerMsg::Pushed(_) => T_PUSHED,
    };
    let mut e = Enc::new(PROTO, ty, seq);
    match m {
        ServerMsg::Welcome { build_hash } => e.u64(*build_hash),
        ServerMsg::Refuse { reason } => e.u32(*reason),
        ServerMsg::NotReady | ServerMsg::Busy => {}
        ServerMsg::Out(o) => {
            e.flag(o.ran);
            e.flag(o.core_error);
            e.flag(o.took_over);
            e.flag(o.handled);
            e.u32(o.cursor_utf16);
            e.u32(o.candidate_selected as u32);
            e.text(&o.commit);
            e.text(&o.preedit);
            e.u32(o.candidates.len() as u32);
            for c in &o.candidates {
                e.text(c);
            }
        }
        ServerMsg::Pushed(b) => e.0.extend_from_slice(b),
    }
    e.finish()
}

// ---- decoding ----

struct Dec<'a>(&'a [u8]);

impl<'a> Dec<'a> {
    fn take(&mut self, n: usize) -> Option<&'a [u8]> {
        if n > self.0.len() {
            return None;
        }
        let (a, b) = self.0.split_at(n);
        self.0 = b;
        Some(a)
    }
    fn u8(&mut self) -> Option<u8> {
        Some(self.take(1)?[0])
    }
    fn flag(&mut self) -> Option<bool> {
        match self.u8()? {
            0 => Some(false),
            1 => Some(true),
            _ => None,
        }
    }
    fn u16(&mut self) -> Option<u16> {
        Some(u16::from_le_bytes(self.take(2)?.try_into().ok()?))
    }
    fn u32(&mut self) -> Option<u32> {
        Some(u32::from_le_bytes(self.take(4)?.try_into().ok()?))
    }
    fn u64(&mut self) -> Option<u64> {
        Some(u64::from_le_bytes(self.take(8)?.try_into().ok()?))
    }
    /// A string may not run past what is left.
    fn text(&mut self) -> Option<Vec<u16>> {
        let n = self.u32()? as usize;
        let b = self.take(n.checked_mul(2)?)?;
        Some(b.chunks_exact(2).map(|c| u16::from_le_bytes([c[0], c[1]])).collect())
    }
    /// A list of strings; each takes at least its four count bytes, so a count beyond that is refused before allocating.
    fn texts(&mut self) -> Option<Vec<Vec<u16>>> {
        let n = self.u32()? as usize;
        if n > self.0.len() / 4 {
            return None;
        }
        (0..n).map(|_| self.text()).collect()
    }
    /// No bytes may be left over.
    fn end<T>(&self, v: T) -> Option<T> {
        self.0.is_empty().then_some(v)
    }
}

pub struct Header {
    pub proto: u16,
    pub ty: u16,
    pub seq: u64,
}

/// The length a message announces in its first four bytes, if it is within bounds (16 bytes to 64 KiB). Read these four bytes,
/// check, and only then allocate and read the rest.
pub fn frame_len(prefix: [u8; 4]) -> Option<usize> {
    let n = u32::from_le_bytes(prefix) as usize;
    (HEADER_LEN..=MAX_LEN).contains(&n).then_some(n)
}

/// The header of one complete message; `len` must equal the number of bytes given.
pub fn header(frame: &[u8]) -> Option<Header> {
    let mut d = Dec(frame);
    let len = d.u32()? as usize;
    if len != frame.len() || !(HEADER_LEN..=MAX_LEN).contains(&len) {
        return None;
    }
    Some(Header { proto: d.u16()?, ty: d.u16()?, seq: d.u64()? })
}

/// A message to the host: only the client types, only the current protocol version.
pub fn decode_client(frame: &[u8]) -> Option<(u64, ClientMsg)> {
    let h = header(frame)?;
    if h.proto != PROTO {
        return None;
    }
    let mut d = Dec(&frame[HEADER_LEN..]);
    let m = match h.ty {
        T_HELLO => ClientMsg::Hello { build_hash: d.u64()?, pid: d.u32()?, tid: d.u32()? },
        T_KEY => ClientMsg::Key { ctx: d.u64()?, key: Key { kind: d.u32()?, ch: d.u32()?, modifiers: d.u32()? } },
        T_RELEASE => ClientMsg::Release { ctx: d.u64()? },
        T_RESET => ClientMsg::Reset { ctx: d.u64()? },
        _ => return None,
    };
    d.end((h.seq, m))
}

/// A message to the client: only the host types. `Refuse` is read at any protocol version (its layout is frozen); every other type
/// only at the current one.
pub fn decode_server(frame: &[u8]) -> Option<(u64, ServerMsg)> {
    let h = header(frame)?;
    if h.proto != PROTO && h.ty != T_REFUSE {
        return None;
    }
    let mut d = Dec(&frame[HEADER_LEN..]);
    let m = match h.ty {
        T_WELCOME => ServerMsg::Welcome { build_hash: d.u64()? },
        T_REFUSE => ServerMsg::Refuse { reason: d.u32()? },
        T_NOT_READY => ServerMsg::NotReady,
        T_BUSY => ServerMsg::Busy,
        T_OUT => ServerMsg::Out(Out {
            ran: d.flag()?,
            core_error: d.flag()?,
            took_over: d.flag()?,
            handled: d.flag()?,
            cursor_utf16: d.u32()?,
            candidate_selected: d.u32()? as i32,
            commit: d.text()?,
            preedit: d.text()?,
            candidates: d.texts()?,
        }),
        T_PUSHED => ServerMsg::Pushed(d.take(d.0.len())?.to_vec()),
        _ => return None,
    };
    d.end((h.seq, m))
}

/// What the host does with one message of a connection.
pub enum Accept {
    Msg(u64, ClientMsg),
    /// The first message is a `Hello` of another protocol version: answer `Refuse { 1 }` (with this seq), then drop the connection.
    VersionMismatch(u64),
    /// Drop the connection without an answer.
    Drop,
}

/// The host's side of one connection: the first message is a `Hello`, and there is exactly one.
#[derive(Default)]
pub struct HostSession {
    greeted: bool,
}

impl HostSession {
    pub fn accept(&mut self, frame: &[u8]) -> Accept {
        let Some(h) = header(frame) else { return Accept::Drop };
        if !self.greeted {
            if h.ty != T_HELLO {
                return Accept::Drop;
            }
            if h.proto != PROTO {
                return Accept::VersionMismatch(h.seq);
            }
        }
        match decode_client(frame) {
            Some((_, ClientMsg::Hello { .. })) if self.greeted => Accept::Drop,
            Some((seq, m)) => {
                self.greeted = true;
                Accept::Msg(seq, m)
            }
            None => Accept::Drop,
        }
    }
}

/// The client's own checks of an `Out`, beyond parsing (section 2.3): the caret within `preedit`, no candidate window (the ESC that
/// closes one is sent by the host), and no control characters in `commit` or `preedit` (a forged host must not type a line break
/// into a terminal). The client also checks that the seq is the one it sent.
pub fn out_ok(o: &Out) -> bool {
    o.cursor_utf16 as usize <= o.preedit.len() && o.candidate_selected == -1 && text_ok(&o.commit) && text_ok(&o.preedit)
}

/// Valid UTF-16 with no C0 or C1 control character (U+0000 to U+001F, U+007F to U+009F).
pub fn text_ok(s: &[u16]) -> bool {
    char::decode_utf16(s.iter().copied()).all(|c| c.is_ok_and(|c| !c.is_control()))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn w(s: &str) -> Vec<u16> {
        s.encode_utf16().collect()
    }

    fn out() -> Out {
        Out {
            ran: true,
            core_error: false,
            took_over: true,
            handled: true,
            commit: w("你好"),
            preedit: w("ㄋㄧ"),
            cursor_utf16: 2,
            candidate_selected: -1,
            candidates: vec![],
        }
    }

    fn clients() -> Vec<ClientMsg> {
        vec![
            ClientMsg::Hello { build_hash: BUILD_HASH, pid: 1234, tid: 5678 },
            ClientMsg::Key { ctx: u64::MAX, key: Key { kind: 1, ch: 's' as u32, modifiers: 1 | 2 | 16 } },
            ClientMsg::Release { ctx: 7 },
            ClientMsg::Reset { ctx: 0 },
        ]
    }

    fn servers() -> Vec<ServerMsg> {
        let mut with_candidates = out();
        with_candidates.candidates = vec![w("你"), w(""), w("妳")];
        with_candidates.candidate_selected = 2;
        vec![
            ServerMsg::Welcome { build_hash: 42 },
            ServerMsg::Refuse { reason: REFUSE_LIMIT },
            ServerMsg::NotReady,
            ServerMsg::Busy,
            ServerMsg::Out(out()),
            ServerMsg::Out(with_candidates),
            ServerMsg::Pushed(vec![1, 2, 3]),
            ServerMsg::Pushed(vec![]),
        ]
    }

    fn set_proto(frame: &mut [u8], p: u16) {
        frame[4..6].copy_from_slice(&p.to_le_bytes());
    }

    #[test]
    fn every_type_round_trips() {
        for (i, m) in clients().iter().enumerate() {
            let f = encode_client(i as u64 + 9, m).unwrap();
            assert!(frame_len(f[..4].try_into().unwrap()) == Some(f.len()));
            let (seq, back) = decode_client(&f).expect("client message decodes");
            assert!(seq == i as u64 + 9 && back == *m, "client message {i}");
        }
        for (i, m) in servers().iter().enumerate() {
            let f = encode_server(i as u64, m).unwrap();
            let (seq, back) = decode_server(&f).expect("server message decodes");
            assert!(seq == i as u64 && back == *m, "server message {i}");
        }
    }

    #[test]
    fn length_bounds_are_checked_before_anything_else() {
        assert!(frame_len(15u32.to_le_bytes()).is_none());
        assert!(frame_len(16u32.to_le_bytes()) == Some(16));
        assert!(frame_len((64 * 1024u32).to_le_bytes()) == Some(65536));
        assert!(frame_len((64 * 1024 + 1u32).to_le_bytes()).is_none());
        assert!(frame_len(u32::MAX.to_le_bytes()).is_none());
        // `len` must equal the real size of the message.
        let mut f = encode_client(1, &ClientMsg::Release { ctx: 1 }).unwrap();
        f.push(0);
        assert!(header(&f).is_none() && decode_client(&f).is_none());
        // A message that would pass 64 KiB is not encoded.
        let mut big = out();
        big.commit = vec![0x4f60; 33 * 1024];
        assert!(encode_server(1, &ServerMsg::Out(big)).is_none());
    }

    #[test]
    fn truncated_or_padded_bodies_are_refused() {
        for m in clients() {
            let f = encode_client(1, &m).unwrap();
            for cut in HEADER_LEN..f.len() {
                let mut g = f[..cut].to_vec();
                g[..4].copy_from_slice(&(cut as u32).to_le_bytes());
                assert!(decode_client(&g).is_none(), "a truncated client body is refused");
            }
            let mut g = f.clone();
            g.push(0);
            let n = g.len() as u32;
            g[..4].copy_from_slice(&n.to_le_bytes());
            assert!(decode_client(&g).is_none(), "a trailing byte is refused");
        }
        for m in servers() {
            let f = encode_server(1, &m).unwrap();
            for cut in HEADER_LEN..f.len() {
                let mut g = f[..cut].to_vec();
                g[..4].copy_from_slice(&(cut as u32).to_le_bytes());
                // A Pushed body is opaque: any length is a Pushed.
                if !matches!(m, ServerMsg::Pushed(_)) {
                    assert!(decode_server(&g).is_none(), "a truncated server body is refused");
                }
            }
            if !matches!(m, ServerMsg::Pushed(_)) {
                let mut g = f.clone();
                g.push(0);
                let n = g.len() as u32;
                g[..4].copy_from_slice(&n.to_le_bytes());
                assert!(decode_server(&g).is_none(), "a trailing byte is refused");
            }
        }
    }

    #[test]
    fn types_are_white_listed_per_direction() {
        for m in servers() {
            assert!(decode_client(&encode_server(1, &m).unwrap()).is_none(), "a host type is not a client message");
        }
        for m in clients() {
            assert!(decode_server(&encode_client(1, &m).unwrap()).is_none(), "a client type is not a host message");
        }
        let mut f = encode_client(1, &ClientMsg::Reset { ctx: 1 }).unwrap();
        f[6..8].copy_from_slice(&99u16.to_le_bytes());
        assert!(decode_client(&f).is_none());
    }

    #[test]
    fn a_string_may_not_run_past_the_body_and_flags_are_zero_or_one() {
        let f = encode_server(1, &ServerMsg::Out(out())).unwrap();
        // The commit count sits after 4 flags, cursor and selection: header + 12.
        let at = HEADER_LEN + 12;
        let mut g = f.clone();
        g[at..at + 4].copy_from_slice(&1000u32.to_le_bytes());
        assert!(decode_server(&g).is_none());
        let mut g = f.clone();
        g[at..at + 4].copy_from_slice(&u32::MAX.to_le_bytes());
        assert!(decode_server(&g).is_none());
        let mut g = f.clone();
        g[HEADER_LEN] = 2;
        assert!(decode_server(&g).is_none(), "a flag byte other than 0 or 1");
        // A candidate count larger than the bytes left.
        let mut g = f.clone();
        let n = g.len();
        g[n - 4..].copy_from_slice(&1_000_000u32.to_le_bytes());
        assert!(decode_server(&g).is_none());
    }

    #[test]
    fn protocol_versions() {
        let mut f = encode_server(3, &ServerMsg::Refuse { reason: REFUSE_PROTO }).unwrap();
        set_proto(&mut f, PROTO + 1);
        assert!(decode_server(&f) == Some((3, ServerMsg::Refuse { reason: REFUSE_PROTO })), "Refuse is read at any version");
        let mut f = encode_server(3, &ServerMsg::Welcome { build_hash: 1 }).unwrap();
        set_proto(&mut f, PROTO + 1);
        assert!(decode_server(&f).is_none(), "every other host type needs this version");
        let mut f = encode_client(3, &ClientMsg::Release { ctx: 1 }).unwrap();
        set_proto(&mut f, 0);
        assert!(decode_client(&f).is_none());
    }

    #[test]
    fn a_host_connection_starts_with_exactly_one_hello() {
        let hello = encode_client(1, &clients().remove(0)).unwrap();
        let key = encode_client(2, &clients().remove(1)).unwrap();
        // Anything before the Hello drops the connection.
        let mut s = HostSession::default();
        assert!(matches!(s.accept(&key), Accept::Drop));
        // A Hello, then keys; a second Hello drops it.
        let mut s = HostSession::default();
        assert!(matches!(s.accept(&hello), Accept::Msg(1, ClientMsg::Hello { .. })));
        assert!(matches!(s.accept(&key), Accept::Msg(2, ClientMsg::Key { .. })));
        assert!(matches!(s.accept(&hello), Accept::Drop));
        // A first Hello of another version is answered with Refuse 1, whatever its body; another first type of another version is dropped.
        let mut other = hello.clone();
        set_proto(&mut other, PROTO + 1);
        other.extend_from_slice(&[0; 8]);
        let n = other.len() as u32;
        other[..4].copy_from_slice(&n.to_le_bytes());
        assert!(matches!(HostSession::default().accept(&other), Accept::VersionMismatch(1)));
        let mut other_key = key.clone();
        set_proto(&mut other_key, PROTO + 1);
        assert!(matches!(HostSession::default().accept(&other_key), Accept::Drop));
        // After the Hello a message of another version is dropped.
        let mut s = HostSession::default();
        assert!(matches!(s.accept(&hello), Accept::Msg(..)));
        assert!(matches!(s.accept(&other_key), Accept::Drop));
        // A malformed frame is dropped.
        assert!(matches!(HostSession::default().accept(&hello[..10]), Accept::Drop));
    }

    #[test]
    fn the_client_checks_of_an_out() {
        assert!(out_ok(&out()));
        let mut o = out();
        o.cursor_utf16 = 3;
        assert!(!out_ok(&o), "caret beyond the preedit");
        let mut o = out();
        o.candidate_selected = 0;
        assert!(!out_ok(&o), "a candidate window is never left open");
        for bad in ["\n", "\r", "\t", "\u{1b}[31m", "\u{7f}", "\u{85}", "\u{9f}", "\0"] {
            let mut o = out();
            o.commit = w(&format!("你{bad}"));
            assert!(!out_ok(&o), "control character in commit");
            let mut o = out();
            o.preedit = w(&format!("ㄋ{bad}"));
            o.cursor_utf16 = 0;
            assert!(!out_ok(&o), "control character in preedit");
        }
        let mut o = out();
        o.commit = vec![0xD800];
        assert!(!out_ok(&o), "a lone surrogate");
        let mut o = out();
        o.commit = w("😀 ,。「」ㄅ");
        assert!(out_ok(&o), "printable text, a pair of surrogates and a space pass");
    }

    #[test]
    fn the_pipe_name_has_no_version() {
        assert!(pipe_name("S-1-5-21-1-2-3-1001", 1) == r"\\.\pipe\shanjie-dev-S-1-5-21-1-2-3-1001-1");
    }

    /// xorshift64*: no dependency for random bytes.
    struct Rng(u64);
    impl Rng {
        fn next(&mut self) -> u64 {
            self.0 ^= self.0 >> 12;
            self.0 ^= self.0 << 25;
            self.0 ^= self.0 >> 27;
            self.0.wrapping_mul(0x2545_f491_4f6c_dd1d)
        }
        fn below(&mut self, n: usize) -> usize {
            (self.next() % n as u64) as usize
        }
    }

    fn decode_all(f: &[u8]) {
        let _ = header(f);
        let _ = decode_client(f);
        let _ = decode_server(f);
        let _ = HostSession::default().accept(f);
        if let Some((_, ServerMsg::Out(o))) = decode_server(f) {
            let _ = out_ok(&o);
        }
        if f.len() >= 4 {
            let _ = frame_len(f[..4].try_into().unwrap());
        }
    }

    #[test]
    fn random_bytes_never_panic() {
        let mut r = Rng(0x9E37_79B9_7F4A_7C15);
        let valid: Vec<Vec<u8>> = clients().iter().map(|m| encode_client(1, m).unwrap()).chain(servers().iter().map(|m| encode_server(1, m).unwrap())).collect();
        for _ in 0..200_000 {
            let mut f: Vec<u8> = match r.below(3) {
                // Pure noise.
                0 => (0..r.below(80)).map(|_| r.next() as u8).collect(),
                // A valid message with bytes flipped.
                1 => {
                    let mut f = valid[r.below(valid.len())].clone();
                    for _ in 0..1 + r.below(4) {
                        let i = r.below(f.len());
                        f[i] = r.next() as u8;
                    }
                    f
                }
                // Noise behind a header whose len is right and whose type is real, so the body decoders run.
                _ => {
                    let mut f = valid[r.below(valid.len())][..HEADER_LEN].to_vec();
                    f.extend((0..r.below(64)).map(|_| r.next() as u8));
                    f
                }
            };
            if r.below(2) == 0 && f.len() >= 4 {
                let n = f.len() as u32;
                f[..4].copy_from_slice(&n.to_le_bytes());
            }
            decode_all(&f);
        }
    }
}
