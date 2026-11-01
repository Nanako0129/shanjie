//! `--english-next` (docs/contracts/english-next.md section 3.2) on a synthetic table and three rows, with the real models (so the pinned
//! base-arm numbers are the models' ranks; a model change moves them). Each engine is about 700 MB (the two profiles are built one after the other).
use std::process::Command;

fn files(name: &str) -> (String, String) {
    let dir = std::env::temp_dir().join(format!("shanjie-english-next-cli-{}-{name}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let (rows, table) = (dir.join("rows.txt"), dir.join("table.tsv"));
    // joe is followed by 水 (30) and 說 (20); the second line of rows is not a row and is skipped.
    std::fs::write(&rows, "joe|水|ㄕㄨㄟˇ\nnot a row\njoe|說|ㄕㄨㄛ\njoe|是|ㄕˋ\n").unwrap();
    std::fs::write(&table, "# test\nE\tjoe\t100\nW\tjoe\t水\t30\nW\tjoe\t說\t20\n").unwrap();
    (rows.to_str().unwrap().into(), table.to_str().unwrap().into())
}

#[test]
fn counts_of_a_tiny_table() {
    let (rows, table) = files("counts");
    let out = Command::new(env!("CARGO_BIN_EXE_shanjie-eval")).args(["--english-next", &rows, "--table", &table]).output().unwrap();
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
        // formal base arm i: 1 of 3 rows on the 通語 SJ-LM-6-9.1M models, 2 with the 通語 SJ-LM-7-9.7M ones (the table arm is the same: 2, 3, 1 in i, ii, iii)
        ("formal", ["3", "0", "0", "2", "1", "2", "3", "1", "2", "0", "1", "0", "1", "1"]),
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
    let (rows, table) = files("invalid");
    std::fs::write(&table, "E\tjoe\t100\nW\tjoe\t水\t0\n").unwrap();
    let out = Command::new(env!("CARGO_BIN_EXE_shanjie-eval")).args(["--english-next", &rows, "--table", &table]).output().unwrap();
    assert!(!out.status.success());
    assert!(String::from_utf8_lossy(&out.stderr).contains("bad row at line 2"), "{}", String::from_utf8_lossy(&out.stderr));
    std::fs::remove_dir_all(std::path::Path::new(&rows).parent().unwrap()).unwrap();
}
