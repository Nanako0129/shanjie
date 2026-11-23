//! When the text service asks the host for the result of the semantic rerank, and what it does with the answer (docs/spec/windows-tip.md §5.4, §5.6). Pure:
//! no TSF, no pipe, no clock of its own, so it is built and tested on every platform. `tip.rs` owns the timer and the edit sessions and gives this module the
//! world through `Port`; the tests give it a simulated host and document.
//!
//! - `Poller`: the timer's state. The answer to a key that carries the pending flag arms it for `WINDOW_MS`; every key answer starts that span again, an answer
//!   to a poll never does. It stops when the composition ends or the focus leaves (`ended`), when an answer says nothing is pending, when the span runs out,
//!   and after one poll that timed out (until the next key answer with the flag). The next key alone never stops it: a key that does not decode again
//!   keeps the job valid (`shanjie.h`).
//! - A tick (`tick`) only asks for an asynchronous edit session and notes the generation and the composition of that moment. The session (`session`) checks
//!   both again before it sends anything, asks, and writes the snapshot in the same session, so the snapshot is never older than the composition it lands in.
//! - `Late`: the answers of polls that timed out before a byte arrived are dropped when they turn up, by their seq.
use crate::timing::{poll_result, EV_KEY_PENDING, EV_POLL, EV_RERANK_SHOWN};
use shanjie_win_proto::{self as proto, Out};

/// How often the timer fires while it is armed.
pub const TICK_MS: u32 = 30;
/// How long after the last key answer with the flag the polls go on.
pub const WINDOW_MS: u64 = 600;
/// The deadline of one poll, write and read together (the key's is 200 ms): it runs on the UI thread outside any key.
pub const POLL_MS: u64 = 40;

/// What one poll came to.
pub enum Asked {
    /// The rerank changed the composition: all of it as it is now, and the number to hand back with the next poll.
    Snapshot { snap: u64, out: Out, pending: bool },
    Idle { pending: bool },
    /// A job is waiting or running, or the engine was busy.
    Running,
    /// No byte of the answer arrived in time. The connection stays (the late answer is dropped when it comes, `Late`).
    TimedOut,
    /// Anything else: the answer was cut off part-way, was malformed or of the wrong type, the pipe broke. The connection is closed.
    Failed,
}

/// The world as one asynchronous edit session sees it.
pub trait Port {
    /// The composition this text service has open, by identity (its interface pointer); `None`: none.
    fn comp(&self) -> Option<usize>;
    /// The clock of the timing file (µs); 0 in a build without it.
    fn now_us(&self) -> u64;
    /// Sends one poll that confirms snapshot `acked` (0: none) and reads its answer.
    fn ask(&mut self, acked: u64) -> Asked;
    /// Sets the text, the underline and the caret of the open composition, and nothing else. False when the document refused.
    fn write(&mut self, out: &Out) -> bool;
    /// The host's state for the composition is out of reach or cannot be trusted (docs/spec/windows-tip.md §5.6, first row): the composition ends, its text stays, the connection closes.
    fn give_up(&mut self);
    /// One line of the timing file (numbers only).
    fn record(&mut self, event: u32, a: u64, b: u64);
}

/// A tick's request for an edit session: what was true when it was made.
pub struct Req {
    generation: u64,
    comp: usize,
    id: u64,
}

#[derive(Default)]
pub struct Poller {
    armed: bool,
    /// The polls stop at this time (ms of the caller's clock).
    until_ms: u64,
    /// Counts every key, every end of a composition and every change of focus.
    generation: u64,
    /// The number of the last snapshot written into the document; the next poll hands it back.
    acked: u64,
    /// When the last key answer with the flag arrived (µs), until a rerank is on the screen: the start of event 73.
    flagged_us: Option<u64>,
    /// A session was asked for and has not run: no second one is asked for meanwhile.
    asked: Option<u64>,
    requests: u64,
}

impl Poller {
    /// The timer should be running.
    pub fn armed(&self) -> bool {
        self.armed
    }

    /// A key reached this text service (whatever becomes of it): sessions asked for before it must not ask or write.
    pub fn key(&mut self) {
        self.generation += 1;
    }

    /// The host answered a key with an `Out`. `pending`: the answer carries the flag and the composition is open. Arms the timer and starts the span again, or
    /// stops the timer when nothing is pending. `record` gets event 74 when the flag is set.
    pub fn key_answered(&mut self, pending: bool, now_ms: u64, now_us: u64, record: impl FnOnce(u32, u64, u64)) {
        if !pending {
            return self.stop();
        }
        self.armed = true;
        self.until_ms = now_ms + WINDOW_MS;
        self.flagged_us = Some(now_us);
        // A session asked for before this key will not ask (its generation is old): the next tick may ask for a new one.
        self.asked = None;
        record(EV_KEY_PENDING, 1, 1);
    }

    fn stop(&mut self) {
        self.armed = false;
        self.flagged_us = None;
        self.asked = None;
    }

    /// The app refused the edit session a tick asked for: it will not run, so the next tick may ask again.
    pub fn refused(&mut self) {
        self.asked = None;
    }

    /// The composition ended or the focus left.
    pub fn ended(&mut self) {
        self.generation += 1;
        self.stop();
    }

    /// The timer fired. `comp`: the open composition; `window`: the candidate window is open (the core applies no rerank then, so nothing is asked).
    /// `Some`: ask for an asynchronous edit session on the composition's context and run `session` with this in it.
    pub fn tick(&mut self, now_ms: u64, comp: Option<usize>, window: bool) -> Option<Req> {
        if !self.armed {
            return None;
        }
        let Some(comp) = comp.filter(|_| now_ms < self.until_ms) else {
            self.stop();
            return None;
        };
        if window || self.asked.is_some() {
            return None;
        }
        self.requests += 1;
        self.asked = Some(self.requests);
        Some(Req { generation: self.generation, comp, id: self.requests })
    }

    /// The edit session a tick asked for is running. Nothing is sent, written or opened unless there still is a composition, it is the same object, and no
    /// key, end of composition or change of focus came in between. Then one poll, and its snapshot is written here, in the same session.
    pub fn session(&mut self, req: Req, p: &mut impl Port) {
        if self.asked == Some(req.id) {
            self.asked = None;
        }
        if !self.armed || self.generation != req.generation || p.comp() != Some(req.comp) {
            return;
        }
        let t0 = p.now_us();
        let asked = p.ask(self.acked);
        let result = match &asked {
            Asked::Snapshot { .. } => poll_result::SNAPSHOT,
            Asked::Idle { .. } => poll_result::IDLE,
            Asked::Running => poll_result::RUNNING,
            Asked::TimedOut => poll_result::TIMEOUT,
            Asked::Failed => poll_result::FAILED,
        };
        let t1 = p.now_us();
        p.record(EV_POLL, t1.saturating_sub(t0), result);
        match asked {
            Asked::Snapshot { snap, out, pending } => {
                // A snapshot that is not "this composition, changed" (it commits, it is empty, it has a window) is a malformed answer; so is one the document refuses.
                if !proto::snapshot_ok(&out) || !p.write(&out) {
                    p.give_up();
                    return self.ended();
                }
                self.acked = snap;
                if let Some(t) = self.flagged_us.take() {
                    let now = p.now_us();
                    p.record(EV_RERANK_SHOWN, t, now.max(t));
                }
                if !pending {
                    self.stop();
                }
            }
            Asked::Idle { pending } => {
                if !pending {
                    self.stop();
                }
            }
            Asked::Running => {}
            // One timeout stops the polls until the next key answer with the flag: a host that is slow now is not asked 20 more times.
            Asked::TimedOut => self.stop(),
            Asked::Failed => {
                p.give_up();
                self.ended();
            }
        }
    }
}

/// The polls of one connection that timed out before any byte of their answer arrived. Their answers are still on their way: each is dropped when it is
/// read, in front of the answer that is waited for. Lives in the connection object and goes with it.
#[derive(Default)]
pub struct Late {
    /// The seq of the last poll that timed out so.
    upto: u64,
    /// How many such answers have not turned up yet.
    owed: u32,
}

pub enum Seq {
    /// The answer that is waited for.
    Mine,
    /// The answer of a poll that timed out: drop it and read on.
    Late,
    /// Any other seq: malformed.
    Bad,
}

impl Late {
    /// The poll `seq` timed out with `read` bytes of its answer taken off the pipe. True: none, the connection stays and the answer is owed. False: the
    /// stream is cut inside a message, the connection must close.
    pub fn timed_out(&mut self, seq: u64, read: usize) -> bool {
        if read != 0 {
            return false;
        }
        self.upto = seq;
        self.owed += 1;
        true
    }

    /// An answer with seq `got` was read while the answer to `want` is waited for. No more answers are dropped than polls timed out, and a seq between the
    /// last such poll and `want` is never dropped.
    pub fn judge(&mut self, got: u64, want: u64) -> Seq {
        if got == want {
            return Seq::Mine;
        }
        if self.owed > 0 && got <= self.upto {
            self.owed -= 1;
            if self.owed == 0 {
                self.upto = 0;
            }
            return Seq::Late;
        }
        Seq::Bad
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::timing::format_line;
    use std::collections::VecDeque;

    fn w(s: &str) -> Vec<u16> {
        s.encode_utf16().collect()
    }

    fn out(preedit: &str, pending: bool) -> Out {
        let preedit = w(preedit);
        Out {
            ran: true,
            core_error: false,
            took_over: false,
            handled: true,
            pending,
            commit: vec![],
            cursor_utf16: preedit.len() as u32,
            preedit,
            candidate_selected: -1,
            candidate_first: 0,
            candidate_total: 0,
            candidates: vec![],
        }
    }

    /// A poll the simulated host does not answer by its own rules.
    enum Fault {
        /// The host does its part (applies a finished job), the answer does not arrive in time.
        TimedOut,
        Failed,
        /// The host sends this as the snapshot.
        Forged(Out),
    }

    /// The host and the document, simulated: one engine composition, one job, one applied snapshot (the rules of docs/spec/windows-tip.md §5.2), and a
    /// document that is one composition's text.
    #[derive(Default)]
    struct World {
        /// The open composition: its identity and the text on the screen.
        comp: Option<(usize, Vec<u16>)>,
        /// The host's job: `Some(false)` running, `Some(true)` run and not applied.
        job: Option<bool>,
        /// What the rerank will make of the composition.
        reranked: Vec<u16>,
        /// The applied snapshot nobody confirmed yet.
        snapshot: Option<(u64, Out)>,
        snaps: u64,
        faults: VecDeque<Fault>,
        clock_us: u64,
        /// Everything the client did: the `acked` of every poll, every write, every give-up, every timing line.
        asks: Vec<u64>,
        writes: Vec<Vec<u16>>,
        gave_up: u32,
        lines: Vec<(u32, u64, u64)>,
    }

    impl World {
        /// The host's side of one poll (`engine::poll` for the owner).
        fn host(&mut self, acked: u64) -> Asked {
            match &self.snapshot {
                Some((id, out)) if *id != acked => return Asked::Snapshot { snap: *id, out: out.clone(), pending: true },
                _ => self.snapshot = None,
            }
            match self.job {
                Some(true) => {
                    self.job = None;
                    self.snaps += 1;
                    let o = Out { preedit: self.reranked.clone(), cursor_utf16: self.reranked.len() as u32, ..out("", true) };
                    self.snapshot = Some((self.snaps, o.clone()));
                    Asked::Snapshot { snap: self.snaps, out: o, pending: true }
                }
                Some(false) => Asked::Running,
                None => Asked::Idle { pending: false },
            }
        }
        fn text(&self) -> Option<&[u16]> {
            self.comp.as_ref().map(|c| c.1.as_slice())
        }
    }

    impl Port for World {
        fn comp(&self) -> Option<usize> {
            self.comp.as_ref().map(|c| c.0)
        }
        fn now_us(&self) -> u64 {
            self.clock_us
        }
        fn ask(&mut self, acked: u64) -> Asked {
            self.asks.push(acked);
            self.clock_us += 700;
            match self.faults.pop_front() {
                Some(Fault::TimedOut) => {
                    let _ = self.host(acked);
                    Asked::TimedOut
                }
                Some(Fault::Failed) => Asked::Failed,
                Some(Fault::Forged(o)) => Asked::Snapshot { snap: 99, out: o, pending: true },
                None => self.host(acked),
            }
        }
        fn write(&mut self, o: &Out) -> bool {
            self.clock_us += 300;
            match &mut self.comp {
                Some(c) => {
                    c.1 = o.preedit.clone();
                    self.writes.push(o.preedit.clone());
                    true
                }
                None => false,
            }
        }
        fn give_up(&mut self) {
            self.gave_up += 1;
            self.comp = None;
        }
        fn record(&mut self, event: u32, a: u64, b: u64) {
            self.lines.push((event, a, b));
        }
    }

    /// The text service around the poller, as `tip.rs` drives it: keys, the timer, the sessions (run at once, or held back as a host app may).
    #[derive(Default)]
    struct Sim {
        p: Poller,
        w: World,
        now_ms: u64,
        /// Requests whose edit session the app has not run yet.
        held: VecDeque<Req>,
    }

    impl Sim {
        /// A key that decodes again: the screen shows the n-gram's `text`, the host has a new job that will make `reranked` of it.
        fn key(&mut self, text: &str, reranked: &str) {
            self.p.key();
            self.w.comp = Some((self.w.comp.as_ref().map_or(1, |c| c.0), w(text)));
            self.w.job = Some(false);
            self.w.snapshot = None;
            self.w.reranked = w(reranked);
            self.answered(true);
        }
        /// A key that does not decode again (an unfinished symbol): the job stays; the key's answer is the whole composition as the engine has it.
        fn symbol(&mut self, text: &str) {
            self.p.key();
            let pending = self.w.job.is_some();
            self.w.snapshot = None;
            if let Some(c) = &mut self.w.comp {
                c.1 = w(text);
            }
            self.answered(pending);
        }
        fn answered(&mut self, pending: bool) {
            let (now_ms, now_us) = (self.now_ms, self.w.clock_us);
            let w = &mut self.w;
            self.p.key_answered(pending, now_ms, now_us, |e, a, b| w.lines.push((e, a, b)));
        }
        /// The host's job finishes its run.
        fn job_done(&mut self) {
            self.w.job = Some(true);
        }
        /// `ms` pass; the timer fires every `TICK_MS` while it is armed, and each session it asks for runs at once.
        fn run(&mut self, ms: u64) {
            self.advance(ms, false);
        }
        /// As `run`, but the app does not run the sessions: they wait in `held`.
        fn run_holding(&mut self, ms: u64) {
            self.advance(ms, true);
        }
        fn advance(&mut self, ms: u64, hold: bool) {
            let end = self.now_ms + ms;
            while self.p.armed() && self.now_ms + u64::from(TICK_MS) <= end {
                self.now_ms += u64::from(TICK_MS);
                self.w.clock_us += u64::from(TICK_MS) * 1000;
                let comp = self.w.comp();
                if let Some(req) = self.p.tick(self.now_ms, comp, false) {
                    if hold {
                        self.held.push_back(req);
                    } else {
                        self.p.session(req, &mut self.w);
                    }
                }
            }
            self.w.clock_us += (end - self.now_ms) * 1000;
            self.now_ms = end;
        }
        /// The app runs the sessions it held back.
        fn release_held(&mut self) {
            while let Some(req) = self.held.pop_front() {
                self.p.session(req, &mut self.w);
            }
        }
        fn asks(&self) -> usize {
            self.w.asks.len()
        }
    }

    #[test]
    fn the_flag_arms_the_timer_and_the_session_asks_and_writes() {
        let mut s = Sim::default();
        s.key("我在", "我再");
        assert!(s.p.armed());
        s.run(60);
        assert!(s.asks() == 2 && s.w.writes.is_empty(), "two ticks, two polls, the job still runs");
        s.job_done();
        s.run(30);
        assert!(s.w.text() == Some(&w("我再")[..]) && s.w.writes == [w("我再")], "the snapshot is written");
        assert!(s.p.armed(), "the snapshot is not confirmed yet: the flag is still set");
        // The next poll hands the snapshot's number back; the host then has nothing pending and the timer stops.
        s.run(30);
        assert!(s.w.asks == [0, 0, 0, 1] && !s.p.armed() && s.w.writes.len() == 1 && s.w.snapshot.is_none());
        s.run(300);
        assert!(s.asks() == 4, "no polls once the timer stopped");
        assert!(s.w.gave_up == 0);
    }

    #[test]
    fn a_key_that_does_not_decode_again_keeps_the_polls_and_the_snapshot_still_lands() {
        let mut s = Sim::default();
        s.key("我在", "我再");
        s.run(30);
        // An unfinished symbol while the job runs: the timer is not cancelled, the span starts again.
        s.symbol("我在ㄋ");
        assert!(s.p.armed());
        s.w.reranked = w("我再ㄋ");
        s.job_done();
        s.run(30);
        assert!(s.w.text() == Some(&w("我再ㄋ")[..]), "the poll after the symbol still writes the reranked composition");
    }

    #[test]
    fn a_session_asked_for_before_a_key_does_not_ask_and_the_next_tick_writes() {
        let mut s = Sim::default();
        s.key("我在", "我再");
        s.job_done();
        s.run_holding(30);
        assert!(s.held.len() == 1 && s.asks() == 0);
        // A key gets in before the app runs the session: the generation moved.
        s.symbol("我在ㄋ");
        s.w.reranked = w("我再ㄋ");
        s.release_held();
        assert!(s.asks() == 0 && s.w.writes.is_empty(), "the old session sends no poll and writes nothing");
        s.run(30);
        assert!(s.asks() == 1 && s.w.text() == Some(&w("我再ㄋ")[..]), "the next tick's session writes the reranked text");
    }

    #[test]
    fn a_session_whose_composition_ended_asks_nothing_writes_nothing_and_opens_nothing() {
        let mut s = Sim::default();
        s.key("我在", "我再");
        s.job_done();
        s.run_holding(30);
        // The app ended the composition (`OnCompositionTerminated`) before it ran the session.
        s.w.comp = None;
        s.p.ended();
        s.release_held();
        assert!(s.asks() == 0 && s.w.writes.is_empty() && s.w.comp.is_none() && !s.p.armed());
        // A new composition (another object) before an old session runs: not the same composition, even if the timer is armed again.
        let mut s = Sim::default();
        s.key("我在", "我再");
        s.job_done();
        s.run_holding(30);
        s.w.comp = Some((2, w("你")));
        s.release_held();
        assert!(s.asks() == 0 && s.w.text() == Some(&w("你")[..]), "a session for another composition object does nothing");
    }

    #[test]
    fn only_one_session_is_asked_for_at_a_time() {
        let mut s = Sim::default();
        s.key("我在", "我再");
        s.run_holding(300);
        assert!(s.held.len() == 1, "the app sits on the first session: no pile of sessions behind it");
        s.job_done();
        s.release_held();
        assert!(s.asks() == 1 && s.w.writes.len() == 1);
    }

    #[test]
    fn the_polls_go_on_for_600_ms_after_the_last_flagged_key_answer_and_a_new_one_starts_them_again() {
        let mut s = Sim::default();
        s.key("我在", "我再");
        // The job never finishes: a poll every 30 ms while less than 600 ms have passed, none after.
        s.run(570);
        assert!(s.asks() == 19 && s.p.armed());
        s.run(30);
        assert!(s.asks() == 19 && !s.p.armed(), "the tick at 600 ms stops the timer and asks nothing");
        s.run(1000);
        assert!(s.asks() == 19);
        // The answer to a poll (`running`, flag set) never started the span again: it ended 600 ms after the key.
        // Another key answer with the flag: the timer starts again, for 600 ms from then.
        s.symbol("我在ㄋ");
        assert!(s.p.armed());
        s.run(300);
        assert!(s.asks() == 29);
        // A key answer in the middle starts the span again.
        s.symbol("我在ㄋㄧ");
        s.run(570);
        assert!(s.asks() == 48 && s.p.armed());
        s.run(60);
        assert!(!s.p.armed());
    }

    #[test]
    fn a_poll_that_times_out_stops_the_timer_until_the_next_flagged_key_answer_and_keeps_the_connection() {
        let mut s = Sim::default();
        s.key("我在", "我再");
        s.job_done();
        // The host applies the job, the answer does not arrive in time.
        s.w.faults.push_back(Fault::TimedOut);
        s.run(30);
        assert!(s.asks() == 1 && s.w.writes.is_empty() && !s.p.armed() && s.w.snapshot.is_some());
        assert!(s.w.gave_up == 0 && s.w.comp.is_some(), "the connection and the composition stay");
        s.run(300);
        assert!(s.asks() == 1, "no poll until a key answer with the flag");
        // The host kept the applied snapshot, so its flag is set in the next key's answer; the timer starts again and the next poll brings the snapshot.
        s.p.key();
        s.answered(true);
        s.run(30);
        assert!(s.asks() == 2 && s.w.text() == Some(&w("我再")[..]), "the tick after the next key writes the reranked text");
        assert!(s.w.gave_up == 0);
    }

    #[test]
    fn a_failed_poll_ends_the_composition_and_closes_the_connection() {
        let mut s = Sim::default();
        s.key("我在", "我再");
        s.w.faults.push_back(Fault::Failed);
        s.run(30);
        assert!(s.w.gave_up == 1 && s.w.comp.is_none() && !s.p.armed() && s.w.writes.is_empty());
        s.run(300);
        assert!(s.asks() == 1);
    }

    #[test]
    fn a_snapshot_that_commits_or_is_not_a_composition_changes_nothing_in_the_document() {
        let forged: [fn(&mut Out); 4] = [
            |o| o.commit = w("壞"),
            |o| {
                o.preedit = vec![];
                o.cursor_utf16 = 0;
            },
            |o| {
                o.candidates = vec![w("壞")];
                o.candidate_selected = 0;
                o.candidate_total = 1;
            },
            |o| o.took_over = true,
        ];
        for f in forged {
            let mut s = Sim::default();
            s.key("我在", "我再");
            let mut o = out("我再", true);
            f(&mut o);
            s.w.faults.push_back(Fault::Forged(o));
            s.run(30);
            assert!(s.w.writes.is_empty(), "nothing is written");
            assert!(s.w.gave_up == 1 && !s.p.armed(), "it is a malformed answer: the connection closes");
        }
        // The composition is gone when the session runs (no `ended` reached the poller): nothing is asked, nothing written, nothing opened.
        let mut s = Sim::default();
        s.key("我在", "我再");
        s.job_done();
        s.w.comp = None;
        s.run(30);
        assert!(s.asks() == 0 && s.w.writes.is_empty() && s.w.comp.is_none() && !s.p.armed());
    }

    #[test]
    fn nothing_is_asked_while_the_candidate_window_is_open() {
        let mut p = Poller::default();
        p.key_answered(true, 0, 0, |_, _, _| {});
        assert!(p.tick(30, Some(1), true).is_none() && p.armed(), "no session with the window open, the timer stays");
        assert!(p.tick(60, Some(1), false).is_some());
    }

    #[test]
    fn the_three_timing_events_are_written_at_their_moments_with_numbers_only() {
        let mut s = Sim::default();
        s.w.clock_us = 5_000;
        s.key("我在", "我再");
        assert!(s.w.lines == [(EV_KEY_PENDING, 1, 1)], "74: the key's answer carried the flag");
        s.run(30);
        // 72: the length of the exchange (the simulated one takes 700 µs) and its result.
        assert!(s.w.lines[1] == (EV_POLL, 700, poll_result::RUNNING));
        s.job_done();
        s.run(30);
        assert!(s.w.lines[2] == (EV_POLL, 700, poll_result::SNAPSHOT));
        // 73: from the key answer with the flag (5,000) to the composition written (two ticks, two polls and the write later).
        assert!(s.w.lines[3] == (EV_RERANK_SHOWN, 5_000, 5_000 + 60_000 + 2 * 700 + 300));
        s.run(30);
        assert!(s.w.lines[4] == (EV_POLL, 700, poll_result::IDLE) && s.w.lines.len() == 5);
        // A key answer without the flag writes nothing; a timeout and a failure have their own results.
        s.p.key();
        s.answered(false);
        assert!(s.w.lines.len() == 5);
        for (fault, result) in [(Fault::TimedOut, poll_result::TIMEOUT), (Fault::Failed, poll_result::FAILED)] {
            let mut s = Sim::default();
            s.key("我在", "我再");
            s.w.faults.push_back(fault);
            s.run(30);
            assert!(s.w.lines[1] == (EV_POLL, 700, result) && s.w.lines.len() == 2);
        }
        // Every line is digits, tabs and a newline.
        for (e, a, b) in s.w.lines {
            assert!(format_line(e, a, b).bytes().all(|c| c.is_ascii_digit() || c == b'\t' || c == b'\n'));
        }
    }

    #[test]
    fn the_late_answer_of_a_poll_that_timed_out_is_dropped_and_no_other() {
        // Poll 5 timed out with nothing read; key 6 then reads the late answer first.
        let mut l = Late::default();
        assert!(l.timed_out(5, 0), "nothing read: the connection stays");
        assert!(matches!(l.judge(5, 6), Seq::Late), "the seq equal to the recorded one is dropped");
        assert!(matches!(l.judge(6, 6), Seq::Mine), "then the key's own answer is taken");
        // The record is used up: the same seq again is malformed.
        assert!(matches!(l.judge(5, 7), Seq::Bad));
        // An earlier seq is dropped too while an answer is owed, but never more answers than polls that timed out.
        let mut l = Late::default();
        assert!(l.timed_out(5, 0) && l.timed_out(8, 0));
        assert!(matches!(l.judge(5, 9), Seq::Late) && matches!(l.judge(8, 9), Seq::Late) && matches!(l.judge(7, 9), Seq::Bad));
        // A seq between the recorded one and the one waited for is malformed, as before.
        let mut l = Late::default();
        assert!(l.timed_out(5, 0));
        assert!(matches!(l.judge(6, 7), Seq::Bad) && matches!(l.judge(9, 7), Seq::Bad));
        // Nothing owed: any other seq is malformed.
        assert!(matches!(Late::default().judge(1, 2), Seq::Bad) && matches!(Late::default().judge(0, 2), Seq::Bad));
        // A new connection starts with no record (`Link` holds a fresh `Late`).
        let mut l = Late::default();
        assert!(l.timed_out(5, 0));
        l = Late::default();
        assert!(matches!(l.judge(5, 6), Seq::Bad));
    }

    #[test]
    fn a_poll_cut_off_part_way_closes_the_connection() {
        for read in [1, 3, 4, 17] {
            let mut l = Late::default();
            assert!(!l.timed_out(5, read), "{read} bytes were taken off the pipe: the stream is cut inside a message");
            assert!(matches!(l.judge(5, 6), Seq::Bad), "and nothing is owed");
        }
    }
}
