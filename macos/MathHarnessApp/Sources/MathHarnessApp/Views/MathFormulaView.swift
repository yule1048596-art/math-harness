import MathHarnessCore
import SwiftUI

/// 把排版树画出来。
///
/// 解析在 `MathHarnessCore.MathTypesetting` 里（纯函数，断言钉着），这里只管摆位置：
/// 分式上下叠、角标缩小偏移、根号加一道横线、大算符把上下限摆在符号上下。
///
/// **认不出来的公式根本到不了这里**——解析失败的片段以原文形式显示，不会有半懂半猜
/// 的渲染结果。
struct MathFormulaView: View {
  let node: MathTypesetting.Node
  let size: CGFloat

  var body: some View {
    content
  }

  /// 递归发生在这里，而且必须过一次 `AnyView`。
  ///
  /// SwiftUI 的 `some View` 是不透明类型：一个视图的 body 里直接放同类型的自己，
  /// 类型就用自己定义自己，编译不过。排版树很小，这层擦除的代价可以忽略。
  private func child(_ node: MathTypesetting.Node, _ size: CGFloat) -> AnyView {
    AnyView(MathFormulaView(node: node, size: size))
  }

  @ViewBuilder
  private var content: some View {
    switch node {
    case .text(let value):
      Text(value)
        .font(.system(size: size, design: .serif))
        .italic(isVariable(value))

    case .op(let value):
      Text(value)
        .font(.system(size: size, design: .serif))
        // 留白要跟着字号缩。固定 1.5pt 在缩小的角标里显得过宽，`x^{n-1}` 会散成
        // `n - 1`，看着像三个独立的符号。
        .padding(.horizontal, size * 0.1)

    case .row(let items):
      HStack(alignment: .firstTextBaseline, spacing: 1) {
        ForEach(Array(items.enumerated()), id: \.offset) { _, item in
          child(item, size)
        }
      }

    case .fraction(let numerator, let denominator):
      VStack(spacing: 1) {
        child(numerator, size * 0.92)
        Rectangle()
          .frame(height: max(0.8, size * 0.06))
        child(denominator, size * 0.92)
      }
      .fixedSize()
      // 分数线要落在这一行的中线附近，否则整行看着往上飘。
      .alignmentGuide(.firstTextBaseline) { context in
        context[VerticalAlignment.center] + size * 0.35
      }

    case .script(let base, let superscript, let subscriptNode):
      // 角标必须**离开基线**，靠 padding 是挪不动的：`firstTextBaseline` 对齐的是
      // 基线本身，加内边距只会在外面留白。实测过一版 padding 的写法，`x^{n}` 画出来
      // 和 `x_n` 一模一样——上标显示成下标，是那种不会报错的错。
      HStack(alignment: .firstTextBaseline, spacing: 0) {
        child(base, size)
        if let superscript, let subscriptNode {
          VStack(alignment: .leading, spacing: size * 0.04) {
            child(superscript, size * 0.7)
            child(subscriptNode, size * 0.7)
          }
          .alignmentGuide(.firstTextBaseline) { context in
            context[VerticalAlignment.center] + size * 0.22
          }
        } else if let superscript {
          child(superscript, size * 0.7)
            .alignmentGuide(.firstTextBaseline) { context in
              context[.firstTextBaseline] + size * 0.42
            }
        } else if let subscriptNode {
          child(subscriptNode, size * 0.7)
            .alignmentGuide(.firstTextBaseline) { context in
              context[.firstTextBaseline] - size * 0.16
            }
        }
      }

    case .radical(let radicand, let index):
      HStack(alignment: .firstTextBaseline, spacing: 0) {
        if let index {
          child(index, size * 0.62)
            .padding(.trailing, -size * 0.18)
            .padding(.bottom, size * 0.34)
        }
        Text("√")
          .font(.system(size: size * 1.15, design: .serif))
        VStack(spacing: 1) {
          Rectangle().frame(height: max(0.8, size * 0.05))
          child(radicand, size)
        }
        .fixedSize()
      }

    case .bigOperator(let symbol, let lower, let upper):
      VStack(spacing: 0) {
        if let upper {
          child(upper, size * 0.62)
        }
        Text(symbol)
          .font(.system(size: symbol.count == 1 ? size * 1.5 : size, design: .serif))
        if let lower {
          child(lower, size * 0.62)
        }
      }
      .fixedSize()
      .padding(.horizontal, 2)
      .alignmentGuide(.firstTextBaseline) { context in
        context[VerticalAlignment.center] + size * 0.35
      }

    case .fenced(let open, let close, let body):
      HStack(alignment: .firstTextBaseline, spacing: 0) {
        Text(open).font(.system(size: size, design: .serif))
        child(body, size)
        Text(close).font(.system(size: size, design: .serif))
      }

    case .matrix(let open, let close, let rows):
      HStack(alignment: .center, spacing: 2) {
        if !open.isEmpty { bracket(open, rowCount: rows.count) }
        VStack(alignment: .center, spacing: size * 0.25) {
          ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
            HStack(alignment: .firstTextBaseline, spacing: size * 0.7) {
              ForEach(Array(row.enumerated()), id: \.offset) { _, cell in
                child(cell, size)
              }
            }
          }
        }
        if !close.isEmpty { bracket(close, rowCount: rows.count) }
      }
      .fixedSize()
      .alignmentGuide(.firstTextBaseline) { context in
        context[VerticalAlignment.center] + size * 0.35
      }

    case .boxed(let body):
      child(body, size)
        .padding(.horizontal, size * 0.35)
        .padding(.vertical, size * 0.22)
        .overlay {
          RoundedRectangle(cornerRadius: 3)
            .stroke(.secondary, lineWidth: max(0.8, size * 0.045))
        }
        .fixedSize()
    }
  }

  private func bracket(_ symbol: String, rowCount: Int) -> some View {
    Text(symbol)
      .font(.system(size: size, design: .serif))
      .scaleEffect(x: 1, y: max(1, CGFloat(rowCount) * 1.4), anchor: .center)
  }

  /// 单个拉丁字母按数学惯例排成斜体；函数名、数字和中文不斜。
  private func isVariable(_ value: String) -> Bool {
    guard value.count == 1, let character = value.first else { return false }
    return character.isLetter && character.isASCII
  }
}

/// 一行正文：文字与公式混排。
///
/// 行内公式跟着文字走，`$$` 那种独占一行。文字部分照旧走 Markdown 的行内渲染，两套
/// 记号各管各的——先由 `MathTypesetting` 把公式切出来，剩下的才交给 Markdown。
struct MathTextLine: View {
  let segments: [MathTypesetting.Segment]
  let size: CGFloat
  let lineSpacing: CGFloat

  var body: some View {
    // 只有一条 display 公式时独占一行居中，其余情况按行内混排。
    if segments.count == 1, case .formula(let node, true, _) = segments[0] {
      MathFormulaView(node: node, size: size * 1.1)
        .frame(maxWidth: .infinity, alignment: .center)
        .padding(.vertical, 2)
    } else {
      WrappingSegments(segments: segments, size: size, lineSpacing: lineSpacing)
    }
  }
}

/// 行内混排。
///
/// 用 `HStack` 会让长句子撑破宽度而不换行，所以文字段落仍然交给 `Text` 自己折行，
/// 公式作为独立单元夹在中间——公式本身不折行（数学式折行比不折行更难读）。
private struct WrappingSegments: View {
  let segments: [MathTypesetting.Segment]
  let size: CGFloat
  let lineSpacing: CGFloat

  var body: some View {
    FlowLayout(spacing: 0, lineSpacing: lineSpacing) {
      ForEach(Array(segments.enumerated()), id: \.offset) { _, segment in
        switch segment {
        case .text(let value):
          ForEach(Array(words(in: value).enumerated()), id: \.offset) { _, word in
            inlineMarkdown(word)
              .font(.system(size: size))
          }
        case .rawFormula(let source):
          Text(source)
            .font(.system(size: size, design: .monospaced))
        case .formula(let node, _, _):
          MathFormulaView(node: node, size: size)
        }
      }
    }
  }

  /// 按可断行的位置切开：西文按空格，中文按字。
  ///
  /// 这是为了让 `FlowLayout` 有地方折行。整段文字塞成一个 `Text` 的话，一行里只要
  /// 有公式，整段就不折了。
  private func words(in value: String) -> [String] {
    var pieces: [String] = []
    var current = ""
    for character in value {
      if character.isASCII, !character.isWhitespace {
        current.append(character)
        continue
      }
      if !current.isEmpty {
        pieces.append(current)
        current = ""
      }
      pieces.append(String(character))
    }
    if !current.isEmpty { pieces.append(current) }
    return pieces
  }

  private func inlineMarkdown(_ raw: String) -> Text {
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

/// 会折行的横向布局。
private struct FlowLayout: Layout {
  let spacing: CGFloat
  let lineSpacing: CGFloat

  func sizeThatFits(
    proposal: ProposedViewSize, subviews: Subviews, cache: inout ()
  ) -> CGSize {
    let width = proposal.width ?? .infinity
    let rows = arrange(subviews: subviews, width: width)
    let height =
      rows.reduce(0) { $0 + $1.height } + lineSpacing
      * CGFloat(max(0, rows.count - 1))
    return CGSize(width: proposal.width ?? rows.map(\.width).max() ?? 0, height: height)
  }

  func placeSubviews(
    in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()
  ) {
    let rows = arrange(subviews: subviews, width: bounds.width)
    var y = bounds.minY
    for row in rows {
      var x = bounds.minX
      for item in row.items {
        let size = subviews[item].sizeThatFits(.unspecified)
        subviews[item].place(
          at: CGPoint(x: x, y: y + (row.height - size.height) / 2),
          proposal: ProposedViewSize(size)
        )
        x += size.width + spacing
      }
      y += row.height + lineSpacing
    }
  }

  private struct Row {
    var items: [Int] = []
    var width: CGFloat = 0
    var height: CGFloat = 0
  }

  private func arrange(subviews: Subviews, width: CGFloat) -> [Row] {
    var rows: [Row] = []
    var current = Row()
    for index in subviews.indices {
      let size = subviews[index].sizeThatFits(.unspecified)
      if !current.items.isEmpty, current.width + spacing + size.width > width {
        rows.append(current)
        current = Row()
      }
      if !current.items.isEmpty { current.width += spacing }
      current.items.append(index)
      current.width += size.width
      current.height = max(current.height, size.height)
    }
    if !current.items.isEmpty { rows.append(current) }
    return rows
  }
}
