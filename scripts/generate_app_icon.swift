// 生成 App 图标。用 CoreGraphics 而不是外部图形库，是为了不给这个纯 Swift + Python
// 的项目增加构建期依赖；`swift` 本来就是构建 App 的前提。
//
// 图形是「曲线趋近渐近线」——这个项目的实际数学领域。小尺寸下它退化成一个抽象记号，
// 仍然可辨认。
//
// 用法：swift scripts/generate_app_icon.swift <输出 iconset 目录>

import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

let sizes: [(name: String, pixels: Int)] = [
  ("icon_16x16", 16),
  ("icon_16x16@2x", 32),
  ("icon_32x32", 32),
  ("icon_32x32@2x", 64),
  ("icon_128x128", 128),
  ("icon_128x128@2x", 256),
  ("icon_256x256", 256),
  ("icon_256x256@2x", 512),
  ("icon_512x512", 512),
  ("icon_512x512@2x", 1024),
]

func roundedRectPath(_ rect: CGRect, radius: CGFloat) -> CGPath {
  CGPath(roundedRect: rect, cornerWidth: radius, cornerHeight: radius, transform: nil)
}

func drawIcon(size: CGFloat, into context: CGContext) {
  let rect = CGRect(x: 0, y: 0, width: size, height: size)
  context.setShouldAntialias(true)
  context.interpolationQuality = .high

  // macOS 26 的图标网格：内容留出约 10% 边距，圆角约为边长的 22%。
  let inset = size * 0.06
  let plate = rect.insetBy(dx: inset, dy: inset)
  let radius = plate.width * 0.225
  let path = roundedRectPath(plate, radius: radius)

  context.saveGState()
  context.addPath(path)
  context.clip()

  let space = CGColorSpaceCreateDeviceRGB()
  // 深靛蓝 → 蓝，冷色调，和数学工具的气质一致，也能让白色线条有足够对比。
  let colors =
    [
      CGColor(colorSpace: space, components: [0.16, 0.19, 0.42, 1.0])!,
      CGColor(colorSpace: space, components: [0.10, 0.42, 0.72, 1.0])!,
    ] as CFArray
  if let gradient = CGGradient(colorsSpace: space, colors: colors, locations: [0, 1]) {
    context.drawLinearGradient(
      gradient,
      start: CGPoint(x: plate.minX, y: plate.maxY),
      end: CGPoint(x: plate.maxX, y: plate.minY),
      options: []
    )
  }

  let unit = plate.width
  let originX = plate.minX
  let originY = plate.minY
  func point(_ x: CGFloat, _ y: CGFloat) -> CGPoint {
    CGPoint(x: originX + unit * x, y: originY + unit * y)
  }

  // 线宽按边长比例，保证 32px 下也有足够视觉重量——上一版太细，小尺寸糊成一团。
  let lineWidth = max(unit * 0.075, 1.5)
  context.setLineCap(.round)

  // 渐近线：少量长虚线。段数太多在小尺寸下会退化成噪点。
  context.setStrokeColor(CGColor(colorSpace: space, components: [1, 1, 1, 0.55])!)
  context.setLineWidth(lineWidth * 0.7)
  context.setLineDash(phase: 0, lengths: [unit * 0.17, unit * 0.10])
  context.move(to: point(0.13, 0.68))
  context.addLine(to: point(0.87, 0.68))
  context.strokePath()
  context.setLineDash(phase: 0, lengths: [])

  // 曲线：先平缓、再陡升、最后趋于水平，逼近渐近线但留出明显间距。
  // 上一版终点离渐近线只有一个线宽，两者视觉上黏在一起，「趋近而不相交」就没了。
  context.setStrokeColor(CGColor(colorSpace: space, components: [1, 1, 1, 1])!)
  context.setLineWidth(lineWidth)
  context.setLineJoin(.round)
  // 控制点让曲线「先陡升、再长距离趋平」——这才是渐近形状。上一版是先平后陡再平的
  // S 形，读起来像一段普通折线，不像在逼近什么。
  context.move(to: point(0.15, 0.17))
  context.addCurve(
    to: point(0.86, 0.555),
    control1: point(0.31, 0.51),
    control2: point(0.49, 0.555)
  )
  context.strokePath()

  context.restoreGState()
}

guard CommandLine.arguments.count == 2 else {
  FileHandle.standardError.write(
    Data("usage: generate_app_icon.swift <iconset-directory>\n".utf8)
  )
  exit(2)
}

let outputDirectory = URL(fileURLWithPath: CommandLine.arguments[1])
try FileManager.default.createDirectory(
  at: outputDirectory,
  withIntermediateDirectories: true
)

for entry in sizes {
  let pixels = entry.pixels
  guard
    let context = CGContext(
      data: nil,
      width: pixels,
      height: pixels,
      bitsPerComponent: 8,
      bytesPerRow: 0,
      space: CGColorSpaceCreateDeviceRGB(),
      bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
    )
  else {
    FileHandle.standardError.write(Data("failed to create context\n".utf8))
    exit(1)
  }

  drawIcon(size: CGFloat(pixels), into: context)

  guard let image = context.makeImage() else {
    FileHandle.standardError.write(Data("failed to render image\n".utf8))
    exit(1)
  }

  let url = outputDirectory.appendingPathComponent("\(entry.name).png")
  guard
    let destination = CGImageDestinationCreateWithURL(
      url as CFURL,
      UTType.png.identifier as CFString,
      1,
      nil
    )
  else {
    FileHandle.standardError.write(Data("failed to create destination\n".utf8))
    exit(1)
  }
  CGImageDestinationAddImage(destination, image, nil)
  guard CGImageDestinationFinalize(destination) else {
    FileHandle.standardError.write(Data("failed to write \(url.path)\n".utf8))
    exit(1)
  }
}

print("Wrote \(sizes.count) icon images to \(outputDirectory.path)")
