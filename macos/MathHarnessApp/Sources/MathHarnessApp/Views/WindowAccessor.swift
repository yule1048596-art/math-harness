import AppKit
import SwiftUI

/// 拿到承载这棵视图的 `NSWindow`。
///
/// 用来记住窗口大小。SwiftUI 的 `.defaultSize` 只管第一次：用户把窗口拉大之后，下次
/// 启动又回到原样，每次都得重新拉一遍。
struct WindowAccessor: NSViewRepresentable {
  let autosaveName: String

  func makeNSView(context: Context) -> NSView {
    let view = NSView(frame: .zero)
    // 视图刚创建时还没进窗口，要等一拍。
    DispatchQueue.main.async {
      guard let window = view.window else { return }
      guard window.frameAutosaveName != autosaveName else { return }
      // 顺序有讲究：`setFrameAutosaveName` 会立刻把当前尺寸写进去，所以必须**先**
      // 恢复上次的尺寸，否则等于每次启动都把记录覆盖成默认值。
      window.setFrameUsingName(autosaveName)
      window.setFrameAutosaveName(autosaveName)
    }
    return view
  }

  func updateNSView(_ nsView: NSView, context: Context) {}
}
