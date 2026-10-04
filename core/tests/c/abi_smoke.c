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

/* S2c (docs/PLAN.md S2c acceptance 6): dev302 row 10, reading
 *   ㄑㄧˊ ㄓㄨㄥ ㄅㄠˋ ㄍㄠˋ ㄇㄧㄥˊ ㄊㄧㄢ ㄧㄠˋ ㄐㄧㄠ
 * standard keys `fu6 5j/_ 1l4 el4 au/6 wu0_ ul4 rul_` (_ = space bar). Expected top-1:
 *   no LM (unigram)  其中報告明天要教
 *   chat             其中報告明天要交
 *   formal           期中報告明天要交
 * (chat / formal from eval/golden/s2-lm-dev302-top1.tsv row 10, i.e. lm_eval.py --dump; unigram is what
 * an engine without LM commits, checked by 302 below.) */
#define ROW10 "fu65j/ 1l4el4au/6wu0 ul4rul "
#define UNIGRAM "\xe5\x85\xb6\xe4\xb8\xad\xe5\xa0\xb1\xe5\x91\x8a\xe6\x98\x8e\xe5\xa4\xa9\xe8\xa6\x81\xe6\x95\x99"
#define CHAT "\xe5\x85\xb6\xe4\xb8\xad\xe5\xa0\xb1\xe5\x91\x8a\xe6\x98\x8e\xe5\xa4\xa9\xe8\xa6\x81\xe4\xba\xa4"
#define FORMAL "\xe6\x9c\x9f\xe4\xb8\xad\xe5\xa0\xb1\xe5\x91\x8a\xe6\x98\x8e\xe5\xa4\xa9\xe8\xa6\x81\xe4\xba\xa4"

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
  ShanjieOutput *o = type_last(e, ROW10);
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
  /* Reset (both modes) keeps the LM and the formal profile. */
  for (mode = 0; mode <= 1; mode++) {
    int b = 312 + (int)mode * 3;
    CHECK(b, type(e, "fu65j/ "));
    CHECK(b + 1, shanjie_engine_reset(e, mode, &o) == 0 && o != 0);
    shanjie_output_free(o);
    o = 0;
    CHECK(b + 2, row_shows(e, FORMAL) && enter_commits(e, FORMAL));
  }
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
  /* without a file, clear only drops memory */
  CHECK(517, shanjie_engine_learning_clear(e) == 0);
  if (learn_dir) {
    CHECK(518, shanjie_engine_learning_open(e, learn_dir) == 0);
    CHECK(519, shanjie_engine_learning_status(e, &flags) == 0 && flags == 0u);
    CHECK(520, shanjie_engine_learning_clear(e) == 0);
    CHECK(521, shanjie_engine_learning_clear(e) == 0); /* a missing file is success */
  }
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
  if (rc == 0) rc = run_learn(argv[1], argc == 4 ? argv[3] : 0);
  shanjie_engine_free(0);
  shanjie_output_free(0);
  if (rc != 0) {
    printf("%d\n", rc); /* the check number only, never string contents */
    return 1;
  }
  return 0;
}
