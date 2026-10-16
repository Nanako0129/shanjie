// App icon layers (docs/contracts/app-icon.md): writes frame.svg and glyph.svg, the two glass layers of
// packaging/AppIcon.icon, from the 解 of Noto Serif CJK TC Black. Run it only to change the design; the build uses the
// committed SVGs and needs no font.
//
//   swift scripts/make-app-icon.swift <NotoSerifCJKtc-Black.otf> packaging/AppIcon.icon/Assets
//
// The font (SIL Open Font License 1.1) is not in the repo. The committed SVGs came from this file, downloaded 2026-10-17:
// https://github.com/notofonts/noto-cjk/raw/main/Serif/OTF/TraditionalChinese/NotoSerifCJKtc-Black.otf
// The script refuses any other file (fontSHA256), so a later upstream revision cannot change the glyph unnoticed.
import AppKit
import CoreText
import CryptoKit

let fontSHA256 = "3ad2c936d8757c8c4d661394592a40d29bd66351d0e6971310f19b159d380db7"
let fontName = "NotoSerifCJKtc-Black"

/// Icon Composer's canvas, in points; the system masks it to the macOS app icon shape.
let canvas: CGFloat = 1024
/// The seal frame and the glyph as first drawn (draft A, the site favicon's seal): a 696 pt rounded square
/// with 120 pt corners and a 22 pt stroke, and the glyph at 560 pt.
let frameSide: CGFloat = 696, frameRadius: CGFloat = 120, frameStroke: CGFloat = 22, glyphSize: CGFloat = 560
/// Both scaled about the centre: the maintainer asked for the whole seal 10% larger (2026-10-17, after the g2b draft).
let scale: CGFloat = 1.10

let args = CommandLine.arguments
guard args.count == 3 else {
    FileHandle.standardError.write(Data("usage: make-app-icon.swift <NotoSerifCJKtc-Black.otf> <out dir>\n".utf8))
    exit(64)
}
guard let data = FileManager.default.contents(atPath: args[1]),
      SHA256.hash(data: data).map({ String(format: "%02x", $0) }).joined() == fontSHA256,
      let descs = CTFontManagerCreateFontDescriptorsFromURL(URL(fileURLWithPath: args[1]) as CFURL) as? [CTFontDescriptor],
      let desc = descs.first(where: { CTFontDescriptorCopyAttribute($0, kCTFontNameAttribute) as? String == fontName }) else {
    FileHandle.standardError.write(Data("\(args[1]) is not the pinned \(fontName) (SHA-256 \(fontSHA256))\n".utf8))
    exit(66)
}
let font = CTFontCreateWithFontDescriptor(desc, glyphSize * scale, nil)
var chars = Array("解".utf16), glyphs = [CGGlyph](repeating: 0, count: 1)
guard CTFontGetGlyphsForCharacters(font, &chars, &glyphs, 1), let outline = CTFontCreatePathForGlyph(font, glyphs[0], nil) else {
    FileHandle.standardError.write(Data("the font has no 解\n".utf8))
    exit(65)
}
// Centre the glyph's ink box on the canvas and flip to SVG's y-down coordinates.
let box = outline.boundingBoxOfPath
var place = CGAffineTransform(a: 1, b: 0, c: 0, d: -1, tx: canvas / 2 - box.midX, ty: canvas / 2 + box.midY)
let placed = outline.copy(using: &place)!

func n(_ v: CGFloat) -> String { String(format: "%.1f", v) }
var d = ""
placed.applyWithBlock { e in
    let p = e.pointee.points
    switch e.pointee.type {
    case .moveToPoint: d += "M\(n(p[0].x)) \(n(p[0].y))"
    case .addLineToPoint: d += "L\(n(p[0].x)) \(n(p[0].y))"
    case .addQuadCurveToPoint: d += "Q\(n(p[0].x)) \(n(p[0].y)) \(n(p[1].x)) \(n(p[1].y))"
    case .addCurveToPoint: d += "C\(n(p[0].x)) \(n(p[0].y)) \(n(p[1].x)) \(n(p[1].y)) \(n(p[2].x)) \(n(p[2].y))"
    case .closeSubpath: d += "Z"
    @unknown default: break
    }
}
// White layers: Icon Composer turns them into glass over the vermilion fill (icon.json).
let head = "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 \(n(canvas)) \(n(canvas))\" width=\"\(n(canvas))\" height=\"\(n(canvas))\">\n"
let side = frameSide * scale, origin = (canvas - side) / 2
let frame = head + "<rect x=\"\(n(origin))\" y=\"\(n(origin))\" width=\"\(n(side))\" height=\"\(n(side))\" rx=\"\(n(frameRadius * scale))\" "
    + "fill=\"none\" stroke=\"#FFFFFF\" stroke-width=\"\(n(frameStroke * scale))\"/>\n</svg>\n"
let glyph = head + "<path fill=\"#FFFFFF\" d=\"\(d)\"/>\n</svg>\n"
do {
    try frame.write(toFile: "\(args[2])/frame.svg", atomically: true, encoding: .utf8)
    try glyph.write(toFile: "\(args[2])/glyph.svg", atomically: true, encoding: .utf8)
} catch {
    FileHandle.standardError.write(Data("cannot write to \(args[2])\n".utf8))
    exit(73)
}
