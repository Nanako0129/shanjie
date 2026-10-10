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
 * (including an unreadable or malformed LM file, and since S2k a missing or mismatching classes.sjc beside
 * it), 4 internal error (caught panic or decode/encode
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
 *  - shanjie_engine_load_lm needs classes.sjc (the word-class term, docs/contracts/s2k-word-classes.md) in
 *    the same directory as the model file, built for that model; without it the load fails with 3.
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
// kind: 1 CHAR, 2 SPACE, 3 ENTER, 4 BACKSPACE, 5 DELETE, 6 ESC, 7 LEFT, 8 RIGHT, 9 UP, 10 DOWN, 11 HOME, 12 END, 13 TAB, 14 PAGE_UP, 15 PAGE_DOWN
// PAGE_UP / PAGE_DOWN (candidate-vertical contract section 2.2): the shell sends them only while the last output had
//   candidate_vertical 1 and no modifier is held. They move the vertical window by nine. With any other state of the
//   engine (no vertical window open, or a modifier) they are not handled (handled 0), change nothing and commit nothing.
// ch: Unicode scalar of the keycap without Shift when kind is CHAR; ignored for other kinds
// modifiers: bit0 SHIFT, bit1 CONTROL, bit2 OPTION, bit3 COMMAND, bit4 CAPSLOCK
typedef struct {
  int32_t handled;            // 1 = engine handled it, do not forward; 0 = pass through (insert commit first, then let the key go)
  const char *commit;         // UTF-8 text to insert now; may be "", never NULL
  const char *preedit;        // UTF-8 composition display (pending Zhuyin inserted at the cursor); never NULL
  uint32_t cursor_utf16;      // cursor in preedit, in UTF-16 code units (for NSRange); after the pending syllable
  uint32_t candidate_count;   // candidates in this output: collapsed one page (0-9), expanded the visible rows (up to 5 x candidate_columns); also the prediction row (1-9, see candidate_selected)
  const char *const *candidates; // NULL when candidate_count is 0
  int32_t candidate_selected; // selection within this output's candidates; -1 when candidates are closed. -1 with candidate_count > 0 is the V3 prediction row, not entered (no number, no highlight; columns 0, first 0, total = count); >= 0 is the candidate window or the entered prediction row
  uint32_t candidate_columns; // 0 = collapsed single row; > 0 = expanded, always 9 (one row = one page, the selected page on top; s3b2 9)
  uint32_t candidate_first;   // position of candidates[0] in the whole list; 0 when closed (and for the prediction row)
  uint32_t candidate_total;   // length of the whole list; 0 when closed (the prediction row: its length)
  uint32_t candidate_vertical; // 1 = this output is a vertical candidate window (candidate_columns is then 0; the window shows up to 9 rows from candidate_first); 0 for the horizontal window, the prediction row and no candidates. Appended last.
} ShanjieOutput;
typedef struct ShanjieEngine ShanjieEngine;

int32_t shanjie_engine_new(const char *data_dir, uint32_t layout, ShanjieEngine **out); // layout 0 standard, 1 ETen
#define SHANJIE_PACK_ACG 1u   // bit0 of the packs mask below
// acg-pack (docs/contracts/acg-pack.md A.2): word packs. packs is a bit mask (SHANJIE_PACK_ACG = ACG, packs_dir/acg-add.tsv);
//   the rows are parsed into the lexicon, so the set is fixed for the engine's life: the shell turns a pack on or
//   off by freeing the engine and creating another, like a layout change. packs 0 ignores packs_dir (may be NULL)
//   and is exactly shanjie_engine_new; a missing pack file contributes nothing (same engine as without it).
//   2 for a bit outside the mask or a non-UTF-8 data_dir or packs_dir, 1 for a NULL packs_dir with a non-zero mask, 3 when a pack file exists but cannot
//   be read or parsed. The pack's words are capped like overlay-add.tsv's when the LM is loaded.
int32_t shanjie_engine_new_packs(const char *data_dir, uint32_t layout, const char *packs_dir, uint32_t packs, ShanjieEngine **out);
void    shanjie_engine_free(ShanjieEngine *engine);
int32_t shanjie_engine_key(ShanjieEngine *engine, ShanjieKey key, ShanjieOutput **out);
// ENTER (docs/contracts/s3a.md section 3; docs/contracts/enter-pending.md): with the candidate window open or the
//   prediction row entered, ENTER and SHIFT+ENTER select (rules 6, 1d). Otherwise ENTER commits the composition as
//   shown, unfinished zhuyin symbols included (rules 12a, 19), and SHIFT+ENTER commits the same and then passes the
//   key on (handled = 0, commit non-empty; rules 12b, 19a), so the app gets its line break. COMMAND, OPTION and
//   CONTROL+ENTER pass through unchanged (rule 1).
// s3b2 (docs/contracts/s3b2-glass-panel.md section 8): mouse pick. index is a position in the last
//   output's candidates; the core chooses candidate_first + index through the same path as ENTER, so
//   learning behaves identically. V3 (docs/contracts/v3-engine.md section 4): with the candidate window
//   closed and a prediction row showing (entered or not), pick(index) selects that row's item (index
//   counts from 0 in the row; the click is an explicit choice). Section 10: the pick is learned like a
//   candidate-window re-pick (nothing displaced) at the commit, when learning is on at the pick and at the commit.
//   1 when engine or out is NULL; 2 when there is neither a candidate window nor a prediction row, or
//   index is outside that output (state unchanged); 4 internal (engine reset). *out is NULL on any error.
int32_t shanjie_engine_pick(ShanjieEngine *engine, uint32_t index, ShanjieOutput **out);
int32_t shanjie_engine_reset(ShanjieEngine *engine, uint32_t mode, ShanjieOutput **out); // mode 0 commit what is shown (unfinished zhuyin included, like ENTER) then clear, 1 discard
void    shanjie_output_free(ShanjieOutput *output);
// S2c (docs/PLAN.md S2c)
int32_t shanjie_engine_load_lm(ShanjieEngine *engine, const char *path);               // does not change the current display
int32_t shanjie_engine_set_profile(ShanjieEngine *engine, uint32_t profile, ShanjieOutput **out); // 0 chat (default), 1 formal; recomputes and returns a snapshot (handled 1, commit "")
// sw (docs/contracts/sw-sensitive-demote.md): set_demote 0 or 1 (2 otherwise, state unchanged), like
//   set_profile in its codes and its snapshot: it recomputes the current composition and returns it
//   (handled 1, commit ""), so a toggle in the menu shows at once; 1 when engine or out is NULL.
//   Default 1 at engine creation. With 1, the entries of data_dir/demote.tsv are subtracted from the
//   matching word's score in the bigram decode and in the total of a composition with fixed words.
//   demote.tsv is required like the other data files: shanjie_engine_new returns 3 when it is missing,
//   malformed, or has a row naming no entry of the lexicon (a reading or word the lexicon lacks).
//   0 treats every delta as 0, bit-identical to a lexicon without the file. No effect without a loaded
//   model. The shell wires it to the "avoid ranking sensitive words first" menu item.
int32_t shanjie_engine_set_demote(ShanjieEngine *engine, uint32_t enabled, ShanjieOutput **out); // 0 or 1; recomputes and returns a snapshot (handled 1, commit "")
// V3 (docs/contracts/v3-engine.md section 10.5): set_prediction 0 or 1 (2 otherwise, state unchanged), like
//   set_demote in its codes and its snapshot (handled 1, commit ""); 1 when engine or out is NULL. Default 1.
//   0 clears the prediction row (an entered row is left) and computes none; 1 recomputes it, so a row that
//   fits the display conditions shows in the returned snapshot. No effect without a loaded model.
int32_t shanjie_engine_set_prediction(ShanjieEngine *engine, uint32_t enabled, ShanjieOutput **out); // 0 or 1; returns a snapshot
// candidate-vertical (docs/contracts/candidate-vertical.md section 2.2): set_candidate_vertical 0 or 1 (2 otherwise, state
//   unchanged), default 0, like set_prediction in its codes and its snapshot (handled 1, commit ""); 1 when engine or out is
//   NULL. It sets the orientation of the candidate windows that open from now on (the Space/Down that opens the window,
//   and the punctuation window). A window that is already open (including an expanded grid) keeps the orientation it
//   opened with, so the snapshot equals the output before the call. Call it after every engine creation, like set_prediction.
int32_t shanjie_engine_set_candidate_vertical(ShanjieEngine *engine, uint32_t enabled, ShanjieOutput **out); // 0 or 1; returns a snapshot
// V3 (docs/contracts/v3-engine.md section 12): set_abbreviation 0 or 1 (2 otherwise, state unchanged), default 0, like
//   set_prediction in its codes and its snapshot (handled 1, commit ""); 1 when engine or out is NULL. With 1 (and the
//   prediction row on and the cursor at the end of the composition) a zhuyin key whose column already holds a symbol
//   opens a new unfinished unit instead of replacing it, so ㄋㄔ is two units, and the prediction row reads them as
//   the initials of a word's syllables (奶茶). With two or more units a tone key and space do nothing, Enter sends the
//   shown symbols, Esc drops all units (the composition, fixed words and cursor stay) and Backspace takes the last symbol.
//   Turning it off, or the prediction row off, while there are two or more units drops them all (as Esc) and returns
//   that state; with one unit nothing changes. Turning it on changes nothing. No effect without a loaded model.
int32_t shanjie_engine_set_abbreviation(ShanjieEngine *engine, uint32_t enabled, ShanjieOutput **out); // 0 or 1; returns a snapshot
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
//   S2h: also the condition of the first word in the bigram model (its last 2 characters, else the last
//   one, whichever has bigram history; none: the sentence start).
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
//   shows the new composition with the candidates still open. Without candidates the key passes through,
//   except while the V3 prediction row is entered: there it forgets the selected item the same way (and drops a
//   pending learn of it), recomputes the row, and the row stays entered with the selection on that word.
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
