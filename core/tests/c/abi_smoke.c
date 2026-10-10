/* S3a contract §7.4: compile and link shanjie.h against libcore.a. Usage: abi_smoke <data_dir> <lm_path> [learning_dir].
 * On failure prints only the check number (never string contents) and exits 1. */
#include "shanjie.h"

/* §7.4 allows no other include; this is the standard prototype. */
int printf(const char *, ...);

/* On failure the process exits right away, so early returns leak nothing that matters. */
#define CHECK(n, cond) do { if (!(cond)) return (n); } while (0)

static int str_eq(const char *a, const char *b) {
  if (a == 0 || b == 0) return 0;
  while (*a && *a == *b) { a++; b++; }
  return *a == *b;
}

static ShanjieKey ch_key(char c) { ShanjieKey k = {1u, (uint32_t)(unsigned char)c, 0u}; return k; }
static ShanjieKey kind_key(uint32_t kind) { ShanjieKey k = {kind, 0u, 0u}; return k; }

/* ' ' in a key string is the space bar (tone 1); everything else is a CHAR key. */
static ShanjieKey key_of(char c) { return c == ' ' ? kind_key(2u) : ch_key(c); }

/* Send keys, freeing every output. */
static int type(ShanjieEngine *e, const char *keys) {
  for (; *keys; keys++) {
    ShanjieOutput *o = 0;
    if (shanjie_engine_key(e, key_of(*keys), &o) != 0 || o == 0) return 0;
    shanjie_output_free(o);
  }
  return 1;
}

/* Send keys; returns the output of the last key (caller frees), or NULL on any failure. */
static ShanjieOutput *type_last(ShanjieEngine *e, const char *keys) {
  ShanjieOutput *o = 0;
  for (; *keys; keys++) {
    if (o) shanjie_output_free(o);
    o = 0;
    if (shanjie_engine_key(e, key_of(*keys), &o) != 0 || o == 0) return 0;
  }
  return o;
}

/* Returns 0 on success, otherwise a check number (base + n). */
static int run(const char *dir, uint32_t layout, const char *nihao, const char *ni, int base) {
  ShanjieEngine *e = 0;
  ShanjieOutput *o = 0;
  CHECK(base + 1, shanjie_engine_new(dir, layout, &e) == 0 && e != 0);
  CHECK(base + 2, type(e, nihao));
  CHECK(base + 3, shanjie_engine_key(e, kind_key(3), &o) == 0 && o != 0); /* ENTER */
  CHECK(base + 4, o->handled == 1);
  CHECK(base + 5, str_eq(o->commit, "\xe4\xbd\xa0\xe5\xa5\xbd")); /* 你好 */
  CHECK(base + 6, str_eq(o->preedit, ""));
  CHECK(base + 7, o->cursor_utf16 == 0);
  CHECK(base + 8, o->candidate_count == 0);
  CHECK(base + 9, o->candidates == 0);
  CHECK(base + 10, o->candidate_selected == -1);
  shanjie_output_free(o);
  o = 0;
  CHECK(base + 11, type(e, ni));
  CHECK(base + 12, shanjie_engine_key(e, kind_key(2), &o) == 0 && o != 0); /* SPACE */
  CHECK(base + 13, o->candidate_count >= 1 && o->candidate_count <= 9);
  CHECK(base + 14, o->candidates != 0 && o->candidates[0] != 0);
  CHECK(base + 15, o->candidate_selected == 0);
  shanjie_output_free(o);
  o = 0;
  CHECK(base + 16, shanjie_engine_reset(e, 1, &o) == 0 && o != 0);
  CHECK(base + 17, str_eq(o->commit, ""));
  CHECK(base + 18, str_eq(o->preedit, ""));
  shanjie_output_free(o);
  shanjie_engine_free(e);
  return 0;
}

/* S2c (docs/PLAN.md S2c acceptance 6), on dev302 row 226 since S2k (the word-class term makes chat pick
 * 期中報告 as formal does, so the old row 10 no longer tells the profiles apart), reading
 *   ㄒㄧㄥˋ ㄏㄠˇ ㄐㄧㄡˋ ㄏㄨˋ ㄔㄜ ㄐㄧˊ ㄕˊ ㄍㄢˇ ㄉㄠˋ
 * standard keys `vu/4cl3ru.4cj4tk_ru6g6e032l4` (_ = space bar). Expected top-1:
 *   no LM (unigram)  幸好救護車即時感到
 *   chat             幸好救護車及時趕到
 *   formal           幸好救護車即時趕到
 * (chat / formal from eval/golden/s2-lm-dev302-top1.tsv row 226, i.e. lm_eval.py --dump; unigram is what
 * an engine without LM commits, checked by 302 below.) */
#define ROW226 "vu/4cl3ru.4cj4tk ru6g6e032l4"
#define UNIGRAM "\xe5\xb9\xb8\xe5\xa5\xbd\xe6\x95\x91\xe8\xad\xb7\xe8\xbb\x8a\xe5\x8d\xb3\xe6\x99\x82\xe6\x84\x9f\xe5\x88\xb0"
#define CHAT "\xe5\xb9\xb8\xe5\xa5\xbd\xe6\x95\x91\xe8\xad\xb7\xe8\xbb\x8a\xe5\x8f\x8a\xe6\x99\x82\xe8\xb6\x95\xe5\x88\xb0"
#define FORMAL "\xe5\xb9\xb8\xe5\xa5\xbd\xe6\x95\x91\xe8\xad\xb7\xe8\xbb\x8a\xe5\x8d\xb3\xe6\x99\x82\xe8\xb6\x95\xe5\x88\xb0"

/* Press Enter; 1 when it is handled and commits `want` with an empty preedit. */
static int enter_commits(ShanjieEngine *e, const char *want) {
  ShanjieOutput *o = 0;
  int ok;
  if (shanjie_engine_key(e, kind_key(3u), &o) != 0 || o == 0) return 0;
  ok = o->handled == 1 && str_eq(o->commit, want) && str_eq(o->preedit, "");
  shanjie_output_free(o);
  return ok;
}

/* Type the row; 1 when the preedit after the last key is `want`. */
static int row_shows(ShanjieEngine *e, const char *want) {
  ShanjieOutput *o = type_last(e, ROW226);
  int ok = o != 0 && str_eq(o->preedit, want);
  shanjie_output_free(o);
  return ok;
}

/* The user report (sw contract): ㄍㄠˇ ㄨㄢˊ ㄓㄜˋ ㄅㄛ, standard keys; its two possible top-1 strings. */
#define REPORT_KEYS "el3j065k41i "
#define REPORT_ON "\xe6\x90\x9e\xe5\xae\x8c\xe9\x80\x99\xe6\xb3\xa2"  /* 搞完這波 */
#define REPORT_OFF "\xe7\x9d\xaa\xe4\xb8\xb8\xe9\x80\x99\xe6\xb3\xa2" /* 睪丸這波 */

/* Type the report and leave it composing; 1 when the preedit after the last key is `want`. */
static int report_shows(ShanjieEngine *e, const char *want) {
  ShanjieOutput *o = type_last(e, REPORT_KEYS);
  int ok = o != 0 && str_eq(o->preedit, want);
  shanjie_output_free(o);
  return ok;
}

static int run_lm(const char *dir, const char *lm, const char *missing) {
  static ShanjieOutput dummy; /* non-NULL sentinel: proves *out is overwritten */
  ShanjieEngine *e = 0;
  ShanjieOutput *o = 0;
  uint32_t mode;
  CHECK(301, shanjie_engine_new(dir, 0, &e) == 0 && e != 0);
  CHECK(302, row_shows(e, UNIGRAM) && enter_commits(e, UNIGRAM));
  CHECK(303, shanjie_engine_load_lm(e, lm) == 0);
  CHECK(304, row_shows(e, CHAT));
  CHECK(305, shanjie_engine_set_profile(e, 1, &o) == 0 && o != 0);
  CHECK(306, o->handled == 1 && str_eq(o->commit, ""));
  CHECK(307, str_eq(o->preedit, FORMAL));
  shanjie_output_free(o);
  o = 0;
  CHECK(308, enter_commits(e, FORMAL));
  /* A failed load keeps the previous LM. */
  CHECK(309, shanjie_engine_load_lm(e, missing) == 3);
  CHECK(310, row_shows(e, FORMAL) && enter_commits(e, FORMAL));
  /* Out-of-range profile: code 2, *out NULL, nothing changes. */
  o = &dummy;
  CHECK(311, shanjie_engine_set_profile(e, 7, &o) == 2 && o == 0);
  /* set_demote (docs/contracts/sw-sensitive-demote.md): 0 or 1, else 2; NULL engine or out 1; recomputes
   * like set_profile. REPORT is the user report ㄍㄠˇ ㄨㄢˊ ㄓㄜˋ ㄅㄛ (standard keys): 搞完這波 with the
   * table on (the default), 睪丸這波 with it off, so a set_demote that does nothing fails here. */
  CHECK(701, shanjie_engine_set_demote(0, 1, &o) == 1 && o == 0);
  CHECK(702, shanjie_engine_set_demote(e, 1, 0) == 1);
  o = &dummy;
  CHECK(703, shanjie_engine_set_demote(e, 2, &o) == 2 && o == 0);
  CHECK(704, report_shows(e, REPORT_ON));
  CHECK(705, shanjie_engine_set_demote(e, 0, &o) == 0 && o != 0 && str_eq(o->preedit, REPORT_OFF)); /* mid-composition */
  shanjie_output_free(o);
  o = 0;
  CHECK(706, shanjie_engine_reset(e, 1, &o) == 0 && o != 0);
  shanjie_output_free(o);
  o = 0;
  CHECK(707, report_shows(e, REPORT_OFF));
  CHECK(708, shanjie_engine_set_demote(e, 1, &o) == 0 && o != 0 && str_eq(o->preedit, REPORT_ON));
  shanjie_output_free(o);
  o = 0;
  CHECK(709, shanjie_engine_reset(e, 1, &o) == 0 && o != 0);
  shanjie_output_free(o);
  o = 0;
  /* Reset (both modes) keeps the LM and the formal profile. */
  for (mode = 0; mode <= 1; mode++) {
    int b = 312 + (int)mode * 3;
    CHECK(b, type(e, "vu/4cl3"));
    CHECK(b + 1, shanjie_engine_reset(e, mode, &o) == 0 && o != 0);
    shanjie_output_free(o);
    o = 0;
    CHECK(b + 2, row_shows(e, FORMAL) && enter_commits(e, FORMAL));
  }
  /* V3 (docs/contracts/v3-engine.md section 4): the first key shows the prediction row in the candidate
   * fields, not entered (selected -1, one row); pick selects from it; with no row pick is 2. */
  o = type_last(e, "s"); /* ㄋ */
  CHECK(801, o != 0 && o->candidate_count >= 1 && o->candidate_count <= 9 && o->candidates != 0);
  CHECK(802, o->candidate_selected == -1 && o->candidate_columns == 0 && o->candidate_first == 0);
  CHECK(803, o->candidate_total == o->candidate_count);
  shanjie_output_free(o);
  o = 0;
  CHECK(804, shanjie_engine_pick(e, 0u, &o) == 0 && o != 0 && o->handled == 1);
  CHECK(805, o->candidate_count == 0 && str_eq(o->commit, "") && !str_eq(o->preedit, ""));
  shanjie_output_free(o);
  o = &dummy;
  CHECK(806, shanjie_engine_pick(e, 0u, &o) == 2 && o == 0);
  o = 0;
  CHECK(807, shanjie_engine_reset(e, 1, &o) == 0 && o != 0);
  shanjie_output_free(o);
  o = 0;
  /* set_prediction (v3-engine section 10.5): NULL -> 1, other values -> 2 with *out NULL, 0 clears the row in the
   * snapshot, 1 brings it back for the same composition. */
  CHECK(810, shanjie_engine_set_prediction(0, 1, &o) == 1 && o == 0);
  CHECK(811, shanjie_engine_set_prediction(e, 1, 0) == 1);
  o = &dummy;
  CHECK(812, shanjie_engine_set_prediction(e, 2, &o) == 2 && o == 0);
  o = type_last(e, "s");
  CHECK(813, o != 0 && o->candidate_count >= 1);
  shanjie_output_free(o);
  o = 0;
  CHECK(814, shanjie_engine_set_prediction(e, 0, &o) == 0 && o != 0 && o->handled == 1 && o->candidate_count == 0 && o->candidate_selected == -1);
  shanjie_output_free(o);
  o = 0;
  CHECK(815, shanjie_engine_set_prediction(e, 1, &o) == 0 && o != 0 && o->candidate_count >= 1 && o->candidate_selected == -1);
  shanjie_output_free(o);
  o = 0;
  CHECK(816, shanjie_engine_reset(e, 1, &o) == 0 && o != 0);
  shanjie_output_free(o);
  /* set_candidate_vertical (candidate-vertical contract section 2.2): codes like set_prediction; candidate_vertical is the
   * struct's last field (read through the header, so the C layout is checked): 0 horizontal, 2 the vertical prediction row,
   * 1 the vertical candidate window. The window keeps its orientation when the setting changes. */
  o = 0;
  CHECK(830, shanjie_engine_set_candidate_vertical(0, 1, &o) == 1 && o == 0);
  CHECK(831, shanjie_engine_set_candidate_vertical(e, 1, 0) == 1);
  o = &dummy;
  CHECK(832, shanjie_engine_set_candidate_vertical(e, 2, &o) == 2 && o == 0);
  o = type_last(e, "s");
  CHECK(833, o != 0 && o->candidate_count >= 1 && o->candidate_selected == -1 && o->candidate_vertical == 0);
  shanjie_output_free(o);
  o = 0;
  CHECK(834, shanjie_engine_set_candidate_vertical(e, 1, &o) == 0 && o != 0 && o->candidate_count >= 1);
  CHECK(835, o->candidate_selected == -1 && o->candidate_columns == 0 && o->candidate_first == 0 && o->candidate_vertical == 2);
  shanjie_output_free(o);
  o = type_last(e, "u3 "); /* ㄋㄧˇ, then space opens the window */
  CHECK(836, o != 0 && o->candidate_selected == 0 && o->candidate_columns == 0 && o->candidate_vertical == 1);
  shanjie_output_free(o);
  o = 0;
  CHECK(837, shanjie_engine_set_candidate_vertical(e, 0, &o) == 0 && o != 0 && o->candidate_vertical == 1);
  shanjie_output_free(o);
  o = 0;
  CHECK(838, shanjie_engine_reset(e, 1, &o) == 0 && o != 0 && o->candidate_vertical == 0);
  shanjie_output_free(o);
  o = type_last(e, "s");
  CHECK(839, o != 0 && o->candidate_count >= 1 && o->candidate_vertical == 0);
  shanjie_output_free(o);
  o = 0;
  CHECK(840, shanjie_engine_reset(e, 1, &o) == 0 && o != 0);
  shanjie_output_free(o);
  /* set_abbreviation (v3-engine section 12.1): codes like set_prediction; with it on, s then t are two units (preedit ㄋㄔ), and turning it off drops them. */
  o = 0;
  CHECK(820, shanjie_engine_set_abbreviation(0, 1, &o) == 1 && o == 0);
  CHECK(821, shanjie_engine_set_abbreviation(e, 1, 0) == 1);
  o = &dummy;
  CHECK(822, shanjie_engine_set_abbreviation(e, 2, &o) == 2 && o == 0);
  CHECK(823, shanjie_engine_set_abbreviation(e, 1, &o) == 0 && o != 0);
  shanjie_output_free(o);
  o = type_last(e, "st");
  CHECK(824, o != 0 && str_eq(o->preedit, "\xe3\x84\x8b\xe3\x84\x94"));
  shanjie_output_free(o);
  o = 0;
  CHECK(825, shanjie_engine_set_abbreviation(e, 0, &o) == 0 && o != 0 && str_eq(o->preedit, ""));
  shanjie_output_free(o);
  o = 0;
  CHECK(826, shanjie_engine_reset(e, 1, &o) == 0 && o != 0);
  shanjie_output_free(o);
  shanjie_engine_free(e);
  return 0;
}

/* s3e: set_punctuation return codes; behaviour is covered by the Rust tests. */
static int run_punct(const char *dir) {
  ShanjieEngine *e = 0;
  CHECK(401, shanjie_engine_new(dir, 0, &e) == 0 && e != 0);
  CHECK(402, shanjie_engine_set_punctuation(0, "\xef\xbc\x8c\t\xe3\x80\x81\n") == 1);
  CHECK(403, shanjie_engine_set_punctuation(e, 0) == 1);
  CHECK(404, shanjie_engine_set_punctuation(e, "\xef\xbc\x8c\t\xe3\x80\x81\n") == 0); /* ，\t、 */
  CHECK(405, shanjie_engine_set_punctuation(e, "\xef\xbc\x8c\n") == 2);                  /* no alternative */
  CHECK(406, shanjie_engine_set_punctuation(e, "\xff\t\xe3\x80\x81") == 2);             /* not UTF-8 */
  shanjie_engine_free(e);
  return 0;
}

/* s3b2 section 8: expand with DOWN, then mouse pick. Fields are read through the header's struct, so
 * a reordered Rust struct fails here. */
static int run_grid(const char *dir) {
  static ShanjieOutput dummy;
  ShanjieEngine *e = 0;
  ShanjieOutput *o = 0;
  uint32_t total;
  CHECK(601, shanjie_engine_new(dir, 0, &e) == 0 && e != 0);
  CHECK(602, type(e, "su3"));
  CHECK(603, shanjie_engine_key(e, kind_key(2u), &o) == 0 && o != 0); /* SPACE opens, collapsed */
  CHECK(604, o->candidate_columns == 0 && o->candidate_first == 0 && o->candidate_count == 9);
  total = o->candidate_total;
  CHECK(605, total > 9);
  shanjie_output_free(o);
  CHECK(606, shanjie_engine_key(e, kind_key(10u), &o) == 0 && o != 0); /* DOWN expands */
  CHECK(607, o->candidate_columns == 9 && o->candidate_first == 0 && o->candidate_total == total);
  CHECK(608, o->candidate_selected == 0 && o->candidate_count == (total < 9u * 5u ? total : 9u * 5u));
  shanjie_output_free(o);
  o = &dummy;
  CHECK(609, shanjie_engine_pick(e, 1000u, &o) == 2 && o == 0);
  CHECK(610, shanjie_engine_pick(0, 0u, &o) == 1 && o == 0);
  CHECK(611, shanjie_engine_pick(e, 0u, 0) == 1);
  CHECK(612, shanjie_engine_pick(e, 1u, &o) == 0 && o != 0);
  CHECK(613, o->handled == 1 && o->candidate_count == 0 && o->candidate_columns == 0 && o->candidate_total == 0);
  CHECK(614, str_eq(o->commit, "") && !str_eq(o->preedit, ""));
  shanjie_output_free(o);
  o = &dummy;
  CHECK(615, shanjie_engine_pick(e, 0u, &o) == 2 && o == 0); /* closed */
  shanjie_engine_free(e);
  return 0;
}

/* S4: argument checks of the five learning functions. `learn_dir` (optional) is an empty, writable
 * directory for the calls that touch the learning file. */
static int run_learn(const char *dir, const char *learn_dir) {
  ShanjieEngine *e = 0;
  uint32_t flags = 7u;
  CHECK(501, shanjie_engine_new(dir, 0, &e) == 0 && e != 0);
  /* NULL engine */
  CHECK(502, shanjie_engine_set_left_context(0, "") == 1);
  CHECK(503, shanjie_engine_set_learning(0, 1u) == 1);
  CHECK(504, shanjie_engine_learning_open(0, "x") == 1);
  CHECK(505, shanjie_engine_learning_clear(0) == 1);
  CHECK(506, shanjie_engine_learning_status(0, &flags) == 1 && flags == 7u);
  /* NULL argument */
  CHECK(507, shanjie_engine_learning_open(e, 0) == 1);
  CHECK(508, shanjie_engine_learning_status(e, 0) == 1);
  /* left context: NULL and "" mean none; anything not UTF-8 is 2 */
  CHECK(509, shanjie_engine_set_left_context(e, 0) == 0);
  CHECK(510, shanjie_engine_set_left_context(e, "") == 0);
  CHECK(511, shanjie_engine_set_left_context(e, "\xe5\xa5\xbd\xe4\xbb\x96") == 0);
  CHECK(512, shanjie_engine_set_left_context(e, "\xff\xfe") == 2);
  /* learning flag: 0 and 1 only; off by default */
  CHECK(513, shanjie_engine_set_learning(e, 2u) == 2);
  CHECK(514, shanjie_engine_set_learning(e, 1u) == 0);
  CHECK(515, shanjie_engine_set_learning(e, 0u) == 0);
  /* nothing written yet */
  CHECK(516, shanjie_engine_learning_status(e, &flags) == 0 && flags == 0u);
  /* no learning_open has succeeded: clear drops memory but returns 3, never a pretend success */
  CHECK(517, shanjie_engine_learning_clear(e) == 3);
  if (learn_dir) {
    CHECK(518, shanjie_engine_learning_open(e, learn_dir) == 0);
    CHECK(519, shanjie_engine_learning_status(e, &flags) == 0 && flags == 0u);
    CHECK(520, shanjie_engine_learning_clear(e) == 0);
    CHECK(521, shanjie_engine_learning_clear(e) == 0); /* a missing file is success */
  }
  shanjie_engine_free(e);
  return 0;
}

/* acg-pack: argument checks of shanjie_engine_new_packs. The behaviour with a pack is in core/tests/engine_pack.rs. */
static int run_packs(const char *dir) {
  ShanjieEngine *e = 0;
  CHECK(701, shanjie_engine_new_packs(0, 0, 0, 0u, &e) == 1 && e == 0);
  CHECK(702, shanjie_engine_new_packs(dir, 0, 0, 1u, &e) == 1 && e == 0); /* a pack without a directory */
  CHECK(703, shanjie_engine_new_packs(dir, 0, dir, 2u, &e) == 2 && e == 0); /* a bit outside the mask */
  CHECK(704, shanjie_engine_new_packs(dir, 2u, 0, 0u, &e) == 2 && e == 0);   /* layout out of range */
  CHECK(705, shanjie_engine_new_packs(dir, 0, 0, 0u, &e) == 0 && e != 0);    /* mask 0: packs_dir may be NULL */
  shanjie_engine_free(e);
  e = 0;
  CHECK(706, shanjie_engine_new_packs(dir, 0, "/nonexistent/shanjie-packs", 1u, &e) == 0 && e != 0); /* a missing pack file adds nothing */
  shanjie_engine_free(e);
  return 0;
}

int main(int argc, char **argv) {
  int rc;
  if (argc != 3 && argc != 4) return 2;
  rc = run(argv[1], 0, "su3cl3", "su3", 100);
  if (rc == 0) rc = run(argv[1], 1, "ne3hz3", "ne3", 200);
  if (rc == 0) rc = run_lm(argv[1], argv[2], "/nonexistent/shanjie-missing.sjlm");
  if (rc == 0) rc = run_punct(argv[1]);
  if (rc == 0) rc = run_grid(argv[1]);
  if (rc == 0) rc = run_learn(argv[1], argc == 4 ? argv[3] : 0);
  if (rc == 0) rc = run_packs(argv[1]);
  shanjie_engine_free(0);
  shanjie_output_free(0);
  if (rc != 0) {
    printf("%d\n", rc); /* the check number only, never string contents */
    return 1;
  }
  return 0;
}
