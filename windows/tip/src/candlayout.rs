//! The geometry of the candidate window (docs/spec/windows-tip.md §4.3 and §3.13's candidate window section): sizes at a DPI, the window's width
//! for the text it holds, and where the window goes next to the composition. Pure integer functions with saturating arithmetic: no Windows
//! types, so the unit tests run on every platform, and no value from the host or the app (a text rectangle of `i32::MIN` included) can overflow.
//!
//! Every pixel size below is the Microsoft Zhuyin's, measured at 100% in docs/spec/windows-tip.md §4.3 and scaled with the window's DPI; a row of that
//! table that was not measured says so.
#![cfg_attr(not(windows), allow(dead_code))]

/// A rectangle in physical screen pixels: `right` and `bottom` are exclusive, like `RECT`.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Rect {
    pub left: i32,
    pub top: i32,
    pub right: i32,
    pub bottom: i32,
}

/// §4.3 "邊框": 1 px at 100%.
const BORDER: i32 = 1;
/// §4.3 "邊框到內容的留白": 4 px at 100%, above the first row and on both sides. The window's height 294 = 2 × 1 + 4 + 9 × 32 leaves none below.
const PADDING: i32 = 4;
/// §4.3 "每列間距": 32 px at 100%.
const ROW: i32 = 32;
/// §4.3 "選取列的底色塊": 28 px high (2 above and 2 below inside its row), as wide as the window less the border and padding on both sides.
const BLOCK_H: i32 = 28;
/// §4.3 "選取條": 3 px wide and 14 px high at 100% (the height measured in the test VM's 24-bit capture of Microsoft Zhuyin, Notepad, light and
/// dark), centred in the block. The block's corners (4 px) are not measured: a guess to be corrected by the side-by-side screenshots.
const BAR_W: i32 = 3;
const BAR_H: i32 = 14;
const BLOCK_RADIUS: i32 = 4;
/// §4.3 "外框圓角": about 8 px (not measured at 150%: it scales).
const WINDOW_RADIUS: i32 = 8;
/// Where the number and the text start inside the block, and the space kept right of the text. §4.3 has no column positions: a guess that fits
/// a 3-px bar, a one-digit number and a 132-px block holding a four-character word.
const NUMBER_X: i32 = 12;
const TEXT_X: i32 = 36;
const TEXT_PAD_RIGHT: i32 = 12;
/// Font sizes at 100%. §4.3 gives none either; the candidate text fills most of the 28-px block and the number is a little smaller.
const FONT_PX: i32 = 16;
const NUMBER_FONT_PX: i32 = 14;
/// §4.3 "候選窗外框" width without the bottom row: 142 px at 100% (96 dpi) and 215 px at 150% (144 dpi); the width of the other DPIs is on the line
/// between those two points (extended past them). 150% is not 1.5 × 142: the measured window is 2 px wider.
const MIN_W_100: i64 = 142;
const MIN_W_150: i64 = 215;
const DPI_100: i64 = 96;
const DPI_150: i64 = 144;

/// A DPI the code will lay out for: Windows reports 96 (100%) to a few hundred; anything else is a failed query.
pub fn sane_dpi(dpi: u32) -> u32 {
    if (96..=768).contains(&dpi) {
        dpi
    } else {
        96
    }
}

/// `px` at 100% scaled to `dpi`, rounded half up: 1 → 2 and 4 → 6 and 28 → 42 and 32 → 48 at 150%, the §4.3 150% column.
pub fn scale(px: i32, dpi: u32) -> i32 {
    ((i64::from(px) * i64::from(dpi) + 48) / 96) as i32
}

/// The sizes of one window at one DPI.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Metrics {
    pub border: i32,
    pub padding: i32,
    pub row: i32,
    pub block_h: i32,
    pub bar_w: i32,
    pub bar_h: i32,
    pub block_radius: i32,
    pub window_radius: i32,
    pub number_x: i32,
    pub text_x: i32,
    pub text_pad_right: i32,
    pub font_px: i32,
    pub number_font_px: i32,
    pub min_width: i32,
}

impl Metrics {
    pub fn new(dpi: u32) -> Self {
        let dpi = sane_dpi(dpi);
        let d = i64::from(dpi);
        let min_width = MIN_W_100 + (d - DPI_100) * (MIN_W_150 - MIN_W_100) / (DPI_150 - DPI_100);
        Self {
            border: scale(BORDER, dpi),
            padding: scale(PADDING, dpi),
            row: scale(ROW, dpi),
            block_h: scale(BLOCK_H, dpi),
            bar_w: scale(BAR_W, dpi),
            bar_h: scale(BAR_H, dpi),
            block_radius: scale(BLOCK_RADIUS, dpi),
            window_radius: scale(WINDOW_RADIUS, dpi),
            number_x: scale(NUMBER_X, dpi),
            text_x: scale(TEXT_X, dpi),
            text_pad_right: scale(TEXT_PAD_RIGHT, dpi),
            font_px: scale(FONT_PX, dpi),
            number_font_px: scale(NUMBER_FONT_PX, dpi),
            min_width: min_width as i32,
        }
    }

    /// The window's height for `rows` candidates: the two borders, the padding above the first row and the rows (294 for nine at 100%, 442 at 150%).
    pub fn height(&self, rows: usize) -> i32 {
        let rows = i32::try_from(rows).unwrap_or(i32::MAX);
        self.border.saturating_mul(2).saturating_add(self.padding).saturating_add(self.row.saturating_mul(rows))
    }

    /// The border and padding on one side of the content.
    fn inset(&self) -> i32 {
        self.border + self.padding
    }

    /// The window's width for a widest candidate `text_w` px wide, in a work area `work_w` px wide: room for the number column, the text and the
    /// right margin, not narrower than the measured window, not wider than half the work area.
    pub fn width(&self, text_w: i32, work_w: i32) -> i32 {
        let natural = self.inset().saturating_mul(2).saturating_add(self.text_x).saturating_add(text_w.max(0)).saturating_add(self.text_pad_right);
        natural.max(self.min_width).min((work_w / 2).max(1))
    }

    /// The width left for the text of a window `width` wide (the part the layout is allowed to fill before it is trimmed).
    pub fn text_width(&self, width: i32) -> i32 {
        width.saturating_sub(self.inset().saturating_mul(2)).saturating_sub(self.text_x).saturating_sub(self.text_pad_right).max(0)
    }

    /// The top of row `i` (the row is `row` high, full width inside the border).
    pub fn row_top(&self, i: usize) -> i32 {
        let i = i32::try_from(i).unwrap_or(i32::MAX);
        self.inset().saturating_add(self.row.saturating_mul(i))
    }

    /// The selection block of row `i` in a window `width` wide: centred in the row, inside the border and padding.
    pub fn block(&self, i: usize, width: i32) -> Rect {
        let top = self.row_top(i).saturating_add((self.row - self.block_h) / 2);
        Rect { left: self.inset(), top, right: width.saturating_sub(self.inset()), bottom: top.saturating_add(self.block_h) }
    }
}

/// Where the window's top-left corner goes: at the bottom-left of the composition `comp`; when it does not fit below inside `work` (the work area of
/// the monitor), above the composition, its bottom at the composition's top; never above the top of `work`; pushed left when it runs out on the
/// right, then right when out on the left, so it starts inside `work` (the left edge wins when the window is wider than the work area).
/// Saturating arithmetic only.
pub fn place(comp: Rect, w: i32, h: i32, work: Rect) -> (i32, i32) {
    let y = if comp.bottom.saturating_add(h) <= work.bottom { comp.bottom } else { comp.top.saturating_sub(h) }.max(work.top);
    let x = if comp.left.saturating_add(w) > work.right { work.right.saturating_sub(w) } else { comp.left };
    (x.max(work.left), y)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn r(left: i32, top: i32, right: i32, bottom: i32) -> Rect {
        Rect { left, top, right, bottom }
    }

    #[test]
    fn the_sizes_at_100_and_150_percent_are_the_measured_ones() {
        let m = Metrics::new(96);
        assert!((m.border, m.padding, m.row, m.block_h, m.bar_w, m.window_radius) == (1, 4, 32, 28, 3, 8));
        assert!(m.height(9) == 294 && m.min_width == 142, "142 x 294 without the bottom row");
        let m = Metrics::new(144);
        assert!((m.border, m.padding, m.row, m.block_h) == (2, 6, 48, 42));
        assert!(m.height(9) == 442 && m.min_width == 215, "215 x 442 without the bottom row");
        // The block of the first row at 100%: 132 wide (142 less 1 + 4 on both sides), 28 high, 2 below the padding.
        let b = Metrics::new(96).block(0, 142);
        assert!(b == r(5, 7, 137, 35), "block {b:?}");
        let b = Metrics::new(96).block(8, 142);
        assert!(b.bottom == 5 + 8 * 32 + 2 + 28 && b.bottom + 2 + 1 == 294, "the last block ends 2 px and the border above the bottom edge");
    }

    #[test]
    fn other_dpis_scale_and_a_failed_query_is_100_percent() {
        for dpi in [96, 120, 144, 168, 192, 288] {
            let m = Metrics::new(dpi);
            assert!(m.row == scale(32, dpi) && m.height(1) < m.height(9) && m.min_width >= 142, "dpi {dpi}");
        }
        assert!(Metrics::new(192).min_width == 288 && Metrics::new(120).min_width == 142 + 24 * 73 / 48);
        for dpi in [0, 1, 95, 769, u32::MAX] {
            assert!(Metrics::new(dpi) == Metrics::new(96), "dpi {dpi}");
        }
        assert!(scale(0, 96) == 0 && scale(1, 144) == 2 && scale(4, 144) == 6 && scale(28, 144) == 42 && scale(32, 144) == 48);
    }

    #[test]
    fn the_width_follows_the_text_between_the_measured_width_and_half_the_work_area() {
        let m = Metrics::new(96);
        assert!(m.width(0, 1920) == 142 && m.width(40, 1920) == 142, "short text keeps the measured width");
        // 2 x (1 + 4) + 36 + text + 12.
        assert!(m.width(200, 1920) == 10 + 36 + 200 + 12);
        assert!(m.width(5000, 1920) == 960, "half of the work area");
        assert!(m.width(i32::MAX, 1920) == 960 && m.width(-5, 1920) == 142);
        assert!(m.width(40, 0) == 1 && m.width(40, i32::MIN) == 1, "a broken work area never gives zero or less");
        let w = m.width(200, 1920);
        assert!(m.text_width(w) == 200 && m.text_width(0) == 0 && m.text_width(i32::MIN) == 0);
    }

    #[test]
    fn rows_beyond_the_count_do_not_overflow() {
        let m = Metrics::new(96);
        assert!(m.height(0) == 6 && m.height(1) == 38);
        let _ = (m.height(usize::MAX), m.row_top(usize::MAX), m.block(usize::MAX, i32::MAX), m.block(0, i32::MIN), m.width(i32::MAX, i32::MAX));
    }

    #[test]
    fn the_window_goes_below_the_composition_left_aligned() {
        let work = r(0, 0, 1920, 1080);
        // A composition from (100, 200) to (160, 224), the window 142 x 294.
        assert!(place(r(100, 200, 160, 224), 142, 294, work) == (100, 224));
    }

    #[test]
    fn the_window_goes_above_when_it_does_not_fit_below() {
        let work = r(0, 0, 1920, 1080);
        // 800 + 24 + 294 > 1080: above, its bottom at the composition's top.
        assert!(place(r(100, 800, 160, 824), 142, 294, work) == (100, 800 - 294));
        // Exactly touching the bottom still fits below.
        assert!(place(r(100, 762, 160, 786), 142, 294, work) == (100, 786));
        assert!(place(r(100, 763, 160, 787), 142, 294, work).1 == 763 - 294);
        // Neither side fits: the top of the work area.
        assert!(place(r(100, 100, 160, 124), 142, 2000, work) == (100, 0));
        // A work area that does not start at 0 (a second monitor above or to the left).
        let work = r(-1920, -1080, 0, 0);
        assert!(place(r(-500, -30, -440, -6), 142, 294, work) == (-500, -30 - 294));
    }

    #[test]
    fn the_window_is_pushed_inside_the_work_area_sideways() {
        let work = r(0, 0, 1920, 1080);
        assert!(place(r(1900, 200, 1910, 224), 142, 294, work) == (1920 - 142, 224), "pushed left at the right edge");
        assert!(place(r(-50, 200, -40, 224), 142, 294, work) == (0, 224), "pushed right at the left edge");
        assert!(place(r(100, 200, 160, 224), 3000, 294, work).0 == 0, "wider than the work area: the left edge wins");
    }

    #[test]
    fn extreme_rectangles_and_work_areas_never_panic() {
        let ext = [i32::MIN, i32::MIN + 1, -1, 0, 1, i32::MAX - 1, i32::MAX];
        for &a in &ext {
            for &b in &ext {
                for &c in &ext {
                    let comp = r(a, b, c, a);
                    let work = r(b, c, a, b);
                    for (w, h) in [(0, 0), (142, 294), (i32::MAX, i32::MAX), (i32::MIN, i32::MIN), (1, i32::MAX)] {
                        let _ = place(comp, w, h, work);
                    }
                }
            }
        }
        // Real work, whatever the composition rectangle says: a result inside the work area when the window fits in it.
        let work = r(0, 0, 1920, 1080);
        for &a in &ext {
            let (x, y) = place(r(a, a, a, a), 142, 294, work);
            assert!(x >= 0 && x + 142 <= 1920 && y >= 0, "left {a}: ({x}, {y})");
        }
    }
}
