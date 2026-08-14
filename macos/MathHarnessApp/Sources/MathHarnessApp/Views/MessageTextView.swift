import MathHarnessCore
import SwiftUI

/// 回答正文。按块渲染 Markdown，但**先保证公式不被改掉**。
///
/// 模型的回答里 `### 标题`、`- 列表`、`**加粗**` 是常态，纯文本显示等于把结构全丢了。
/// 但这是个数学软件：`x**2 + y**2` 交给 Markdown 会变成 `x<strong>2 + y</strong>2`，
/// 星号凭空消失。所以先把当运算符用的 `*` `_` 转义掉（`MathMarkdown`，有断言钉着），
/// 再渲染；解析失败就退回纯文本，绝不因为渲染而少显示一个字。
struct MessageTextView: View {
  let text: String

  @AppStorage(AppSettingsKey.messageTextSize)
  private var textSize = MessageTextSize.medium.rawValue
  @AppStorage(AppSettingsKey.rendersMarkdown)
  private var rendersMarkdown = true
  @AppStorage(AppSettingsKey.typesetsFormulas)
  private var typesetsFormulas = true

  private var size: MessageTextSize {
    MessageTextSize(rawValue: textSize) ?? .medium
  }

  var body: some View {
    Group {
      if rendersMarkdown {
        VStack(alignment: .leading, spacing: 5) {
          ForEach(Array(MathMarkdown.blocks(from: text).enumerated()), id: \.offset) {
            _, block in
            view(for: block)
          }
        }
      } else {
        Text(text)
          .font(.system(size: size.pointSize))
          .lineSpacing(size.lineSpacing)
      }
    }
    .frame(maxWidth: .infinity, alignment: .leading)
    .textSelection(.enabled)
  }

  @ViewBuilder
  private func view(for block: MathMarkdown.Block) -> some View {
    switch block {
    case .blank:
      Color.clear.frame(height: 1)

    case .heading(let level, let content):
      inline(content)
        .font(
          .system(size: size.pointSize + (level <= 2 ? 3 : 1), weight: .semibold)
        )
        .padding(.top, 3)
        .frame(maxWidth: .infinity, alignment: .leading)

    case .listItem(let marker, let content):
      HStack(alignment: .firstTextBaseline, spacing: 7) {
        Text(marker)
          .font(.system(size: size.pointSize))
          .foregroundStyle(.secondary)
          .frame(minWidth: 14, alignment: .trailing)
        inline(content)
          .font(.system(size: size.pointSize))
          .lineSpacing(size.lineSpacing)
      }
      .frame(maxWidth: .infinity, alignment: .leading)

    case .code(let content):
      Text(content)
        .font(.system(size: size.pointSize - 1, design: .monospaced))
        .textSelection(.enabled)
        .padding(9)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 7))

    case .paragraph(let content):
      paragraph(content)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
  }

  /// 段落：有公式就走混排，没有就照旧一整段 `Text`。
  ///
  /// 分开是有理由的——混排要把文字切成词才折得了行，而那会让没有公式的普通段落
  /// 白白多做一遍布局，还可能改变标点的断行位置。绝大多数段落里没有公式。
  @ViewBuilder
  private func paragraph(_ content: String) -> some View {
    let segments = typesetsFormulas ? MathTypesetting.segments(in: content) : []
    if segments.contains(where: { if case .text = $0 { false } else { true } }) {
      MathTextLine(
        segments: segments,
        size: size.pointSize,
        lineSpacing: size.lineSpacing
      )
    } else {
      inline(content)
        .font(.system(size: size.pointSize))
        .lineSpacing(size.lineSpacing)
    }
  }

  private func inline(_ raw: String) -> Text {
    let escaped = MathMarkdown.escapingMathOperators(raw)
    guard
      let attributed = try? AttributedString(
        markdown: escaped,
        options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
      )
    else {
      return Text(raw)
    }
    return Text(attributed)
  }
}

/// 用户自己发的消息。不做 Markdown——他打的是什么就显示什么。
struct UserMessageText: View {
  let text: String

  @AppStorage(AppSettingsKey.messageTextSize)
  private var textSize = MessageTextSize.medium.rawValue

  var body: some View {
    let size = MessageTextSize(rawValue: textSize) ?? .medium
    Text(text)
      .font(.system(size: size.pointSize))
      .lineSpacing(size.lineSpacing)
      .textSelection(.enabled)
  }
}
