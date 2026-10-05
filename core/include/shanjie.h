/*
 * shanjie core C ABI (docs/contracts/s3a.md §6). Implemented in core/src/ffi.rs; link libcore.a.
 *
 * Notes for the Swift shell (S3b):
 *  - Output contents are user input. Never pass them to NSLog, print, debugPrint, dump, os_log or any
 *    other logging; never interpolate input text into fatalError / precondition / assert messages.
 *  - Copy every field you need into Swift `String`s in the same call that received the output, then
 *    call shanjie_output_free immediately. Do not keep ShanjieOutput pointers around.
 *  - A handle is not thread-safe: make every call on one thread (the IMK main thread).
 *  - Any non-zero return: treat the key as not handled (pass it through) and do not record it.
 *
 * Return codes: 0 success, 1 a required pointer is NULL, 2 invalid input (data_dir or LM path not
 * UTF-8, ch not a Unicode scalar, kind / layout / mode / profile out of range), 3 data load failed
 * (including an unreadable or malformed LM file), 4 internal error (caught panic or decode/encode
 * error; the engine has already been reset in discard mode). shanjie_engine_load_lm failing leaves the
 * LM state as it was (no LM, or the previously loaded one).
 *
 * Memory and lifetime:
 *  - On a non-zero return, *out is set to NULL (when out itself is non-NULL) and nothing is allocated.
 *  - shanjie_engine_free(NULL) and shanjie_output_free(NULL) do nothing. Freeing twice, or freeing a
 *    pointer this library did not return, is undefined behaviour.
 *  - A ShanjieOutput owns copies of all its strings and of the candidates array; they stay valid
 *    until shanjie_output_free, regardless of later engine_key / engine_reset / engine_set_profile /
 *    engine_load_lm / engine_free calls.
 *
 * Language model (S2c):
 *  - The default profile is chat. The shell picks the profile from the frontmost app (S3b); the core
 *    keeps no app identity. A profile set before any LM is loaded is remembered and applies once loaded.
 *  - Load the LM once at startup while the composition is empty: loading does not recompute the
 *    current display; the next change to the composition decodes with the new model.
 *  - shanjie_engine_reset (both modes) and the automatic reset after code 4 clear the composition only;
 *    the loaded LM and the current profile are kept.
 *  - The logging rules above apply to set_profile snapshots too; never log the LM path either.
 */
#ifndef SHANJIE_H
#define SHANJIE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct { uint32_t kind; uint32_t ch; uint32_t modifiers; } ShanjieKey;
// kind: 1 CHAR, 2 SPACE, 3 ENTER, 4 BACKSPACE, 5 DELETE, 6 ESC, 7 LEFT, 8 RIGHT, 9 UP, 10 DOWN, 11 HOME, 12 END, 13 TAB
// ch: Unicode scalar of the keycap without Shift when kind is CHAR; ignored for other kinds
// modifiers: bit0 SHIFT, bit1 CONTROL, bit2 OPTION, bit3 COMMAND, bit4 CAPSLOCK
typedef struct {
  int32_t handled;            // 1 = engine handled it, do not forward; 0 = pass through (insert commit first, then let the key go)
  const char *commit;         // UTF-8 text to insert now; may be "", never NULL
  const char *preedit;        // UTF-8 composition display (pending Zhuyin inserted at the cursor); never NULL
  uint32_t cursor_utf16;      // cursor in preedit, in UTF-16 code units (for NSRange); after the pending syllable
  uint32_t candidate_count;   // candidates on the current page (0-9)
  const char *const *candidates; // NULL when candidate_count is 0
  int32_t candidate_selected; // selection within the page; -1 when candidates are closed
} ShanjieOutput;
typedef struct ShanjieEngine ShanjieEngine;

int32_t shanjie_engine_new(const char *data_dir, uint32_t layout, ShanjieEngine **out); // layout 0 standard, 1 ETen
void    shanjie_engine_free(ShanjieEngine *engine);
int32_t shanjie_engine_key(ShanjieEngine *engine, ShanjieKey key, ShanjieOutput **out);
int32_t shanjie_engine_reset(ShanjieEngine *engine, uint32_t mode, ShanjieOutput **out); // mode 0 commit then clear, 1 discard
void    shanjie_output_free(ShanjieOutput *output);
// S2c (docs/PLAN.md S2c)
int32_t shanjie_engine_load_lm(ShanjieEngine *engine, const char *path);               // does not change the current display
int32_t shanjie_engine_set_profile(ShanjieEngine *engine, uint32_t profile, ShanjieOutput **out); // 0 chat (default), 1 formal; recomputes and returns a snapshot (handled 1, commit "")
// s3e (docs/contracts/s3e-punctuation-candidates.md): punctuation alternatives, UTF-8 lines
// "mark\talt\talt...", blank lines ignored, a repeated mark overrides; at most 64 KB / 1,000 lines.
// 2 on any invalid input, keeping the previous table (a built-in default until the first success).
int32_t shanjie_engine_set_punctuation(ShanjieEngine *engine, const char *table); // does not change the current display
// S4 (docs/contracts/s4-learning.md): learning from candidate-window re-picks. Every function returns 1
// when engine (or a required pointer) is NULL. None of them changes the current display, except that
// learning_clear and a forget may re-decode on the next key.
// set_left_context: copies the text before the insertion point; only its last <= 2 consecutive Han
//   characters are kept (S4 section 1.1). NULL or "" means none. Not UTF-8: returns 2 and clears.
//   Cleared by the core after every commit, reset and code 4; call it at every composition start.
int32_t shanjie_engine_set_left_context(ShanjieEngine *engine, const char *utf8);
// set_learning: 0 or 1 (2 otherwise). Default 0 at engine creation (fail-closed). 0 drops pending
//   learns; a span is learned only if the flag was 1 both when it was chosen and at commit.
int32_t shanjie_engine_set_learning(ShanjieEngine *engine, uint32_t enabled);
// learning_open: dir is the learning directory (.../Application Support/shanjie); created 0700 if
//   missing. A missing file starts empty; a corrupt one is renamed learning.tsv.corrupt and starts
//   empty (both 0). 1 when engine or dir is NULL; 2 when dir is not UTF-8; 3 on I/O failure or a
//   directory not owned by the user.
int32_t shanjie_engine_learning_open(ShanjieEngine *engine, const char *dir);
// Forgetting: KEY with COMMAND (bit3) and kind BACKSPACE (4) while candidates are open removes the
//   highlighted word's learned records for that reading (all contexts) and re-decodes; the output
//   shows the new composition with the candidates still open. Without candidates the key passes through.
//   After a successful learning_open a forget ALWAYS rewrites the whole learning file, even when
//   memory held no record of the word (a record pruned on load can still be in the file); before any
//   successful learning_open it changes memory only. A failed rewrite sets status bit0.
// learning_clear: drops memory, pending learns and the files (a missing file is success); 3 on failure.
//   Also 3 when no learning_open has succeeded on this engine (no file it could have deleted); memory
//   and pending learns are dropped anyway.
int32_t shanjie_engine_learning_clear(ShanjieEngine *engine);
// A single-character pick is learned, and a single-character record looked up, only under a full
//   context key made of Han characters: the last <= 2 Han characters before the word, taken from the
//   composition text before it AND the left context from set_left_context. When no Han character
//   precedes the word (sentence start, after punctuation or ASCII) the key is "^" and a single
//   character is neither learned nor looked up. With a NULL left context, or a paused gate that makes
//   the shell pass NULL, keys that follow Han characters typed in the same composition still teach
//   (if learning is on) and look up. set_learning(0) stops teaching only; it does not stop lookup.
//   Words of 2+ characters are unaffected (docs/contracts/s4-learning.md section 12).
// Learning happens only at a commit (Enter, a key the engine passes through after committing, or the
//   40-syllable auto-commit), never on shanjie_engine_reset or Esc, and never for punctuation picks.
// Writes: a learning commit appends only the records it changed; a full rewrite happens on a forget,
//   on the first write after learning_open or a clear, on the first write of each day, every 1,024
//   appended lines, after any failed append, and after a failed full rewrite or forget.
// learning_status: *flags bit0 = the last FULL REWRITE of the learning file failed (an append that
//   fails falls back to a full rewrite, so it counts only through that rewrite). Set by a failed full
//   rewrite; cleared only by a successful full rewrite or a successful learning_clear.
int32_t shanjie_engine_learning_status(ShanjieEngine *engine, uint32_t *flags);

#ifdef __cplusplus
}
#endif

#endif /* SHANJIE_H */
