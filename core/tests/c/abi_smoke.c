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

/* S2c (docs/PLAN.md S2c acceptance 6), on dev302 row 287 (row 284 until SJ-LM-7, whose chat and formal answers the new model made equal; row 226
 * since S2k, whose chat and formal answers the one-path-per-(surface, last word) decoder made equal; before that row 10, which the word-class term made
 * equal), reading
 *   ㄎㄜˇ ㄧˇ ㄉㄧㄢˋ ㄧ ㄍㄜ˙ ㄙㄢˋ ㄖㄜˋ ㄉㄧㄢˋ
 * standard keys `dk3u32u04u_ek7n04bk42u04` (_ = space bar). Expected top-1:
 *   no LM (unigram)  可以電一個散熱電
 *   chat             可以電一個散熱墊 (with data/lm/trigram-chat.sjt3, as CI and the App ship it)
 *   formal           可以墊一個散熱墊 (with data/lm/trigram.sjt3)
 * (chat / formal from eval/golden/s2-lm-dev302-top1.tsv row 287, i.e. lm_eval.py --dump; unigram is what
 * an engine without LM commits, checked by 302 below.) */
#define ROW287 "dk3u32u04u ek7n04bk42u04"
#define UNIGRAM "\xe5\x8f\xaf\xe4\xbb\xa5\xe9\x9b\xbb\xe4\xb8\x80\xe5\x80\x8b\xe6\x95\xa3\xe7\x86\xb1\xe9\x9b\xbb"
#define CHAT "\xe5\x8f\xaf\xe4\xbb\xa5\xe9\x9b\xbb\xe4\xb8\x80\xe5\x80\x8b\xe6\x95\xa3\xe7\x86\xb1\xe5\xa2\x8a"
#define FORMAL "\xe5\x8f\xaf\xe4\xbb\xa5\xe5\xa2\x8a\xe4\xb8\x80\xe5\x80\x8b\xe6\x95\xa3\xe7\x86\xb1\xe5\xa2\x8a"

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
  ShanjieOutput *o = type_last(e, ROW287);
  int ok = o != 0 && str_eq(o->preedit, want);
  shanjie_output_free(o);
  return ok;
}

/* ㄍㄠˇ ㄨㄢˊ ㄌㄜ˙ (a reading the sensitive-word table changes on the SJ-LM-7 files, as the report ㄍㄠˇ ㄨㄢˊ ㄓㄜˋ ㄅㄛ did before them), standard keys; its two possible top-1 strings. */
#define REPORT_KEYS "el3j06xk7"
#define REPORT_ON "\xe6\x90\x9e\xe5\xae\x8c\xe4\xba\x86"  /* 搞完了 */
#define REPORT_OFF "\xe7\x9d\xaa\xe4\xb8\xb8\xe4\xba\x86" /* 睪丸了 */

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
   * like set_profile. REPORT is ㄍㄠˇ ㄨㄢˊ ㄌㄜ˙ (standard keys): 搞完了 with the
   * table on (the default), 睪丸了 with it off, so a set_demote that does nothing fails here. */
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
  /* The two appended fields (zhen-inline section 1.3f) are 0 without an English run; their non-zero values are the Rust FFI test's. */
  CHECK(841, o->english_run_utf16_start == 0 && o->english_run_utf16_len == 0);
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

/* zhen-mixed (docs/contracts/zhen-mixed.md section 5.2): the codes of load_english, set_english, set_english_inline and set_english_key_order through the header. The window
 * itself is covered by core/tests/engine_english.rs and the child-process test in ffi.rs. */
static int run_english(const char *dir, const char *missing) {
  static ShanjieOutput dummy;
  ShanjieEngine *e = 0;
  ShanjieOutput *o = 0;
  CHECK(1001, shanjie_engine_new(dir, 0, &e) == 0 && e != 0);
  CHECK(1002, shanjie_engine_load_english(0, missing) == 1);
  CHECK(1003, shanjie_engine_load_english(e, 0) == 1);
  CHECK(1004, shanjie_engine_load_english(e, missing) == 3);
  CHECK(1005, shanjie_engine_set_english(0, 1, &o) == 1 && o == 0);
  CHECK(1006, shanjie_engine_set_english(e, 1, 0) == 1);
  o = &dummy;
  CHECK(1007, shanjie_engine_set_english(e, 2, &o) == 2 && o == 0);
  CHECK(1008, shanjie_engine_set_english(e, 1, &o) == 0 && o != 0 && o->handled == 1);
  shanjie_output_free(o);
  o = 0;
  CHECK(1009, shanjie_engine_set_english(e, 0, &o) == 0 && o != 0 && o->handled == 1);
  shanjie_output_free(o);
  /* zhen-inline: set_english_inline 0 and 1 give a snapshot (codes like set_english), 2 is 2, NULL engine or out is 1. */
  CHECK(1010, shanjie_engine_set_english_inline(0, 1, &o) == 1 && o == 0);
  CHECK(1011, shanjie_engine_set_english_inline(e, 1, 0) == 1);
  o = &dummy;
  CHECK(1012, shanjie_engine_set_english_inline(e, 2, &o) == 2 && o == 0);
  CHECK(1013, shanjie_engine_set_english_inline(e, 0, &o) == 0 && o != 0 && o->handled == 1);
  shanjie_output_free(o);
  o = 0;
  CHECK(1014, shanjie_engine_set_english_inline(e, 1, &o) == 0 && o != 0 && o->handled == 1);
  shanjie_output_free(o);
  /* zhen-keyorder: set_english_key_order has the codes of set_english_inline. */
  o = 0;
  CHECK(1015, shanjie_engine_set_english_key_order(0, 1, &o) == 1 && o == 0);
  CHECK(1016, shanjie_engine_set_english_key_order(e, 1, 0) == 1);
  o = &dummy;
  CHECK(1017, shanjie_engine_set_english_key_order(e, 2, &o) == 2 && o == 0);
  CHECK(1018, shanjie_engine_set_english_key_order(e, 1, &o) == 0 && o != 0 && o->handled == 1);
  shanjie_output_free(o);
  o = 0;
  CHECK(1019, shanjie_engine_set_english_key_order(e, 0, &o) == 0 && o != 0 && o->handled == 1);
  shanjie_output_free(o);
  shanjie_engine_free(e);
  return 0;
}

/* digits (docs/contracts/digits.md A.4.3): with the switch on, the model and data/lexicon/digits.tsv in the data directory, 2 0 2 6 Enter commits the
 * digits; with the switch off the same keys are the old Zhuyin keys. The behaviour itself is in core/tests/engine_digits.rs. */
static int run_digits(const char *dir, const char *lm) {
  ShanjieEngine *e = 0;
  ShanjieOutput *o = 0;
  CHECK(1101, shanjie_engine_new(dir, 0, &e) == 0 && e != 0);
  CHECK(1102, shanjie_engine_load_lm(e, lm) == 0);
  CHECK(1103, shanjie_engine_set_english(e, 1, &o) == 0 && o != 0);
  shanjie_output_free(o);
  o = 0;
  CHECK(1104, type(e, "2026"));
  CHECK(1105, shanjie_engine_key(e, kind_key(3), &o) == 0 && o != 0 && o->handled == 1 && str_eq(o->commit, "2026"));
  shanjie_output_free(o);
  o = 0;
  CHECK(1106, shanjie_engine_set_english(e, 0, &o) == 0 && o != 0);
  shanjie_output_free(o);
  o = 0;
  o = type_last(e, "2");
  CHECK(1107, o != 0 && str_eq(o->preedit, "\xe3\x84\x89")); /* ㄉ */
  shanjie_output_free(o);
  shanjie_engine_free(e);
  return 0;
}

/* fuzzy input (docs/contracts/fuzzy-input.md section 2.1): the codes of set_fuzzy through the header; the behaviour is in
 * core/tests/engine_fuzzy.rs. */
static int run_fuzzy(const char *dir) {
  static ShanjieOutput dummy;
  ShanjieEngine *e = 0;
  ShanjieOutput *o = 0;
  CHECK(1101, shanjie_engine_new(dir, 0, &e) == 0 && e != 0);
  CHECK(1102, shanjie_engine_set_fuzzy(0, 1, &o) == 1 && o == 0);
  CHECK(1103, shanjie_engine_set_fuzzy(e, 1, 0) == 1);
  o = &dummy;
  CHECK(1104, shanjie_engine_set_fuzzy(e, 32, &o) == 2 && o == 0);
  o = &dummy;
  CHECK(1105, shanjie_engine_set_fuzzy(e, 0xffffffffu, &o) == 2 && o == 0);
  CHECK(1106, shanjie_engine_set_fuzzy(e, 31, &o) == 0 && o != 0 && o->handled == 1);
  shanjie_output_free(o);
  o = 0;
  CHECK(1107, shanjie_engine_set_fuzzy(e, 0, &o) == 0 && o != 0 && o->handled == 1);
  shanjie_output_free(o);
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

/* S7c-2a (docs/contracts/s7c2a-core-rerank.md section 2.5): every new function at least once, through the header. There is no
 * .sjtl here, so a successful run / apply is covered by the child-process tests in ffi.rs; here: the NULL paths, load errors,
 * set_rerank's codes, and take on an engine without a small model (0 with *job NULL). */
static int run_rerank(const char *dir, const char *lm, const char *missing) {
  static ShanjieOutput dummy;
  static ShanjieRerankJob *job_dummy = (ShanjieRerankJob *)&dummy; /* non-NULL sentinel, never dereferenced */
  ShanjieEngine *e = 0;
  ShanjieOutput *o = 0;
  ShanjieRerankJob *j = 0;
  CHECK(901, shanjie_engine_new(dir, 0, &e) == 0 && e != 0);
  CHECK(902, shanjie_engine_load_tinylm(e, missing) == 3);         /* no n-gram model yet */
  CHECK(903, shanjie_engine_load_tinylm(0, missing) == 1);
  CHECK(904, shanjie_engine_load_tinylm(e, 0) == 1);
  CHECK(905, shanjie_engine_load_lm(e, lm) == 0);
  CHECK(906, shanjie_engine_load_tinylm(e, missing) == 3);         /* missing file */
  CHECK(907, shanjie_engine_set_rerank(0, 1, &o) == 1 && o == 0);
  CHECK(908, shanjie_engine_set_rerank(e, 1, 0) == 1);
  o = &dummy;
  CHECK(909, shanjie_engine_set_rerank(e, 2, &o) == 2 && o == 0);
  CHECK(910, shanjie_engine_set_rerank(e, 1, &o) == 0 && o != 0 && o->handled == 1);
  shanjie_output_free(o);
  o = 0;
  CHECK(911, type(e, "su3cl3"));
  j = job_dummy;
  CHECK(912, shanjie_engine_rerank_take(e, &j) == 0 && j == 0);    /* no small model: no job */
  j = job_dummy;
  CHECK(913, shanjie_engine_rerank_take(0, &j) == 1 && j == 0);
  CHECK(914, shanjie_engine_rerank_take(e, 0) == 1);
  CHECK(915, shanjie_rerank_run(0) == 1);
  o = &dummy;
  CHECK(916, shanjie_engine_rerank_apply(0, job_dummy, &o) == 1 && o == 0);
  o = &dummy;
  CHECK(917, shanjie_engine_rerank_apply(e, 0, &o) == 1 && o == 0);
  CHECK(918, shanjie_engine_rerank_apply(e, job_dummy, 0) == 1 && SHANJIE_NO_CHANGE == 5);
  shanjie_rerank_free(0);
  shanjie_engine_free(e);
  return 0;
}

/* Rerank strength (docs/contracts/rerank-strength.md section 2.2): both new functions through the header. No .sjtl here: the
 * levels themselves are covered by ffi.rs `child_rerank_level`; here the codes and the mask without a small model. */
static int run_rerank_level(const char *dir, const char *lm) {
  static ShanjieOutput dummy;
  ShanjieEngine *e = 0;
  ShanjieOutput *o = 0;
  uint32_t m = 99u;
  CHECK(951, shanjie_engine_new(dir, 0, &e) == 0 && e != 0);
  CHECK(952, shanjie_engine_rerank_levels(e, &m) == 0 && m == 0u);   /* no small model */
  CHECK(953, shanjie_engine_rerank_levels(0, &m) == 1);
  CHECK(954, shanjie_engine_rerank_levels(e, 0) == 1);
  o = &dummy;
  CHECK(955, shanjie_engine_set_rerank_level(0, 1, &o) == 1 && o == 0);
  CHECK(956, shanjie_engine_set_rerank_level(e, 1, 0) == 1);
  o = &dummy;
  CHECK(957, shanjie_engine_set_rerank_level(e, 3, &o) == 2 && o == 0);
  CHECK(958, shanjie_engine_set_rerank_level(e, 2, &o) == 0 && o != 0 && o->handled == 1); /* remembered without a model */
  shanjie_output_free(o);
  o = 0;
  CHECK(959, shanjie_engine_load_lm(e, lm) == 0);
  m = 99u;
  CHECK(960, shanjie_engine_rerank_levels(e, &m) == 0 && m == 0u);
  /* load_colloquial (docs/contracts/colloquial-term.md section 2.2): the codes; a successful load is covered by core/tests/engine_colloquial.rs. */
  CHECK(961, shanjie_engine_load_colloquial(0, "/nonexistent/shanjie-missing.sjlm") == 1);
  CHECK(962, shanjie_engine_load_colloquial(e, 0) == 1);
  CHECK(963, shanjie_engine_load_colloquial(e, "/nonexistent/shanjie-missing.sjlm") == 3);
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
  if (rc == 0) rc = run_rerank(argv[1], argv[2], "/nonexistent/shanjie-missing.sjtl");
  if (rc == 0) rc = run_rerank_level(argv[1], argv[2]);
  if (rc == 0) rc = run_english(argv[1], "/nonexistent/shanjie-missing-english.txt");
  if (rc == 0) rc = run_digits(argv[1], argv[2]);
  if (rc == 0) rc = run_fuzzy(argv[1]);
  shanjie_engine_free(0);
  shanjie_output_free(0);
  if (rc != 0) {
    printf("%d\n", rc); /* the check number only, never string contents */
    return 1;
  }
  return 0;
}
