// Reads a scanned PDF with the OCR built into macOS (Vision). Offline, no dependency.
import Foundation
import PDFKit
import Vision
import CoreGraphics

let args = CommandLine.arguments
guard args.count > 1,
      let doc = PDFDocument(url: URL(fileURLWithPath: args[1])) else {
    FileHandle.standardError.write(Data("kispy-ocr: cannot open that PDF\n".utf8))
    exit(1)
}

var out = ""
for i in 0..<doc.pageCount {
    guard let page = doc.page(at: i) else { continue }
    let rect = page.bounds(for: .mediaBox)
    let scale: CGFloat = 2.0
    let w = Int(rect.width * scale), h = Int(rect.height * scale)
    guard w > 0, h > 0,
          let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8,
                              bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(),
                              bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)
    else { continue }
    ctx.setFillColor(CGColor(gray: 1, alpha: 1))
    ctx.fill(CGRect(x: 0, y: 0, width: w, height: h))
    ctx.scaleBy(x: scale, y: scale)
    ctx.translateBy(x: -rect.origin.x, y: -rect.origin.y)
    page.draw(with: .mediaBox, to: ctx)
    guard let img = ctx.makeImage() else { continue }

    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.recognitionLanguages = ["en-US", "fr-FR", "de-DE", "es-ES", "it-IT"]
    req.usesLanguageCorrection = true
    try? VNImageRequestHandler(cgImage: img, options: [:]).perform([req])
    let lines = (req.results ?? []).compactMap { $0.topCandidates(1).first?.string }
    if !lines.isEmpty {
        out += "--- page \(i + 1) ---\n" + lines.joined(separator: "\n") + "\n"
    }
}
print(out)
