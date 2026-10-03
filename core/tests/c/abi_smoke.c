/* S3a contract §7.4: compile and link shanjie.h against libcore.a. Usage: abi_smoke <data_dir>.
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

/* Send keys, freeing every output. */
static int type(ShanjieEngine *e, const char *keys) {
  for (; *keys; keys++) {
    ShanjieOutput *o = 0;
    if (shanjie_engine_key(e, ch_key(*keys), &o) != 0 || o == 0) return 0;
    shanjie_output_free(o);
  }
  return 1;
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

int main(int argc, char **argv) {
  int rc;
  if (argc != 2) return 2;
  rc = run(argv[1], 0, "su3cl3", "su3", 100);
  if (rc == 0) rc = run(argv[1], 1, "ne3hz3", "ne3", 200);
  shanjie_engine_free(0);
  shanjie_output_free(0);
  if (rc != 0) {
    printf("%d\n", rc); /* the check number only, never string contents */
    return 1;
  }
  return 0;
}
