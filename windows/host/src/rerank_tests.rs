//! The semantic rerank in the host (docs/spec/windows-tip.md §5.2, §5.5 and §5.11 item 12), against the engine module and the real core, with no pipe in between:
//! `engine::key`, `engine::poll`, `engine::release` and `engine::drop_connection` are what a connection thread calls, with the owner it made of its own
//! connection id. Runs on every platform that has the models (scripts/fetch-models.sh). The tests share the one installed engine, so they run one at a time, and
//! each leaves it with no owner and no job.
//!
//! The sentence is row 65 of dev302 (`shanjie --selftest` types the same one, docs/spec/semantic-rerank.md §8.4): with the rerank the composition changes one
//! character. What it must say on and off is asked from the core itself (`Direct`), and the two must differ.
use crate::engine::{self, probe, Answer, Owner};
use crate::testkit::{check, fail, installed, kind, parse_keys, quiet, w, Direct, Need, ENTER, ONE_AT_A_TIME};
use shanjie_win_proto::{self as proto, Key, Out, PollResult, Polled, ServerMsg};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc;
use std::sync::{MutexGuard, OnceLock, PoisonError};
use std::time::{Duration, Instant};

/// ㄨㄛˇ ㄉㄥˇ ㄧ ㄒㄧㄚˋ ㄗㄞˋ ㄍㄨㄛˋ ㄑㄩˋ on the standard layout (key scheme 0: Space is the first tone).
const SENTENCE: &str = "j i 3 2 / 3 u space v u 8 4 y 9 4 e j i 4 f m 4";

const NOT_YOURS: Polled = Polled { pending: false, what: PollResult::Idle };

/// (the composition with the rerank off, with the rerank on): the core's own answers for `SENTENCE`.
fn texts() -> &'static (Vec<u16>, Vec<u16>) {
    static T: OnceLock<(Vec<u16>, Vec<u16>)> = OnceLock::new();
    T.get_or_init(|| {
        let (off, sent_off, jobs_off) = Direct::with(0, false).typed(SENTENCE);
        let (on, sent_on, jobs_on) = Direct::with(0, true).typed(SENTENCE);
        check(jobs_off == 0, "with the rerank off no key leaves a job (the `Rerank` value 0)");
        check(jobs_on > 0, "with the rerank on the keys leave jobs");
        check(off == sent_off && on == sent_on, "Enter sends what is shown");
        check(off != on, "the sentence reads differently with the rerank on");
        check(off == w("我等一下在過去") && on == w("我等一下再過去"), "the two readings are those of the selftest");
        (off, on)
    })
}

struct Session {
    _one: MutexGuard<'static, ()>,
    takes: u64,
    frees: u64,
}

/// One test at a time on the installed engine; the counters of the jobs as they are now.
fn session() -> Session {
    let one = ONE_AT_A_TIME.lock().unwrap_or_else(PoisonError::into_inner);
    installed();
    texts();
    quiet();
    idle();
    Session { _one: one, takes: probe::TAKES.load(Ordering::SeqCst), frees: probe::FREES.load(Ordering::SeqCst) }
}

impl Session {
    /// Jobs handed out since the session began.
    fn takes(&self) -> u64 {
        probe::TAKES.load(Ordering::SeqCst) - self.takes
    }
    fn frees(&self) -> u64 {
        probe::FREES.load(Ordering::SeqCst) - self.frees
    }
    /// The end of every test: nobody owns the engine, the rerank thread is idle, and every job handed out was freed.
    fn end(self, owners: &[Owner]) {
        for o in owners {
            engine::release(*o);
        }
        idle();
        check(probe::owner().is_none(), "nobody owns the engine at the end");
        check(self.takes() > 0, "the test made jobs");
        check(self.takes() == self.frees(), "every job handed out was freed (rerank_free count == rerank_take count)");
    }
}

fn wait_for(what: &str, f: impl Fn() -> bool) {
    let until = Instant::now() + Duration::from_secs(60);
    while !f() {
        if Instant::now() > until {
            fail(what);
        }
        std::thread::sleep(Duration::from_millis(2));
    }
}

/// No job waits and the rerank thread holds none.
fn idle() {
    wait_for("the rerank thread comes to rest", probe::idle);
}

/// Owners of connections no server made: ids far above the pipe server's own counter.
fn owner(ctx: u64) -> Owner {
    static NEXT: AtomicU64 = AtomicU64::new(1 << 40);
    Owner { conn: NEXT.fetch_add(1, Ordering::SeqCst), ctx }
}

fn key(me: Owner, k: Key) -> Out {
    match engine::key(me, k) {
        Answer::Out(o) => {
            check(o.ran && !o.core_error && proto::out_ok(&o), "a usable Out");
            o
        }
        Answer::Busy => fail("the engine was busy"),
        Answer::NotReady => fail("the engine was not ready"),
    }
}

/// Types the tokens for `me`; the answer to the last key.
fn type_keys(me: Owner, keys: &str) -> Out {
    parse_keys(keys).into_iter().map(|k| key(me, k)).last().need("at least one key")
}

/// Types `SENTENCE`: the answer to its last key shows the n-gram's text and says a rerank is pending.
fn type_sentence(me: Owner) -> Out {
    let o = type_keys(me, SENTENCE);
    check(o.preedit == texts().0, "the key's answer shows the n-gram's text");
    check(o.pending, "the key's answer says a rerank is pending");
    o
}

fn finished(me: Owner) {
    wait_for("the job of the sentence finishes", || probe::has_done(me));
}

/// The snapshot a poll must give now: its number, and the text checked.
fn snapshot(me: Owner, acked: u64) -> (u64, Out) {
    match engine::poll(me, acked) {
        Polled { pending: true, what: PollResult::Snapshot { snap, out } } => {
            check(proto::snapshot_ok(&out), "the snapshot passes the client's checks");
            check(out.preedit == texts().1, "the snapshot is the reranked text");
            check(out.pending, "the snapshot's own flag is set: it is not confirmed yet");
            (snap, out)
        }
        _ => fail("the poll did not give a snapshot with the flag set"),
    }
}

#[test]
fn the_owner_gets_the_rerank_by_asking_and_the_same_snapshot_until_it_confirms() {
    let s = session();
    let a = owner(7);
    type_sentence(a);
    finished(a);
    let (id, out) = snapshot(a, 0);
    check(id > 0, "snapshot numbers start above 0 (0 is the client's `none yet`)");
    // Not confirmed: every poll gives the same one, and applies nothing again.
    for acked in [0, id + 1, u64::MAX] {
        let (again, out2) = snapshot(a, acked);
        check(again == id && out2 == out, "the same snapshot until its number comes back");
    }
    // Confirmed: nothing is pending any more.
    check(engine::poll(a, id) == NOT_YOURS, "after the confirmation: idle, flag 0");
    check(engine::poll(a, id) == NOT_YOURS, "and it stays so");
    // The engine holds the reranked composition: Enter sends it, and the answer's flag is 0.
    let o = key(a, kind(ENTER));
    check(o.commit == texts().1 && o.preedit.is_empty() && !o.pending, "Enter sends the reranked text");
    s.end(&[a]);
}

#[test]
fn a_snapshot_not_yet_confirmed_goes_when_the_owner_types_on() {
    let s = session();
    let a = owner(7);
    type_sentence(a);
    finished(a);
    let (id, _) = snapshot(a, 0);
    // An unfinished symbol does not decode again: no new job, and the key's answer is the whole (reranked) composition, so the snapshot is not kept.
    let o = type_keys(a, "s");
    check(o.preedit.starts_with(&texts().1) && o.preedit.len() > texts().1.len(), "the key's answer shows the reranked text and the symbol");
    check(!o.pending, "nothing is pending after it");
    check(engine::poll(a, 0) == NOT_YOURS && engine::poll(a, id) == NOT_YOURS, "no snapshot is sent again");
    s.end(&[a]);
}

#[test]
fn a_key_that_does_not_decode_again_keeps_the_job() {
    let s = session();
    let a = owner(7);
    let gate = probe::GATE.lock().unwrap_or_else(PoisonError::into_inner);
    type_sentence(a);
    let takes = s.takes();
    // An unfinished symbol while the job is still not run.
    let o = type_keys(a, "s");
    check(s.takes() == takes, "an unfinished symbol leaves no new job");
    check(o.pending && probe::in_flight(a), "the job of the sentence is still wanted");
    check(matches!(engine::poll(a, 0), Polled { pending: true, what: PollResult::Running }), "a poll meanwhile says running");
    drop(gate);
    finished(a);
    match engine::poll(a, 0) {
        Polled { pending: true, what: PollResult::Snapshot { out, .. } } => {
            check(proto::snapshot_ok(&out) && out.preedit.starts_with(&texts().1) && out.preedit.len() > texts().1.len(), "the snapshot is the reranked text with the symbol still there")
        }
        _ => fail("the poll after the symbol did not give the snapshot"),
    }
    s.end(&[a]);
}

#[test]
fn one_job_runs_at_a_time_and_a_new_job_replaces_the_waiting_one() {
    let s = session();
    let a = owner(7);
    let gate = probe::GATE.lock().unwrap_or_else(PoisonError::into_inner);
    // Key by key; once the first job exists the rerank thread is given the time to pick it up, and stands at the gate with it while the rest is typed.
    let mut picked = false;
    for k in parse_keys(SENTENCE) {
        key(a, k);
        if !picked && s.takes() == 1 {
            wait_for("the rerank thread picks the first job", || probe::RUNNING.load(Ordering::SeqCst) == 1);
            picked = true;
        }
    }
    // Of the jobs that followed only the newest waits.
    let n = s.takes();
    check(n >= 3, "the sentence leaves a job after several of its syllables");
    check(probe::queued(), "the newest job waits behind the running one");
    check(s.frees() == n - 2, "every job but the running one and the newest was freed when it was replaced");
    check(matches!(engine::poll(a, 0), Polled { pending: true, what: PollResult::Running }), "a poll says running");
    drop(gate);
    finished(a);
    check(s.frees() == n - 1, "the job that was running for an older composition was freed by the rerank thread");
    check(probe::RUNNING_MAX.load(Ordering::SeqCst) == 1, "never two runs at once");
    snapshot(a, 0);
    s.end(&[a]);
}

#[test]
fn another_owner_drops_the_jobs_and_gets_nothing_of_them() {
    let s = session();
    let (a, b) = (owner(7), owner(7));
    type_sentence(a);
    finished(a);
    let o = type_keys(b, "s");
    check(o.took_over && o.preedit == w("ㄋ"), "B takes the engine and sees only its own key");
    check(!probe::has_done(a) && !probe::has_done(b), "A's finished job is gone");
    check(engine::poll(a, 0) == NOT_YOURS, "A gets nothing");
    check(engine::poll(b, 0) == NOT_YOURS, "B has nothing to get");
    s.end(&[a, b]);
}

#[test]
fn an_enter_waits_at_most_100_ms_for_a_job_and_then_sends_what_is_shown() {
    let s = session();
    let a = owner(7);
    let gate = probe::GATE.lock().unwrap_or_else(PoisonError::into_inner);
    type_sentence(a);
    let waits = probe::ENTER_WAITS.load(Ordering::SeqCst);
    let t0 = Instant::now();
    let o = key(a, kind(ENTER));
    let took = t0.elapsed();
    check(probe::ENTER_WAITS.load(Ordering::SeqCst) == waits + 1, "the Enter waited for the job");
    check(took >= Duration::from_millis(95), "it waited the whole limit");
    // The limit itself is 100 ms; the margin is for a loaded test machine, not part of the rule.
    check(took < Duration::from_millis(500), "and not much longer");
    check(o.commit == texts().0 && o.preedit.is_empty() && !o.pending, "it sent the text that was shown");
    drop(gate);
    s.end(&[a]);
}

#[test]
fn an_enter_after_the_job_finished_sends_the_reranked_text_without_waiting() {
    let s = session();
    let a = owner(7);
    type_sentence(a);
    finished(a);
    let waits = probe::ENTER_WAITS.load(Ordering::SeqCst);
    let o = key(a, kind(ENTER));
    check(o.commit == texts().1 && o.preedit.is_empty(), "the Enter sends the reranked text though nobody polled");
    check(probe::ENTER_WAITS.load(Ordering::SeqCst) == waits, "and it did not wait");
    s.end(&[a]);
}

#[test]
fn an_enter_with_the_window_open_does_not_wait() {
    let s = session();
    let a = owner(7);
    let gate = probe::GATE.lock().unwrap_or_else(PoisonError::into_inner);
    type_sentence(a);
    let o = type_keys(a, "down");
    check(o.candidate_selected >= 0, "the window is open");
    let waits = probe::ENTER_WAITS.load(Ordering::SeqCst);
    let o = key(a, kind(ENTER));
    check(probe::ENTER_WAITS.load(Ordering::SeqCst) == waits, "an Enter that picks a row does not wait");
    check(o.candidate_selected == -1 && !o.preedit.is_empty() && o.commit.is_empty(), "it picked the row and the composition stays");
    drop(gate);
    s.end(&[a]);
}

/// Runs `f` on another thread; `None` when it has not come back in 20 s (a deadlock).
fn elsewhere<T: Send + 'static>(f: impl FnOnce() -> T + Send + 'static) -> mpsc::Receiver<T> {
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        let _ = tx.send(f());
    });
    rx
}

fn back<T>(rx: mpsc::Receiver<T>, what: &str) -> T {
    rx.recv_timeout(Duration::from_secs(20)).ok().need(what)
}

#[test]
fn while_an_enter_waits_other_connections_are_served_and_a_new_owner_stops_the_apply() {
    let s = session();
    let (a, b) = (owner(7), owner(7));
    let gate = probe::GATE.lock().unwrap_or_else(PoisonError::into_inner);
    type_sentence(a);
    let waits = probe::ENTER_WAITS.load(Ordering::SeqCst);
    let enter = elsewhere(move || engine::key(a, kind(ENTER)));
    wait_for("A's Enter starts to wait", || probe::ENTER_WAITS.load(Ordering::SeqCst) == waits + 1);
    // B's key while A waits: not `Busy`. B takes the engine.
    match engine::key(b, parse_keys("s")[0]) {
        Answer::Out(o) => check(o.took_over && o.preedit == w("ㄋ"), "B's key is answered while A's Enter waits"),
        _ => fail("B's key got Busy or NotReady while A's Enter waited"),
    }
    // The job would finish now, but A no longer owns the engine: nothing is applied, and A's Enter is the key of a new owner on an empty engine.
    drop(gate);
    match back(enter, "A's Enter comes back") {
        Answer::Out(o) => {
            check(o.took_over, "A is told the engine changed hands");
            check(o.commit.is_empty() && o.preedit.is_empty(), "A's Enter sends neither the n-gram text nor the reranked one");
        }
        _ => fail("A's Enter got no Out"),
    }
    check(engine::poll(a, 0) == NOT_YOURS && engine::poll(b, 0) == NOT_YOURS, "no snapshot for either");
    s.end(&[a, b]);
}

#[test]
fn a_release_of_another_connection_while_an_enter_waits_does_not_deadlock() {
    let s = session();
    let (a, b) = (owner(7), owner(9));
    let gate = probe::GATE.lock().unwrap_or_else(PoisonError::into_inner);
    type_sentence(a);
    let waits = probe::ENTER_WAITS.load(Ordering::SeqCst);
    let enter = elsewhere(move || engine::key(a, kind(ENTER)));
    wait_for("A's Enter starts to wait", || probe::ENTER_WAITS.load(Ordering::SeqCst) == waits + 1);
    // Release and a closed connection wait for the engine lock; A's Enter gave it up, so they come back at once, and they are not A's: A keeps the engine.
    back(elsewhere(move || engine::release(b)), "B's Release comes back while A's Enter waits");
    back(elsewhere(move || engine::drop_connection(b.conn)), "B's closed connection is cleaned up while A's Enter waits");
    match back(enter, "A's Enter comes back") {
        Answer::Out(o) => check(!o.took_over && o.commit == texts().0, "A's Enter sends what was shown"),
        _ => fail("A's Enter got no Out"),
    }
    drop(gate);
    s.end(&[a, b]);
}

#[test]
fn a_poll_never_waits_for_the_engine_and_a_non_owner_never_touches_it() {
    let s = session();
    let (a, b) = (owner(7), owner(7));
    type_sentence(a);
    finished(a);
    {
        // The engine lock is held (as by a key of some connection in the core).
        let _held = probe::hold();
        check(matches!(engine::poll(a, 0), Polled { pending: true, what: PollResult::Running }), "the owner's poll cannot take the lock: running, flag 1");
        // Not the owner: the same fixed answer as ever, with the lock held by someone else, so it was not waited for.
        check(engine::poll(b, 0) == NOT_YOURS, "B with A's ctx: the fixed answer");
        check(engine::poll(Owner { ctx: 8, ..a }, 0) == NOT_YOURS, "A with another ctx: the fixed answer");
        check(matches!(engine::key(b, parse_keys("s")[0]), Answer::Busy), "a key meanwhile is Busy");
    }
    // The same askers with the lock free: byte for byte the same answer but for the seq, and it is 18 bytes, so nothing of A's composition is in it.
    let frame = |seq: u64, p: Polled| proto::encode_server(seq, &ServerMsg::Polled(p)).need("encode");
    let fixed = frame(1, NOT_YOURS);
    for (seq, asker) in [(2u64, b), (3, Owner { ctx: 8, ..a }), (u64::MAX, Owner { conn: b.conn, ctx: 8 })] {
        for acked in [0, 1, u64::MAX] {
            let f = frame(seq, engine::poll(asker, acked));
            check(f.len() == proto::HEADER_LEN + 2 && f[..8] == fixed[..8] && f[proto::HEADER_LEN..] == fixed[proto::HEADER_LEN..], "a non-owner's answer is fixed but for the seq");
            check(f[8..16] == seq.to_le_bytes(), "and carries the seq of its poll");
        }
    }
    check(probe::has_done(a), "none of those polls touched A's finished job");
    // A, with its own ctx, still gets its snapshot.
    snapshot(a, 0);
    s.end(&[a, b]);
}

#[test]
fn the_snapshot_is_gone_after_a_release_a_reset_a_closed_connection_or_a_new_owner() {
    let s = session();
    for way in 0..5 {
        let (a, b) = (owner(7), owner(7));
        type_sentence(a);
        finished(a);
        snapshot(a, 0);
        match way {
            0 => engine::release(a),
            // `Reset` and a closed connection are the same call in the server.
            1 => engine::drop_connection(a.conn),
            2 => {
                type_keys(b, "s");
            }
            // Someone else's Release or closed connection: the snapshot is cleared whoever sent it; A still owns the engine.
            3 => engine::release(b),
            _ => engine::drop_connection(b.conn),
        }
        check(engine::poll(a, 0) == NOT_YOURS, "A, asking with its old ctx, gets `idle` and no snapshot");
        if way >= 3 {
            // The engine still holds A's reranked composition, and the next key's answer carries it.
            check(key(a, kind(ENTER)).commit == texts().1, "A's composition itself is untouched by B's Release");
        }
        engine::release(a);
        engine::release(b);
    }
    s.end(&[]);
}

#[test]
fn a_finished_job_nobody_asked_for_is_freed_once_on_every_way_out() {
    let s = session();
    for way in 0..4 {
        let (a, b) = (owner(7), owner(7));
        type_sentence(a);
        finished(a);
        let (takes, frees) = (s.takes(), s.frees());
        check(frees == takes - 1, "only the finished job is alive");
        match way {
            // (a) a key that decodes again: ㄋㄧˇ leaves a new job, which replaces the finished one.
            0 => {
                type_keys(a, "s u 3");
                check(s.takes() > takes, "the new syllable left a new job");
            }
            // (b) B takes over.
            1 => {
                type_keys(b, "s");
            }
            // (c) Release.
            2 => engine::release(a),
            // (d) the connection closes.
            _ => engine::drop_connection(a.conn),
        }
        engine::release(a);
        engine::release(b);
        idle();
        check(s.takes() == s.frees(), "rerank_free count == rerank_take count");
    }
    s.end(&[]);
}

/// The warm-up (docs/spec/windows-tip.md §5.2) on an engine with the rerank on: the job its keys leave is taken and freed, never run, and nothing is left behind.
#[test]
fn the_warm_up_frees_the_job_its_keys_leave() {
    let s = session();
    let core = Direct::with(0, true);
    // SAFETY: a live engine of this test, used on this thread only.
    check(unsafe { engine::warm_with(core.0, &engine::WARM_KEYS) }, "the warm-up resets");
    check(s.takes() == 1 && s.frees() == 1, "the warm-up's job was handed out and freed");
    check(core.settle().is_none(), "no job is left in the engine");
    let o = core.key(kind(ENTER));
    check(o.commit.is_empty() && o.preedit.is_empty(), "nothing is composed or committed after the warm-up");
    s.end(&[]);
}

/// The three sentences of the VM run (docs/spec/windows-tip.md §5.11 item 12), public rows whose reading differs with the rerank and is right with it: the keys on the
/// standard layout, the text with the rerank off, the text with it on. The on and off texts are what the evaluation CLI prints for the row with and without `--tinylm`
/// (the commands of §5.11 item 12); here the host's own engine types them key by key, in both key schemes.
pub const VM_RERANK_SENTENCES: &[(&str, &str, &str)] = &[
    // eval/dev/user-reported.txt: ㄋㄧˇ ㄗㄞˋ ㄒㄧㄢˇ ㄕˋ ㄧ ㄘˋ
    ("s u 3 y 9 4 v u 0 3 g 4 u space h 4", "你在顯示一次", "你再顯示一次"),
    // eval/dev/user-typing.txt: ㄨㄛˇ ㄅㄚˇ ㄒㄧㄤˋ ㄌㄧㄤˋ ㄗ ㄌㄧㄠˋ ㄎㄨˋ ㄐㄧㄚˋ ㄏㄠˇ ㄌㄜ˙
    ("j i 3 1 8 3 v u ; 4 x u ; 4 y space x u l 4 d j 4 r u 8 4 c l 3 x k 7", "我把向量資料庫價好了", "我把向量資料庫架好了"),
    // eval/dev/user-reported.txt: ㄗˋ ㄉㄨㄥˋ ㄍㄥ ㄒㄧㄣ ㄧㄝˇ ㄗㄨㄛˋ ㄕㄤˋ ㄑㄩˋ
    ("y 4 2 j / 4 e / space v u p space u , 3 y j i 4 g ; 4 f m 4", "自動更新也坐上去", "自動更新也做上去"),
];

#[test]
fn the_vm_sentences_read_as_the_cli_says_with_the_rerank_on_and_off() {
    for scheme in [0, 1] {
        let (off, on) = (Direct::with(scheme, false), Direct::with(scheme, true));
        for (keys, want_off, want_on) in VM_RERANK_SENTENCES {
            let (shown, sent, jobs) = off.typed(keys);
            check(jobs == 0 && shown == w(want_off) && sent == w(want_off), &format!("scheme {scheme}: rerank off"));
            let (shown, sent, jobs) = on.typed(keys);
            check(jobs > 0 && shown == w(want_on) && sent == w(want_on), &format!("scheme {scheme}: rerank on"));
            // Typed without ever applying a rerank, the engine with the rerank on shows the same text as the one with it off, key for key.
            on.reset();
            off.reset();
            for k in parse_keys(keys) {
                check(on.key(k) == off.key(k), &format!("scheme {scheme}: before a rerank applies, the answer is the n-gram's"));
            }
        }
    }
}
