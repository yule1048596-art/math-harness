import Foundation

/// 回答正文的轻量 Markdown 处理。
///
/// 模型的数学回答里同时存在两套用法完全冲突的记号：
///
/// - Markdown 的 `**加粗**`、`_强调_`、`### 标题`、`- 列表`；
/// - 数学的 `x**2`、`a*b`、`x_1`。
///
/// 直接丢给 Markdown 解析器，`x**2 + y**2` 会变成 `x<strong>2 + y</strong>2`——公式里的
/// 星号凭空消失，而用户根本看不出发生了什么。**在一个数学软件里，这比不渲染糟得多。**
///
/// 这里的做法是：先把明显是运算符的 `*` 和 `_` 转义掉，再按块解析。判据是**两侧都紧贴
/// ASCII 操作数**——`x**2` 是，`**重点**内容` 不是（中文两侧），`设 **a** 为` 也不是。
public enum MathMarkdown {
  /// 一段正文拆出来的块。
  public enum Block: Equatable, Sendable {
    /// 普通段落。
    case paragraph(String)
    /// 标题，`level` 是井号个数。
    case heading(level: Int, text: String)
    /// 列表项，`marker` 是要显示的项目符号（`•` 或 `1.`）。
    case listItem(marker: String, text: String)
    /// 围栏代码块，整段原样保留。
    case code(String)
    /// 空行。渲染成一点纵向间距。
    case blank
  }

  /// 把正文拆成块。
  ///
  /// 只认最常见的四种结构。认不出来的一律当普通段落——**宁可少认，不可认错**：
  /// 认错会把一行数学变成别的东西，少认只是显示成一行普通文字。
  public static func blocks(from text: String) -> [Block] {
    var blocks: [Block] = []
    var codeLines: [String] = []
    var inCode = false

    for rawLine in joiningDisplayFormulas(text.components(separatedBy: .newlines)) {
      let trimmed = rawLine.trimmingCharacters(in: .whitespaces)
      if trimmed.hasPrefix("```") {
        if inCode {
          blocks.append(.code(codeLines.joined(separator: "\n")))
          codeLines = []
        }
        inCode.toggle()
        continue
      }
      if inCode {
        codeLines.append(rawLine)
        continue
      }
      if trimmed.isEmpty {
        blocks.append(.blank)
        continue
      }
      if let heading = headingBlock(trimmed) {
        blocks.append(heading)
        continue
      }
      if let item = listItemBlock(trimmed) {
        blocks.append(item)
        continue
      }
      blocks.append(.paragraph(trimmed))
    }
    // 代码块没闭合就到了结尾：已经收进去的行照样显示，不能整段丢掉。
    if inCode, !codeLines.isEmpty {
      blocks.append(.code(codeLines.joined(separator: "\n")))
    }
    return blocks
  }

  /// 行间公式常常跨行写：`\[` 独占一行，公式在中间，`\]` 又独占一行。
  ///
  /// 块切分是按行走的，这样的公式会被切成三块，哪一块都找不到自己的另一半定界符,
  /// 于是整条落回普通文字——而 Markdown 又把 `\[` 当成转义的方括号，屏幕上只剩一个
  /// `[`，公式源码原样摊在那里。所以在切块之前先把它们合成一行。
  ///
  /// 合不拢的一律原样返回（**不吞行**）：没有闭合定界符、中间夹了空行、或者长得离谱，
  /// 都说明它多半不是一条公式。少认只是显示成几行普通文字，认错会让用户丢掉内容。
  private static func joiningDisplayFormulas(_ lines: [String]) -> [String] {
    var result: [String] = []
    var index = 0
    var inCode = false

    while index < lines.count {
      let line = lines[index]
      if line.trimmingCharacters(in: .whitespaces).hasPrefix("```") {
        inCode.toggle()
      } else if !inCode, let joined = joinedDisplayFormula(lines, at: index) {
        result.append(joined.line)
        index = joined.nextIndex
        continue
      }
      result.append(line)
      index += 1
    }
    return result
  }

  /// 行间公式的定界符。行内的 `\(...\)`、`$...$` 不在此列：它们本来就写在一行里。
  private static let displayDelimiters = [("\\[", "\\]"), ("$$", "$$")]

  /// 一条行间公式最多跨多少行。**流式输出时每来一个字都要重排一遍**，没有上限的话
  /// 一个落单的 `\[` 会让每一帧都扫到正文结尾。
  private static let displayFormulaLineLimit = 40

  private static func joinedDisplayFormula(
    _ lines: [String], at start: Int
  ) -> (line: String, nextIndex: Int)? {
    let first = lines[start].trimmingCharacters(in: .whitespaces)
    guard
      let delimiter = displayDelimiters.first(where: { first.hasPrefix($0.0) })
    else { return nil }
    // 本行之内就收尾了：原来的逐行处理认得，不用合并。
    guard !first.dropFirst(delimiter.0.count).contains(delimiter.1) else { return nil }

    var parts = [first]
    var index = start + 1
    while index < lines.count, parts.count <= displayFormulaLineLimit {
      let trimmed = lines[index].trimmingCharacters(in: .whitespaces)
      // 行间公式里不该出现空行。出现了就说明这个 `\[` 根本不是公式的开头。
      guard !trimmed.isEmpty else { return nil }
      parts.append(trimmed)
      if trimmed.contains(delimiter.1) {
        return (parts.joined(separator: " "), index + 1)
      }
      index += 1
    }
    return nil
  }

  private static func headingBlock(_ line: String) -> Block? {
    var level = 0
    var index = line.startIndex
    while index < line.endIndex, line[index] == "#", level < 6 {
      level += 1
      index = line.index(after: index)
    }
    guard level > 0, index < line.endIndex, line[index] == " " else { return nil }
    let text = String(line[index...]).trimmingCharacters(in: .whitespaces)
    guard !text.isEmpty else { return nil }
    return .heading(level: level, text: text)
  }

  private static func listItemBlock(_ line: String) -> Block? {
    for bullet in ["- ", "* ", "+ "] where line.hasPrefix(bullet) {
      let text = String(line.dropFirst(bullet.count))
      return .listItem(marker: "•", text: text)
    }
    // `1. `、`2) ` 这类有序列表。数字后面必须跟分隔符和空格，否则 `2026 年……`
    // 这种以数字开头的正常句子会被当成列表。
    let digits = line.prefix { $0.isNumber }
    guard !digits.isEmpty, digits.count <= 3 else { return nil }
    let rest = line.dropFirst(digits.count)
    guard let separator = rest.first, separator == "." || separator == ")" else {
      return nil
    }
    let body = rest.dropFirst()
    guard body.hasPrefix(" ") else { return nil }
    let text = String(body.dropFirst())
    guard !text.isEmpty else { return nil }
    return .listItem(marker: "\(digits)\(separator)", text: text)
  }

  /// 把当作运算符用的 `*` 和 `_` 转义掉，让 Markdown 解析器别碰它们。
  ///
  /// 反引号里的内容原样保留：那已经是代码，再插反斜杠会真的显示出来。
  public static func escapingMathOperators(_ text: String) -> String {
    let characters = Array(text)
    var output = ""
    output.reserveCapacity(characters.count + 8)
    var index = 0

    while index < characters.count {
      let character = characters[index]

      if character == "`" {
        index = copyCodeSpan(characters, from: index, into: &output)
        continue
      }
      // 已经转义过的原样带走，不再叠一层反斜杠。
      if character == "\\", index + 1 < characters.count {
        output.append(character)
        output.append(characters[index + 1])
        index += 2
        continue
      }
      if character == "*" || character == "_" {
        var run = 0
        while index + run < characters.count, characters[index + run] == character {
          run += 1
        }
        let before = index > 0 ? characters[index - 1] : nil
        let after = index + run < characters.count ? characters[index + run] : nil
        if isASCIIOperand(before), isASCIIOperand(after) {
          for _ in 0..<run {
            output.append("\\")
            output.append(character)
          }
        } else {
          output.append(String(repeating: String(character), count: run))
        }
        index += run
        continue
      }
      output.append(character)
      index += 1
    }
    return output
  }

  /// 反引号跨度：连同定界符一起原样复制，返回下一个待处理的下标。
  private static func copyCodeSpan(
    _ characters: [Character],
    from start: Int,
    into output: inout String
  ) -> Int {
    var fenceLength = 0
    while start + fenceLength < characters.count, characters[start + fenceLength] == "`" {
      fenceLength += 1
    }
    let fence = String(repeating: "`", count: fenceLength)
    output.append(fence)

    var cursor = start + fenceLength
    while cursor < characters.count {
      guard characters[cursor] == "`" else {
        cursor += 1
        continue
      }
      var runEnd = cursor
      while runEnd < characters.count, characters[runEnd] == "`" { runEnd += 1 }
      if runEnd - cursor == fenceLength {
        output.append(String(characters[(start + fenceLength)..<cursor]))
        output.append(fence)
        return runEnd
      }
      cursor = runEnd
    }
    // 没有配对的收尾反引号：剩下的全部原样带走。
    output.append(String(characters[(start + fenceLength)...]))
    return characters.count
  }

  /// 紧贴着它就说明这个符号是运算符，不是强调标记。
  ///
  /// 只认 ASCII。数学表达式是 ASCII 写的（`x**2`、`a*b`、`n_1`），中文里的 `**加粗**`
  /// 两侧则是汉字或标点——这条界线把两者分得很干净，代价只是 `**bold**text` 这种
  /// 纯英文紧贴写法会显示成字面量。宁可多显示两个星号，也不能把公式改掉。
  private static func isASCIIOperand(_ character: Character?) -> Bool {
    guard let character, character.isASCII else { return false }
    if character.isLetter || character.isNumber { return true }
    return "()[]{}.".contains(character)
  }
}
