//! The bookkeeping of the host's background rerank (docs/spec/windows-tip.md §5.2, docs/spec/semantic-rerank.md §8.5): which job is waiting, which one the rerank
//! thread holds, which one has run and waits to be applied, and whose they are. Plain data with no platform code and no core, so it is tested on every platform;
//! `engine` keeps one of these behind the rerank lock.
//!
//! One slot of each kind for the whole process, never a map by owner. Ownership of a job says who frees it: a job is a value that frees itself when dropped (`engine::Job`),
//! and at any time it is in exactly one place, `queued`, the rerank thread's hands, or `done`. Every method that makes a job unwanted hands it back (`Dropped`) or
//! drops it here, so each job is freed exactly once on every path, and never while it runs: a running job is only marked unwanted (its token is no longer the
//! current one) and the rerank thread drops it when the run returns (`finish`).

/// Who a composition belongs to: the host's connection id and the client's context value, which means something only on that connection.
#[derive(Clone, Copy, PartialEq, Eq)]
pub struct Owner {
    pub conn: u64,
    pub ctx: u64,
}

/// The jobs a change made unwanted (never the running one). Dropping it frees them.
pub struct Dropped<J>(#[allow(dead_code)] Option<J>, #[allow(dead_code)] Option<J>);

pub struct Slots<J> {
    owner: Option<Owner>,
    /// The number of the newest job of `owner`; a job in the rerank thread's hands is wanted only while its number is this one.
    token: u64,
    queued: Option<J>,
    /// The number of the job the rerank thread is running.
    running: Option<u64>,
    done: Option<J>,
}

impl<J> Slots<J> {
    pub const fn new() -> Self {
        Slots { owner: None, token: 0, queued: None, running: None, done: None }
    }

    /// A job just taken from the engine for `owner`: it replaces whatever was there (the engine makes a job only when it decoded again, which makes
    /// every earlier job stale). The job the rerank thread is running becomes unwanted.
    pub fn put(&mut self, owner: Owner, job: J) -> Dropped<J> {
        let old = self.discard();
        self.owner = Some(owner);
        self.queued = Some(job);
        old
    }

    /// Nothing of the current owner is wanted any more (another owner, a release, a closed connection, a composition that ended).
    pub fn discard(&mut self) -> Dropped<J> {
        self.owner = None;
        self.token += 1;
        Dropped(self.queued.take(), self.done.take())
    }

    #[cfg(test)]
    pub fn owner(&self) -> Option<Owner> {
        self.owner
    }

    /// The rerank thread: the waiting job, now running, and its number.
    pub fn start(&mut self) -> Option<(u64, J)> {
        let job = self.queued.take()?;
        self.running = Some(self.token);
        Some((self.token, job))
    }

    /// The rerank thread: the run of job `token` returned. Kept as the finished job when it is still wanted; handed back (to be dropped by the caller,
    /// the rerank thread) when not.
    pub fn finish(&mut self, token: u64, job: J) -> Option<J> {
        self.running = None;
        if self.owner.is_some() && token == self.token {
            self.done = Some(job);
            None
        } else {
            Some(job)
        }
    }

    /// The finished job of `owner`, to apply and then drop.
    pub fn take_done(&mut self, owner: Owner) -> Option<J> {
        if self.owner == Some(owner) {
            self.done.take()
        } else {
            None
        }
    }

    pub fn has_done(&self, owner: Owner) -> bool {
        self.owner == Some(owner) && self.done.is_some()
    }

    /// A job of `owner` is waiting or running (and still wanted).
    pub fn in_flight(&self, owner: Owner) -> bool {
        self.owner == Some(owner) && (self.queued.is_some() || self.running == Some(self.token))
    }

    /// `owner` has a job that is not applied yet: waiting, running or finished.
    pub fn pending(&self, owner: Owner) -> bool {
        self.in_flight(owner) || self.has_done(owner)
    }

    #[cfg(test)]
    pub fn has_queued(&self) -> bool {
        self.queued.is_some()
    }

    /// No job waits and the rerank thread holds none (wanted or not).
    #[cfg(test)]
    pub fn is_idle(&self) -> bool {
        self.queued.is_none() && self.running.is_none()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    use std::rc::Rc;

    /// A job that counts its frees by number: freed twice or never shows in the log.
    struct Job(u32, Rc<RefCell<Vec<u32>>>);
    impl Drop for Job {
        fn drop(&mut self) {
            self.1.borrow_mut().push(self.0);
        }
    }

    struct Bench {
        freed: Rc<RefCell<Vec<u32>>>,
        made: u32,
    }

    impl Bench {
        fn new() -> Bench {
            Bench { freed: Rc::new(RefCell::new(Vec::new())), made: 0 }
        }
        fn job(&mut self) -> Job {
            self.made += 1;
            Job(self.made, self.freed.clone())
        }
        /// Every job made so far was freed exactly once.
        fn all_freed_once(&self) -> bool {
            let mut f = self.freed.borrow().clone();
            f.sort_unstable();
            f == (1..=self.made).collect::<Vec<_>>()
        }
        fn freed(&self) -> Vec<u32> {
            self.freed.borrow().clone()
        }
    }

    const A: Owner = Owner { conn: 1, ctx: 7 };
    const B: Owner = Owner { conn: 2, ctx: 7 };
    const A2: Owner = Owner { conn: 1, ctx: 8 };

    /// The rerank thread's loop body, as `engine` runs it: start, (run), finish, drop what is handed back.
    fn run_one(s: &mut Slots<Job>) -> bool {
        match s.start() {
            Some((t, j)) => {
                drop(s.finish(t, j));
                true
            }
            None => false,
        }
    }

    #[test]
    fn a_job_goes_from_queued_to_running_to_done_and_is_freed_when_taken() {
        let mut b = Bench::new();
        let mut s = Slots::new();
        drop(s.put(A, b.job()));
        assert!(s.pending(A) && s.in_flight(A) && !s.has_done(A) && !s.pending(B) && !s.pending(A2));
        let (t, j) = s.start().unwrap();
        assert!(s.start().is_none(), "one job runs at a time: nothing else is waiting");
        assert!(s.in_flight(A) && !s.has_queued());
        assert!(s.finish(t, j).is_none(), "a wanted job is kept");
        assert!(s.has_done(A) && !s.in_flight(A) && s.pending(A) && b.freed().is_empty());
        assert!(s.take_done(B).is_none() && s.take_done(A2).is_none(), "only the owner takes it");
        drop(s.take_done(A).unwrap());
        assert!(!s.pending(A) && b.all_freed_once());
    }

    #[test]
    fn a_new_job_replaces_the_queued_one_and_outdates_the_running_one() {
        let mut b = Bench::new();
        let mut s = Slots::new();
        drop(s.put(A, b.job())); // 1
        drop(s.put(A, b.job())); // 2 replaces 1 before it started
        assert!(b.freed() == [1]);
        let (t2, j2) = s.start().unwrap();
        drop(s.put(A, b.job())); // 3 while 2 runs
        assert!(b.freed() == [1], "the running job is not freed by the connection thread");
        assert!(s.in_flight(A) && s.has_queued());
        let back = s.finish(t2, j2);
        assert!(back.is_some() && !s.has_done(A), "job 2 is not kept: a newer one exists");
        drop(back);
        assert!(b.freed() == [1, 2]);
        assert!(run_one(&mut s) && s.has_done(A));
        // A finished job that a new take replaces is freed by the take.
        drop(s.put(A, b.job())); // 4
        assert!(b.freed() == [1, 2, 3] && !s.has_done(A));
        drop(s.discard());
        assert!(b.all_freed_once());
    }

    #[test]
    fn a_finished_job_nobody_asked_for_is_freed_on_each_way_out() {
        // (a) a new take, (b) another owner, (c) a release, (d) a closed connection: for the slots the last three are one `discard`.
        for way in 0..3 {
            let mut b = Bench::new();
            let mut s = Slots::new();
            drop(s.put(A, b.job()));
            assert!(run_one(&mut s) && s.has_done(A) && b.freed().is_empty());
            match way {
                0 => drop(s.put(A, b.job())),
                1 => {
                    drop(s.discard());
                    drop(s.put(B, b.job()));
                }
                _ => drop(s.discard()),
            }
            assert!(b.freed() == [1], "way {way}: the finished job is freed at once");
            assert!(!s.has_done(A));
            while run_one(&mut s) {}
            drop(s.discard());
            assert!(b.all_freed_once(), "way {way}");
        }
    }

    #[test]
    fn another_owner_drops_the_old_owners_jobs_and_a_job_running_for_them_is_not_kept() {
        let mut b = Bench::new();
        let mut s = Slots::new();
        drop(s.put(A, b.job()));
        let (t, j) = s.start().unwrap();
        drop(s.put(A, b.job())); // 2 waits behind the running 1
        drop(s.discard()); // B typed
        assert!(b.freed() == [2] && !s.pending(A) && s.owner().is_none());
        drop(s.put(B, b.job())); // 3
        drop(s.finish(t, j));
        assert!(b.freed() == [2, 1] && !s.has_done(B) && !s.has_done(A), "A's running job is dropped by the rerank thread, never kept for B");
        assert!(run_one(&mut s) && s.has_done(B) && s.take_done(A).is_none());
        drop(s.take_done(B));
        assert!(b.all_freed_once());
    }

    #[test]
    fn a_discard_while_a_job_runs_then_the_same_owner_again() {
        let mut b = Bench::new();
        let mut s = Slots::new();
        drop(s.put(A, b.job()));
        let (t, j) = s.start().unwrap();
        drop(s.discard());
        assert!(!s.pending(A) && b.freed().is_empty());
        drop(s.put(A, b.job()));
        // The old run returns after A owns the slots again: its token is old, so it is not mistaken for A's new job.
        drop(s.finish(t, j));
        assert!(b.freed() == [1] && !s.has_done(A) && s.in_flight(A));
        assert!(run_one(&mut s) && s.has_done(A));
        drop(s.discard());
        assert!(b.all_freed_once());
    }

    /// Any order of puts, discards, starts, finishes and takes frees every job exactly once.
    #[test]
    fn random_orders_free_every_job_exactly_once() {
        let mut x = 0x9E37_79B9_7F4A_7C15u64;
        let mut next = move |n: u64| {
            x ^= x >> 12;
            x ^= x << 25;
            x ^= x >> 27;
            x.wrapping_mul(0x2545_f491_4f6c_dd1d) % n
        };
        for _ in 0..2_000 {
            let mut b = Bench::new();
            let mut s = Slots::new();
            let mut hand: Option<(u64, Job)> = None;
            for _ in 0..40 {
                let who = [A, B, A2][next(3) as usize];
                match next(6) {
                    0 | 1 => drop(s.put(who, b.job())),
                    2 => drop(s.discard()),
                    3 if hand.is_none() => hand = s.start(),
                    4 => {
                        if let Some((t, j)) = hand.take() {
                            drop(s.finish(t, j));
                        }
                    }
                    _ => drop(s.take_done(who)),
                }
                assert!(!(s.has_done(A) && s.has_done(B)), "one finished job at most");
            }
            if let Some((t, j)) = hand.take() {
                drop(s.finish(t, j));
            }
            drop(s.discard());
            assert!(b.all_freed_once());
        }
    }
}
