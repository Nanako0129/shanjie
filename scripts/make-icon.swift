// Menu bar icon (docs/contracts/s3b.md section 2): 解 in a rounded square, monochrome template,
// drawn locally with CoreText from a system font. Nothing is downloaded.
//
//   swift scripts/make-icon.swift <out.tiff>
//
// The TIFF holds a 16 px and a 32 px (Retina) representation of the same 16 pt image.
import AppKit

guard CommandLine.arguments.count == 2 else {
    FileHandle.standardError.write(Data("usage: make-icon.swift <out.tiff>\n".utf8))
    exit(64)
}

func draw(pixels: Int) -> NSBitmapImageRep {
    let rep = NSBitmapImageRep(
        bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels, bitsPerSample: 8,
        samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
        bytesPerRow: 0, bitsPerPixel: 0)!
    rep.size = NSSize(width: 16, height: 16)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let ctx = NSGraphicsContext.current!.cgContext
    let scale = CGFloat(pixels) / 16
    ctx.scaleBy(x: scale, y: scale)

    // The seal: a rounded square outline, inset so the stroke stays inside the 16 pt box.
    NSColor.black.setStroke()
    let box = NSBezierPath(roundedRect: NSRect(x: 1, y: 1, width: 14, height: 14), xRadius: 3, yRadius: 3)
    box.lineWidth = 1.2
    box.stroke()

    // 解, centred on its glyph bounds.
    let font = CTFontCreateUIFontForLanguage(.system, 10.5, "zh-Hant" as CFString)!
    let text = NSAttributedString(string: "解", attributes: [
        .font: font, .foregroundColor: NSColor.black,
    ])
    let line = CTLineCreateWithAttributedString(text)
    let bounds = CTLineGetBoundsWithOptions(line, .useGlyphPathBounds)
    ctx.textPosition = CGPoint(x: 8 - bounds.midX, y: 8 - bounds.midY)
    CTLineDraw(line, ctx)

    NSGraphicsContext.restoreGraphicsState()
    return rep
}

let data = NSBitmapImageRep.tiffRepresentationOfImageReps(in: [draw(pixels: 16), draw(pixels: 32)], using: .lzw, factor: 0)!
try data.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
