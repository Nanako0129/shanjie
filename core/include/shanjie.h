/*
 * shanjie core C ABI (docs/contracts/s3a.md §6). Implemented in core/src/ffi.rs; link libcore.a.
 *
 * Notes for the Swift shell (S3b):
 *  - Output contents are user input. Never pass them to NSLog, print, debugPrint, dump, os_log or any
 *    other logging; never interpolate input text into fatalError / precondition / assert messages.
 *  - Copy every field you need into Swift `String`s in the same call that received the output, then
 *    call shanjie_output_free immediately. Do not keep ShanjieOutput pointers around.
 *  - A handle is not thread-safe: make every call on one thread (the IMK main thread). The exceptions are
 *    shanjie_rerank_run and shanjie_rerank_free, which take no engine (S7c-2a, below).
 *  - Any non-zero return of shanjie_engine_key / shanjie_engine_pick: treat the key as not handled (pass it
 *    through) and do not record it. The non-zero codes of shanjie_engine_rerank_take, shanjie_rerank_run and
 *    shanjie_engine_rerank_apply do NOT mean pass-through: they have no key.
 *
 * Return codes: 0 success, 1 a required pointer is NULL, 2 invalid input (data_dir or LM path not
 * UTF-8, ch not a Unicode scalar, kind / layout / mode / profile / rerank level out of range), 3 data load failed
 * (including an unreadable or malformed LM file, and since S2k a missing or mismatching classes.sjc beside
 * it), 4 internal error (caught panic or decode/encode
 * error; the engine has already been reset in discard mode; exception: shanjie_rerank_run has no engine and
 * resets nothing), 5 SHANJIE_NO_CHANGE (shanjie_engine_rerank_apply only). shanjie_engine_load_lm failing leaves the
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
 *  - shanjie_engine_load_lm also reads an optional kn.sjkn (Kneser-Ney side file, docs/contracts/kn-core.md) in
 *    the same directory. No such file: nothing changes. A file that exists but cannot be read, or does not
 *    match the model (magic, size, SHA-256, header values), fails the load with 3, never silently ignored.
 *    When it is present and valid it changes the scoring of every vocabulary word. The shipped app has one (kn-v1,
 *    docs/contracts/model-v6.md).
 *  - It reads an optional trigram.sjt3 (docs/contracts/trigram-core.md) the same way (a bad one fails the load with 3).
 *    A valid one is used by the formal profile only, which then scores without kn.sjkn.
 *    The file is memory-mapped (docs/contracts/trigram-mmap.md): while an engine has it loaded it must not be truncated or
 *    rewritten in place (a truncated page is a SIGBUS); replace it by rename, as the install paths do.
 *  - data_dir must hold names-add.tsv (the always-on common proper nouns, model-v6 section 2.5) next to overlay-add.tsv and
 *    sandhi-add.tsv: shanjie_engine_new / _new_packs read it into the lexicon and shanjie_engine_load_lm reads it again to cap
 *    its words like overlay-add.tsv's; missing or unreadable, either call fails with 3.
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
  int32_t candidate_selected; // selection within this output's candidates; -1 when candidates are closed. -1 with candidate_count > 0 is the V3 prediction row, not entered (no highlight; the shell numbers only the first cell, with the Tab glyph, predict-down B.3.2; columns 0, first 0, total = count); >= 0 is the candidate window or the entered prediction row
  uint32_t candidate_columns; // 0 = collapsed single row; > 0 = expanded, always 9 (one row = one page, the selected page on top; s3b2 9)
  uint32_t candidate_first;   // position of candidates[0] in the whole list; 0 when closed (and for the prediction row)
  uint32_t candidate_total;   // length of the whole list; 0 when closed (the prediction row: its length)
  uint32_t candidate_vertical; // 0 = horizontal, or no candidates; 1 = vertical candidate window (candidate_columns is then 0; up to 9 rows from candidate_first; fixed when the window opened); 2 = vertical prediction row, entered or not (follows the current setting on every output; candidate_columns 0, first 0). Appended last.
  uint32_t english_run_utf16_start; // UTF-16 offset in preedit where the open English run starts (zhen-inline section 1.3f); 0 when no run is open. Appended last.
  uint32_t english_run_utf16_len;   // UTF-16 length of the open English run in preedit; 0 when no run is open. Appended last.
} ShanjieOutput;
typedef struct ShanjieEngine ShanjieEngine;

int32_t shanjie_engine_new(const char *data_dir, uint32_t layout, ShanjieEngine **out); // layout 0 standard, 1 ETen
#define SHANJIE_PACK_ACG 1u   // bit0 of the packs mask below
#define SHANJIE_PACK_IT  4u   // bit2 (value 4; value 2 is reserved for the quotes pack): the IT+AI pack, docs/contracts/it-pack.md section 3.1
// acg-pack (docs/contracts/acg-pack.md A.2): word packs. packs is a bit mask (SHANJIE_PACK_ACG = ACG, packs_dir/acg-add.tsv;
//   SHANJIE_PACK_IT = IT+AI, packs_dir/it-add.tsv, appended after the anime pack's rows; the bits combine);
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
//   learning behaves identically (a candidate pick also keeps the words outside its span as they were shown:
//   docs/contracts/repick-freeze.md). V3 (docs/contracts/v3-engine.md section 4): with the candidate window
//   closed and a prediction row showing (entered or not), pick(index) selects that row's item (index
//   counts from 0 in the row; the click is an explicit choice). Section 10: the pick is learned like a
//   candidate-window re-pick (nothing displaced) at the commit, when learning is on at the pick and at the commit.
//   Predict-down (docs/contracts/predict-down.md B.3.1): a window opened while a passive prediction row was showing lists
//   the row's items first, in the row's order; picking one of them (or its digit, or ENTER on it) is picking it from the
//   row, the same as the call above, then the window closes. One whose reading is exactly the typed syllables before the
//   cursor is the lexicon's candidate for that span, and picking it is a re-pick (B.3.1 revision of 2026-10-17). Any
//   prediction pick, from the row or the window, keeps the words left of its span as shown (repick-freeze section 13).
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
//   opened with, so the snapshot equals the output before the call. The prediction row has no opening: it follows the
//   current setting on every output, so a row on screen is re-rendered in the new orientation in the snapshot (value 2
//   when vertical). In the entered vertical row Up / Down move the selection (stopping at both ends). Call it after every engine creation, like set_prediction.
int32_t shanjie_engine_set_candidate_vertical(ShanjieEngine *engine, uint32_t enabled, ShanjieOutput **out); // 0 or 1; returns a snapshot
// V3 (docs/contracts/v3-engine.md section 12): set_abbreviation 0 or 1 (2 otherwise, state unchanged), default 0, like
//   set_prediction in its codes and its snapshot (handled 1, commit "" unless turning it on ends an English run, below); 1 when engine or out is NULL. With 1 (and the
//   prediction row on and the cursor at the end of the composition) a zhuyin key whose column already holds a symbol
//   opens a new unfinished unit instead of replacing it, so ㄋㄔ is two units, and the prediction row reads them as
//   the initials of a word's syllables (奶茶). With two or more units a tone key and space do nothing, Enter sends the
//   shown symbols, Esc drops all units (the composition, fixed words and cursor stay) and Backspace takes the last symbol.
//   Turning it off, or the prediction row off, while there are two or more units drops them all (as Esc) and returns
//   that state; with one unit nothing changes. Turning it on closes an open English window and ends an open English run
//   (see set_english) as a token; if that is the 128th unit the snapshot carries the composition in `commit`.
//   No effect without a loaded model.
int32_t shanjie_engine_set_abbreviation(ShanjieEngine *engine, uint32_t enabled, ShanjieOutput **out); // 0 or 1; returns a snapshot
// zhen-mixed (docs/contracts/zhen-mixed.md): the English word list. 0 ok; 1 engine or path NULL; 2 path not UTF-8; 3 unreadable,
//   not a regular file, over 4 MiB or 200,000 lines, a line outside [a-z]{3,32}, a duplicate line, a BOM. Failure keeps the
//   previous list (or none). Does not change the switch or the current display. Call it once after shanjie_engine_new.
int32_t shanjie_engine_load_english(ShanjieEngine *engine, const char *path);
// set_english 0 or 1 (2 otherwise, state unchanged), default 0 at engine creation, like set_abbreviation in its codes and its
//   snapshot (handled 1; commit "" except as noted for turning it off, below); 1 when engine or out is NULL. With 1, and only with a loaded list, the standard layout and
//   the abbreviation composer off, Space after an English word typed into one unfinished syllable (the keys typed are a listed
//   word and at least one key replaced a symbol in an occupied column; two when the leftover syllable with tone 1 is itself in
//   the lexicon) opens the English window (s3a rule 10e): the word, Capitalised, UPPER and, when the leftover syllable is in
//   the lexicon, its first entry. 1-9, Enter or pick(index) on an English item does not commit (docs/contracts/zhen-inline.md):
//   the item becomes an English token at the cursor that stays in the composition (one unit like a punctuation mark: the arrow
//   keys take it whole, the next word starts a new sentence; Backspace takes one letter, see below), and it is sent with the rest. The Chinese item
//   becomes a fixed word. No effect without a list. Call it after every engine creation, like set_abbreviation.
//   Key handling that comes with it (docs/contracts/zhen-inline.md sections 1.3, 1.3b, 1.3c, 1.3e, 1.3f):
//   - Auto-keep: a Zhuyin key that cannot continue the letters on screen (a whole listed word) keeps the word as an English
//     token and starts the next syllable; a Zhuyin key with the English window open first keeps the selected item.
//   - Backspace deletes English one letter at a time (section 1.3g): with the letters on screen it takes the last letter and the
//     rest becomes an English run at the cursor (no replay, no Zhuyin); with the cursor right after an English token (no
//     unfinished syllable, no window, no run) it removes the token and opens a run with its text less the last letter (a
//     one-letter token just goes). Space units and punctuation tokens still go whole.
//   - Shift+letter starts an English run (an English token in the making, not a unit until it ends): letters go on it as
//     typed, shifted or not, and the run is never auto-kept; Space ends it (the space is eaten), Backspace and Esc take
//     letters off, every other key ends it first. While a run is open the output's english_run_utf16_start and
//     english_run_utf16_len mark it in preedit (0 and 0 otherwise). Without set_english the Shift rules (s3a rules 13, 21,
//     22) are unchanged.
//   - With the cursor right after an English token or a space unit, no unfinished syllable and no window open, Space inserts
//     a half-width space as one unit (like a punctuation mark): the first Space after a run ends it, the second types a space.
//     With the cursor at 0 in front of one of them Space opens nothing; a space unit has no list at all (Down opens nothing).
//     A space unit is learned under the same neighbour key as an English token (`^>_punct_en`).
//   - With the cursor right after an English token and no unfinished syllable, only Down lists its writings (as typed, lower
//     case, Capitalised, UPPER); a token made by an auto-keep adds a Chinese item, so a wrong keep can be undone.
//   - An English token or space unit (from an item, an auto-keep, a run or Space) that makes the 128th unit sends the whole
//     composition in `commit` of that call's output (the key that did it, the pick, or the settings call), never in a later one.
//   Turning it off, like turning the abbreviation composer on, closes the window and ends an open English run as a token
//   (a run that makes the 128th unit sends the composition in the snapshot's `commit`).
//   Digits (docs/contracts/digits.md): with it on (standard layout, abbreviation composer off, a model and `digits.tsv` in the data
//   directory; the table is optional, without it nothing below happens) the keys 0-9 . / - type half-width digit-string tokens
//   into the composition: each key is scored as digit or Zhuyin from the text before it, a run keeps its last four keys unsettled,
//   and a Zhuyin key outside the alphabet, a tone key or Space settles the syllable when it wins. DOWN, ENTER and SHIFT+ENTER end
//   the run as the screen shows it (digits.md A.12: the keys shown as digits become tokens, the keys shown as Zhuyin stay an
//   unfinished syllable); every other key first turns the unsettled keys into digits. Turning it off (or the abbreviation composer on) does that too; the snapshot's `commit` may carry
//   the 128-unit composition.
int32_t shanjie_engine_set_english(ShanjieEngine *engine, uint32_t enabled, ShanjieOutput **out); // 0 or 1; returns a snapshot
// set_english_inline 0 or 1 (2 otherwise, state unchanged), default 1 at engine creation, like set_english in its codes and its
//   snapshot (handled 1, commit ""); 1 when engine or out is NULL. With 1 (and set_english(1)) the unfinished syllable shows as
//   the letters typed instead of the Zhuyin while all of these hold: the cursor is at the end with one unfinished unit, at least
//   3 letters, the overwrite condition of the English window's trigger, and a listed word starts with the letters. Enter (and
//   reset with commit) then send the letters, and Backspace takes the last letter and makes the rest an English run (zhen-inline section 1.3g).
//   0 shows the Zhuyin until the English window opens. The snapshot shows a composition on screen in the new display. Call it
//   after every engine creation, like set_english.
int32_t shanjie_engine_set_english_inline(ShanjieEngine *engine, uint32_t enabled, ShanjieOutput **out); // 0 or 1; returns a snapshot
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
//   learns; a span is learned only if the flag was 1 both when it was chosen and at commit. 0 while a
//   composition is open, or any key the engine handles while it is 0, also marks the composition: its commit learns
//   no new word and counts no use (new-word B section 2.1). The mark goes when the composition is empty again
//   (commit, Esc, reset, or Backspace/Delete of everything).
int32_t shanjie_engine_set_learning(ShanjieEngine *engine, uint32_t enabled);
// learning_open: dir is the learning directory (.../Application Support/shanjie); created 0700 if
//   missing. A missing file starts empty; a corrupt one is renamed learning.tsv.corrupt and starts
//   empty (both 0). 1 when engine or dir is NULL; 2 when dir is not UTF-8; 3 on I/O failure or a
//   directory not owned by the user.
int32_t shanjie_engine_learning_open(ShanjieEngine *engine, const char *dir);
// Forgetting: KEY with COMMAND (bit3) and kind BACKSPACE (4) while candidates are open removes the
//   highlighted word's learned records for that reading (all contexts) and re-decodes; the output
//   shows the new composition with the candidates still open. When the forgotten word was in effect over that span
//   (exact or last-character level under its context), words the engine fixed (repick-freeze: around a re-pick or a
//   prediction pick, or the pieces a Backspace left) over that span are freed and decode again; other fixed words stay.
//   (The pieces a Backspace or Delete left are also all freed by the next typed syllable, repick-freeze section 15.)
//   On a prediction-row item at the front of the window (predict-down B.3.1) the row is recomputed and the list
//   rebuilt without that word, the selection on the word if it is still listed, else on the first item; a rebuilt
//   list that is empty closes the window (as Esc). Without candidates the key passes through,
//   except while the V3 prediction row is entered: there it forgets the selected item the same way (and drops a
//   pending learn of it), recomputes the row, and the row stays entered with the selection on that word;
//   it never frees fixed words.
//   After a successful learning_open a forget ALWAYS rewrites the whole learning file, even when
//   memory held no record of the word (a record pruned on load can still be in the file); before any
//   successful learning_open it changes memory only. A failed rewrite sets status bit0.
// learning_clear: drops memory, pending learns and the files (a missing file is success); 3 on failure.
//   Also 3 when no learning_open has succeeded on this engine (no file it could have deleted); memory
//   and pending learns are dropped anyway.
int32_t shanjie_engine_learning_clear(ShanjieEngine *engine);
// A single-character pick is learned, and a single-character record looked up, only under a full
//   context key made of exactly two Han characters: the last 2 Han characters before the word, taken
//   from the composition text before it AND the left context from set_left_context. When fewer than
//   two Han characters precede the word (sentence start, after punctuation or ASCII, or only one
//   Han character) a single character is neither learned nor looked up. With a NULL left context, or a
//   paused gate that makes the shell pass NULL, keys that follow two Han characters typed in the same
//   composition still teach (if learning is on) and look up. set_learning(0) stops teaching only; it
//   does not stop lookup (docs/contracts/s4-learning.md sections 12 and 14).
// A word of 2+ characters picked with no Han character before it (context key "^") is learned, and
//   looked up, under the key "^>" plus the reading of the syllable right after the word ("^>$" when the
//   word ends the composition): it boosts the same word only where the next syllable is the same.
//   A prediction-row pick is always stored under "^>$" (it is made at the end of the composition), and a
//   re-pick halves the displaced word under the key it was shown under when it was picked as well as
//   under the key at the commit.
//   The shell computes none of this; it only passes the left-context text (section 14).
// Learning happens only at a commit (Enter, a key the engine passes through after committing, or the
//   128-syllable auto-commit), never on shanjie_engine_reset or Esc, and never for punctuation picks.
// New-word B (docs/contracts/newword-b.md), no new function: at a learning commit, a run of 2-4 single Han
//   characters on the decoded path that holds a character re-picked in the candidate window (runs split at
//   punctuation, non-Han characters and a fixed table of function characters; not all numerals; not a word the
//   lexicon has under that reading, and no lexicon word under that reading scores above -6.5; the lexicon is the
//   one the engine loaded: base, overlays, the names layer and the loaded packs, at their file scores) is stored as a
//   new word, and so is each shorter piece of 2+ characters of it that holds a re-pick, is not all numerals and
//   whose reading has no lexicon word at all; one learning.tsv line each,
//   "#<TAB>reading<TAB>word<TAB>weight<TAB>day". Memory never holds more than 2,000 new words: learning one past
//   that drops the lowest weights at once. The file holds at most 2,000 "#" lines after a full rewrite; lines appended
//   since can pass that until the next full rewrite, and a load keeps at most 2,000 (lowest weights dropped). A new
//   word decodes on its reading in any context, is listed first among the candidates of its length, and is forgotten by COMMAND+BACKSPACE (which
//   also drops the pending re-picks overlapping that reading in the composition) and by learning_clear. A commit
//   with learning on that shows a new word adds to its weight and dates it today, at most once per local day; a
//   re-pick that changes the text shown over it halves it (choosing it back over the same span undoes that). Choosing a new word in the window teaches no context record. A fixed new word whose
//   record is gone is unfixed and decoded again, never an error. Older versions skip "#" lines at load.
// Writes: a learning commit appends only the records it changed; a full rewrite happens on a forget,
//   on the first write after learning_open or a clear, on the first write of each day, every 1,024
//   appended lines, after any failed append, and after a failed full rewrite or forget.
// learning_status: *flags bit0 = the last FULL REWRITE of the learning file failed (an append that
//   fails falls back to a full rewrite, so it counts only through that rewrite). Set by a failed full
//   rewrite; cleared only by a successful full rewrite or a successful learning_clear.
int32_t shanjie_engine_learning_status(ShanjieEngine *engine, uint32_t *flags);

// S7c-2a (docs/contracts/s7c2a-core-rerank.md): the small character model reranks the n-gram's first 8 candidates of each free
//   stretch of the composition, off the key path. Every change of the composition may leave a rerank job; the shell takes it,
//   runs it on another thread, and applies it back on the engine's thread. The composition shows the n-gram result first and may
//   change once when the result applies. Only a change that re-decodes the composition makes the result stale (apply drops it):
//   a completed syllable, a deletion, a candidate or prediction pick, punctuation (it does not commit: it enters the composition,
//   re-decodes and leaves a new job), a learning forget, and the settings that change decoding (profile, demote, rerank, rerank
//   level, a new model, learning open / clear; set_learning never makes the job stale: it does not change decoding, S7c-2b). Keys that do not re-decode (cursor moves, unfinished zhuyin symbols, entering the
//   prediction row, pass-through keys) keep the job valid. A key or pick that decodes a free stretch longer than the window W (the small
//   model's context minus 3 characters, 61 without a model; rerank on or off) first freezes its head down to W - 16 characters as
//   frozen words (docs/contracts/composition-cap.md section 1.2), then decodes again: its output and its job are those after the freeze. Commits (ENTER, the auto-commit at 128 tokens, reset) send what is
//   shown; a job not yet applied is dropped.
// Logging: a job and its result hold candidate text. Never log them, their pointers, or the .sjtl path (the rules at the top).
#define SHANJIE_NO_CHANGE 5
typedef struct ShanjieRerankJob ShanjieRerankJob;
// load_tinylm: the .sjtl for the loaded n-gram model. Call it after shanjie_engine_load_lm (a successful load_lm drops the small
//   model: the .sjtl names the n-gram model's hash). 3 when the file cannot be read, is not a regular file, is over 64 MiB or
//   malformed, when no n-gram model is loaded, when the .sjtl was tuned with another n-gram model, or when the Kneser-Ney side
//   file state differs from the one it records (a version 4 .sjtl records the SHA-256 of the kn.sjkn it was tuned with, all
//   zero for none; versions 1 to 3 count as none; the loaded kn.sjkn, or none, must match all 32 bytes; docs/contracts/model-v6.md
//   section 2.7) (a pairing check only, not an integrity check of the .sjtl); the engine keeps its previous model (or none) and
//   the n-gram keeps working. 1 for a NULL engine or path, 2 for a path that is not UTF-8. Does not change the rerank setting
//   (set_rerank) or the current display.
int32_t shanjie_engine_load_tinylm(ShanjieEngine *engine, const char *path);
// load_colloquial: the colloquial table (colloquial.sjlm, docs/contracts/colloquial-term.md). The engine reads the word sets of its k term
//   from the lexicon folder it was created with (mcbpmf-data.txt, overlay-add.tsv) and from the pack rows it was created with. May be
//   called before or after load_lm and load_tinylm (the table does not depend on them). The term applies per job, only with the chat
//   profile, at any of the three rerank strengths (each with its own alpha, tau and delta: docs/contracts/colloquial-levels.md) and a small
//   model whose SHA-256 is the one the term was measured with; in every other case the jobs are those of an engine without the table. Where the term applies the fusion also gets the reading-channel term (docs/contracts/reading-channel.md:
//   each single-character word's reading against that word's most common reading in the lexicon, a rare reading costs up to 4 per character); it has no
//   switch, file or setting of its own and changes nothing where the colloquial term does not apply. 3 when a file cannot be read or is malformed (the engine keeps its previous table,
//   or none); 1 for a NULL engine or path; 2 for a path that is not UTF-8. Drops the pending rerank job. Never log the path.
int32_t shanjie_engine_load_colloquial(ShanjieEngine *engine, const char *path);
// set_rerank 0 or 1 (2 otherwise, state unchanged), default 0 at engine creation (S7c-2c), like set_demote in its codes and its
//   snapshot (handled 1, commit ""): it recomputes the composition, so turning it off shows the n-gram result at once; 1 when engine or
//   out is NULL. Call it after every engine creation, like set_candidate_vertical. With 0, or without a small model, or with
//   set_demote 0 (alpha and tau hold only with the table on), no job is ever made.
int32_t shanjie_engine_set_rerank(ShanjieEngine *engine, uint32_t enabled, ShanjieOutput **out);
// Rerank strength (docs/contracts/rerank-strength.md section 2.2): set_rerank_level 0 conservative, 1 standard (default at engine
//   creation), 2 aggressive: which of the small model's (alpha, tau, delta) the jobs use. Codes and snapshot like set_profile
//   (handled 1, commit ""): 1 when engine or out is NULL, 2 for a level above 2 (state unchanged, the job of the last change
//   stays valid), 4 internal (the engine is reset in discard mode, the new level kept). It makes a job taken before stale; a job
//   keeps the values of the level it was made with. Without a small model the level is remembered and 0 returned; with a
//   version 1 or 2 .sjtl (only the standard set) any level is remembered, 0 returned, and the standard set used.
//   load_tinylm and load_lm keep the level. The shell sends its stored level after every engine creation, after load_tinylm.
int32_t shanjie_engine_set_rerank_level(ShanjieEngine *engine, uint32_t level, ShanjieOutput **out);
// rerank_levels: *levels is a bit mask of the levels the loaded small model offers (bit0 conservative, bit1 standard, bit2
//   aggressive): 0 without a small model, 2 for a version 1 or 2 .sjtl, 7 for a version 3 or 4 .sjtl (always 7, even when a level's
//   values equal the standard ones, and whatever the profile). 0 success; 1 when engine or levels is NULL (*levels untouched);
//   4 internal (nothing is reset: it changes nothing). Changes nothing.
int32_t shanjie_engine_rerank_levels(ShanjieEngine *engine, uint32_t *levels);
// rerank_take: hands out the job of the last change, once; 0 with *job NULL when there is none. 1 when engine or job is NULL
//   (*job is set to NULL first whenever job is non-NULL); 4 internal (the engine is reset in discard mode, *job NULL).
//   Every job handed out must be freed exactly once with shanjie_rerank_free.
int32_t shanjie_engine_rerank_take(ShanjieEngine *engine, ShanjieRerankJob **job);
// rerank_run: computes the result and keeps it in the job. Callable on any thread; it needs no engine and the job does not
//   borrow one, so it may run (or be freed) after shanjie_engine_free. It cannot be cancelled: a newer key only makes the
//   result stale. 0 success, 1 job NULL, 4 caught panic: no engine is touched (the composition is unchanged) and no result
//   is kept, not even an earlier run's (a later apply returns SHANJIE_NO_CHANGE).
int32_t shanjie_rerank_run(ShanjieRerankJob *job);
// rerank_apply: on the engine's thread, after run returned. Takes the job's result: 0 and a snapshot (handled 1, commit "")
//   when the composition changed; SHANJIE_NO_CHANGE with *out NULL when there is no result (not run, or run returned 4), the
//   result is stale, the composition no longer holds the words the job was made from, the candidate window is open, the
//   prediction row is entered, every stretch kept its first candidate, or the job was applied before (a second apply of the
//   same job never changes anything). The prediction row in the snapshot is recomputed only if one was showing. Does not free
//   the job. 1 when engine, job or out is NULL (*out NULL); 4 internal (the engine is reset in discard mode, *out NULL).
int32_t shanjie_engine_rerank_apply(ShanjieEngine *engine, ShanjieRerankJob *job, ShanjieOutput **out);
// rerank_free(NULL) does nothing. Like run, it may be called on any thread, once run has returned (never while run is running on
//   the same job). Undefined behaviour: freeing a job twice, run or apply after free, freeing a pointer rerank_take did not hand
//   out, and using one job from two threads at once (apply or free while run is still running).
void    shanjie_rerank_free(ShanjieRerankJob *job);

#ifdef __cplusplus
}
#endif

#endif /* SHANJIE_H */
