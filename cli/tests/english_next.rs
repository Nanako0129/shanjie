//! `--english-next` (docs/contracts/english-next.md section 3.2) on a synthetic table and three rows, with the real models (so the pinned
//! base-arm numbers are the models' ranks; a model change moves them). Each engine is about 700 MB (the two profiles are built one after the other).
use std::process::Command;

fn files(name: &str) -> (String, String, String) {
    let dir = std::env::temp_dir().join(format!("shanjie-english-next-cli-{}-{name}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let (rows, table, words) = (dir.join("rows.txt"), dir.join("table.tsv"), dir.join("words.txt"));
    // joe is followed by 水 (30) and 說 (20); the second line of rows is not a row and is skipped.
    std::fs::write(&rows, "joe|水|ㄕㄨㄟˇ\nnot a row\njoe|說|ㄕㄨㄛ\njoe|是|ㄕˋ\n").unwrap();
    std::fs::write(&table, "# test\nE\tjoe\t100\nW\tjoe\t水\t30\nW\tjoe\t說\t20\n").unwrap();
    std::fs::write(&words, "hello\n").unwrap();
    (rows.to_str().unwrap().into(), table.to_str().unwrap().into(), words.to_str().unwrap().into())
}

#[test]
fn counts_of_a_tiny_table() {
    let (rows, table, words) = files("counts");
    let out = Command::new(env!("CARGO_BIN_EXE_shanjie-eval")).args(["--english-next", &rows, "--table", &table, "--words", &words]).output().unwrap();
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    assert!(String::from_utf8_lossy(&out.stderr).contains("skipped 1 rows"));
    let text = String::from_utf8(out.stdout).unwrap();
    let lines: Vec<Vec<&str>> = text.lines().map(|l| l.split('\t').collect()).collect();
    assert_eq!(lines.len(), 3);
    assert_eq!(lines[2][0], "rss_kib");
    assert!(lines[2][1].parse::<u64>().is_ok() && lines[2][2].parse::<u64>().is_ok());
    // english-next profile rows no_token | base i ii iii | table i ii iii | imp wor (i) | (ii) | (iii) | p95 base, table (ms)
    let want = [
        ("chat", ["3", "0", "0", "2", "1", "2", "3", "1", "2", "0", "1", "0", "1", "1"]),
        ("formal", ["3", "0", "0", "1", "1", "2", "3", "1", "2", "0", "2", "0", "1", "1"]),
    ];
    for (l, (profile, nums)) in lines[..2].iter().zip(want) {
        assert_eq!(&l[..2], ["english-next", profile]);
        // With the table: 水 and 說 are listed before any Zhuyin (i: 2 of 3 rows, today 0), and all three rows are in the row after the
        // first Zhuyin key (ii: 3). 是 loses its first place to 水 or 說 (iii: one row gained, one lost, in both profiles).
        assert_eq!(&l[2..16], nums, "{profile}");
        assert!(l[16].parse::<f64>().is_ok() && l[17].parse::<f64>().is_ok());
    }
    std::fs::remove_dir_all(std::path::Path::new(&rows).parent().unwrap()).unwrap();
}

#[test]
fn an_invalid_table_is_an_error_not_a_silent_off() {
    let (rows, table, words) = files("invalid");
    std::fs::write(&table, "E\tjoe\t100\nW\tjoe\t水\t0\n").unwrap();
    let out = Command::new(env!("CARGO_BIN_EXE_shanjie-eval")).args(["--english-next", &rows, "--table", &table, "--words", &words]).output().unwrap();
    assert!(!out.status.success());
    assert!(String::from_utf8_lossy(&out.stderr).contains("bad row at line 2"), "{}", String::from_utf8_lossy(&out.stderr));
    std::fs::remove_dir_all(std::path::Path::new(&rows).parent().unwrap()).unwrap();
}

#[test]
fn rowstats_sum_to_the_summary_and_stdout_is_unchanged() {
    let (rows, table, words) = files("rowstats");
    let stats = std::path::Path::new(&rows).with_file_name("stats.tsv");
    let run = |extra: &[&str]| {
        let o = Command::new(env!("CARGO_BIN_EXE_shanjie-eval")).args(["--english-next", &rows, "--table", &table, "--words", &words]).args(extra).output().unwrap();
        assert!(o.status.success(), "{}", String::from_utf8_lossy(&o.stderr));
        String::from_utf8(o.stdout).unwrap()
    };
    let (plain, with) = (run(&[]), run(&["--rowstats", stats.to_str().unwrap()]));
    // stdout is identical but for the timings and the memory line
    let cut = |t: &str| t.lines().take(2).map(|l| l.split('\t').take(16).collect::<Vec<_>>().join("\t")).collect::<Vec<_>>();
    assert_eq!(cut(&plain), cut(&with));
    assert_eq!(plain.lines().count(), with.lines().count());
    let text = std::fs::read_to_string(&stats).unwrap();
    for (l, profile) in with.lines().take(2).zip(["chat", "formal"]) {
        let s: Vec<Vec<u32>> = text.lines().filter(|l| l.starts_with(profile)).map(|l| l.split('\t').skip(1).map(|c| c.parse().unwrap()).collect()).collect();
        // lines 1, 3, 4 are rows, line 2 was skipped
        assert_eq!(s.iter().map(|c| c[0]).collect::<Vec<_>>(), [1, 2, 3, 4]);
        assert_eq!(s[1][1..], [1, 0, 0, 0, 0, 0, 0]);
        let sum = |k: usize| s.iter().map(|c| c[k]).sum::<u32>().to_string();
        let f: Vec<&str> = l.split('\t').collect();
        assert_eq!(sum(1).parse::<u32>().unwrap(), f[3].parse::<u32>().unwrap() + 1, "skipped_or_notoken = no_token + 1 skipped");
        for k in 0..6 {
            assert_eq!(sum(2 + k), f[4 + k], "{profile} column {k}");
        }
    }
    std::fs::remove_dir_all(std::path::Path::new(&rows).parent().unwrap()).unwrap();
}

#[test]
fn ranks_agree_with_the_hit_flags() {
    let (rows, table, words) = files("ranks");
    let (rs, rk) = (std::path::Path::new(&rows).with_file_name("stats.tsv"), std::path::Path::new(&rows).with_file_name("ranks.tsv"));
    let o = Command::new(env!("CARGO_BIN_EXE_shanjie-eval")).args(["--english-next", &rows, "--table", &table, "--words", &words, "--rowstats", rs.to_str().unwrap(), "--ranks", rk.to_str().unwrap()]).output().unwrap();
    assert!(o.status.success(), "{}", String::from_utf8_lossy(&o.stderr));
    let (stats, ranks) = (std::fs::read_to_string(&rs).unwrap(), std::fs::read_to_string(&rk).unwrap());
    let n = |l: &str| -> Vec<String> { l.split('\t').map(String::from).collect() };
    let ranks: Vec<Vec<String>> = ranks.lines().map(n).collect();
    assert_eq!(ranks.len(), 2 * 3 * 2); // profiles x parsed rows x arms
    for st in stats.lines().map(n).filter(|s| s[2] == "0") {
        for (arm, off) in [("base", 3), ("table", 6)] {
            let r = ranks.iter().find(|r| r[0] == st[0] && r[1] == st[1] && r[2] == arm).unwrap();
            for k in 0..3 {
                let rank: usize = r[3 + k].parse().unwrap();
                assert_eq!(rank > 0, st[off + k] == "1", "{r:?} vs {st:?}");
                assert!(rank <= 9);
            }
        }
    }
    // joe is followed by 水 (30) first: row 1 has w = 水, rank_i 1 with the table, 0 without
    for profile in ["chat", "formal"] {
        let r = |arm: &str| ranks.iter().find(|r| r[0] == profile && r[1] == "1" && r[2] == arm).unwrap()[3].clone();
        assert_eq!((r("table"), r("base")), ("1".to_string(), "0".to_string()), "{profile}");
    }
    std::fs::remove_dir_all(std::path::Path::new(&rows).parent().unwrap()).unwrap();
}
