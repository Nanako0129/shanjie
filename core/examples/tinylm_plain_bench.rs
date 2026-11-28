//! tinylm_plain_bench: the portable path's speed (docs/spec/semantic-rerank.md §2.4): 200 fixed (key, candidate) pairs scored with
//! `Backend::Plain` (the scalar kernel on Apple silicon, AVX2 or AVX-512F on x86-64), several rounds in one process. Public API only, and only the
//! calls every build since the vector kernels has (`TinyLm::load`, `TinyLm::ll_many_with`), so the same file builds in an older checkout for a
//! before/after comparison. CI builds it and does not run it.
//!
//! `cargo build --release --locked -p core --example tinylm_plain_bench`, then, in an idle window:
//!   target/release/examples/tinylm_plain_bench data/lm/tinylm.sjtl [rounds, default 3]
//! One line per round and a last line with the median: `tinylm-plain bench: round=<r> ms=<ms> sum=<sum of LLs>` / `... median_ms=<ms>`. The sum is the
//! same in every round and on every build of the same kernel (a check that both builds scored the same pairs the same way).
use core::tinylm::{Backend, TinyLm};
use std::path::Path;
use std::time::Instant;

const PAIRS: usize = 200;
/// Common Traditional Chinese characters the pairs are drawn from (one outside the model's vocabulary would score as UNK).
const CHARS: &str = "的一是不了人我在有他這中大來上國個到說們為子和你地出道也時年得就那要下以生會自著去之過家學對可她裡後小麼心多天而能好都然沒日於起還發成事只作當想看文無開手十用主行方又如前所本見經頭面公同三已老從動兩長知民樣現分將外但身些與高意進把法此實回二理美點月明其種聲全工己話兒者向情部正名定女問力機給等幾很業最間新什打便位因重被走電四第門相次東政海口使教西再平真聽世氣信北少關并內加化由卻代軍產入先山五太水萬市眼體別處總才場師書比住員九笑性通目華報立馬命張活難神數件安表原車白應路期叫死常提感金何更反合放做系計或司利受光王果親界及今京務制解各任至清物台象記邊共風戰干接它許八特覺望直服毛林題建南度統色字請交愛讓認算論百吃義科怎元社術結六功指思非流每青管夫連遠資隊跟帶花快條院變聯言權往展該領傳近留紅治決周保達辦運武半候七必城父強步完革深區即求品士轉量空甚眾技輕程告江語英基派滿式李息寫呢識極令黃德收臉錢黨倒未持取設始版雙歷越史商千片容研像找友孩站廣改議形委早房音火際則首單據導影失拿網香似斯專石若兵弟誰校讀志飛觀爭究包組造落視濟喜離雖壞";

/// The fixed pairs: keys of 0 to 40 characters and candidates of 1 to 8, from an LCG with a fixed seed.
fn pairs() -> Vec<(String, String)> {
    let chars: Vec<char> = CHARS.chars().collect();
    let mut st = 0x5eed_1234_abcd_0001u64;
    let mut next = |n: usize| {
        st = st.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        ((st >> 33) % n as u64) as usize
    };
    (0..PAIRS)
        .map(|_| {
            let (kl, cl) = (next(41), 1 + next(8));
            let k: String = (0..kl).map(|_| chars[next(chars.len())]).collect();
            let c: String = (0..cl).map(|_| chars[next(chars.len())]).collect();
            (k, c)
        })
        .collect()
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let Some(path) = args.get(1) else {
        eprintln!("usage: tinylm_plain_bench <tinylm.sjtl> [rounds]");
        std::process::exit(2);
    };
    let rounds: usize = args.get(2).map(|r| r.parse().expect("rounds")).unwrap_or(3);
    let model = TinyLm::load(Path::new(path)).expect("load");
    let owned = pairs();
    let items: Vec<(&str, &str)> = owned.iter().map(|(k, c)| (k.as_str(), c.as_str())).collect();
    let mut ms = Vec::with_capacity(rounds);
    for r in 1..=rounds {
        let t = Instant::now();
        let ll = model.ll_many_with(Backend::Plain, &items).expect("ll_many_with");
        let el = t.elapsed().as_secs_f64() * 1000.0;
        let sum: f64 = ll.iter().sum();
        println!("tinylm-plain bench: round={r} ms={el:.1} sum={sum:.9}");
        ms.push(el);
    }
    ms.sort_by(f64::total_cmp);
    println!("tinylm-plain bench: rounds={rounds} median_ms={:.1}", ms[ms.len() / 2]);
}
