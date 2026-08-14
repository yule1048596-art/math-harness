import Foundation

/// 把 LaTeX 的一个**有界子集**解析成排版树。
///
/// 一条不让步的规则：**任何一处解析不了，整条公式退回原文显示**。不做部分渲染——
/// 半懂半猜地渲出来，丢掉的那个符号不会有任何提示，而这是数学软件。这和 v0.18 的
/// `MathMarkdown` 是同一条原则：宁可少认，不可认错。
///
/// 子集按真实回答里的出现频率取，不追求完整。认不出来的命令一律让整条公式落回原文，
/// 用户至少还看得见他写的是什么。
public enum MathTypesetting {
  /// 排版树的一个节点。
  public indirect enum Node: Equatable, Sendable {
    /// 一串普通字符（变量、数字、已经解开的符号）。
    case text(String)
    /// 运算符或关系符，两侧要留空。
    case op(String)
    /// 横向依次排开。
    case row([Node])
    /// 分式。
    case fraction(numerator: Node, denominator: Node)
    /// 上标、下标，或两者都有。
    case script(base: Node, superscript: Node?, subscriptNode: Node?)
    /// 根号。`index` 为空是平方根。
    case radical(radicand: Node, index: Node?)
    /// 带上下限的大算符，比如求和、积分、极限。
    case bigOperator(symbol: String, lower: Node?, upper: Node?)
    /// 括号包住的一组内容。
    case fenced(open: String, close: String, body: Node)
    /// 矩阵。`rows` 的每一行长度相同。
    case matrix(open: String, close: String, rows: [[Node]])
  }

  /// 一段正文里切出来的片段：要么是普通文字，要么是一条公式。
  public enum Segment: Equatable, Sendable {
    case text(String)
    /// `display` 为真表示它原本写在 `$$`／`\[` 里，应当独占一行居中。
    case formula(Node, display: Bool, source: String)
    /// 解析失败的公式。**原样带着定界符**，让用户看见他写的是什么。
    case rawFormula(String)
  }

  // MARK: - 切分

  /// 把一行正文切成文字与公式片段。
  ///
  /// 只认成对出现的定界符。落单的 `$` 在中文语境里常常是货币符号，当成公式起点会把
  /// 后面半句话都吞掉，所以配不上对的一律当普通文字。
  public static func segments(in line: String) -> [Segment] {
    var segments: [Segment] = []
    var plain = ""
    let characters = Array(line)
    var index = 0

    func flushPlain() {
      if !plain.isEmpty {
        segments.append(.text(plain))
        plain = ""
      }
    }

    while index < characters.count {
      guard
        let opened = openingDelimiter(characters, at: index),
        let closeIndex = findClosing(characters, from: index + opened.token.count, of: opened)
      else {
        plain.append(characters[index])
        index += 1
        continue
      }
      let bodyRange = (index + opened.token.count)..<closeIndex
      let body = String(characters[bodyRange])
      let source = String(characters[index..<(closeIndex + opened.closing.count)])
      flushPlain()
      if let node = parse(body) {
        segments.append(.formula(node, display: opened.display, source: source))
      } else {
        segments.append(.rawFormula(source))
      }
      index = closeIndex + opened.closing.count
    }
    flushPlain()
    return segments
  }

  private struct Delimiter {
    let token: String
    let closing: String
    let display: Bool
  }

  private static let delimiters = [
    Delimiter(token: "$$", closing: "$$", display: true),
    Delimiter(token: "\\[", closing: "\\]", display: true),
    Delimiter(token: "\\(", closing: "\\)", display: false),
    Delimiter(token: "$", closing: "$", display: false),
  ]

  private static func openingDelimiter(
    _ characters: [Character], at index: Int
  ) -> Delimiter? {
    for delimiter in delimiters {
      let token = Array(delimiter.token)
      guard index + token.count <= characters.count else { continue }
      if Array(characters[index..<(index + token.count)]) == token {
        return delimiter
      }
    }
    return nil
  }

  private static func findClosing(
    _ characters: [Character], from start: Int, of delimiter: Delimiter
  ) -> Int? {
    let closing = Array(delimiter.closing)
    guard !closing.isEmpty, start < characters.count else { return nil }
    var index = start
    while index + closing.count <= characters.count {
      if Array(characters[index..<(index + closing.count)]) == closing {
        // 空公式（`$$`）不算公式。
        return index > start ? index : nil
      }
      index += 1
    }
    return nil
  }

  // MARK: - 解析

  /// 解析一段公式源码。认不出来的任何一处都让整条返回 nil。
  public static func parse(_ source: String) -> Node? {
    var parser = Parser(source: Array(source))
    guard let node = parser.parseRow(until: nil), parser.isAtEnd else { return nil }
    return node
  }

  private struct Parser {
    let source: [Character]
    var index = 0

    var isAtEnd: Bool { index >= source.count }

    mutating func parseRow(until terminator: Character?) -> Node? {
      var items: [Node] = []
      while index < source.count {
        let character = source[index]
        if let terminator, character == terminator { break }
        if character == "}" || character == "]" && terminator == nil {
          // 多出来的收尾括号：结构对不上，整条作废。
          return nil
        }
        guard let node = parseAtomWithScripts() else { return nil }
        items.append(node)
      }
      if items.isEmpty { return .row([]) }
      return items.count == 1 ? items[0] : .row(items)
    }

    /// 一个原子，外加可能跟在后面的上下标。
    mutating func parseAtomWithScripts() -> Node? {
      guard let base = parseAtom() else { return nil }
      var superscript: Node?
      var subscriptNode: Node?
      while index < source.count, source[index] == "^" || source[index] == "_" {
        let isSuper = source[index] == "^"
        index += 1
        guard let script = parseScriptArgument() else { return nil }
        if isSuper {
          guard superscript == nil else { return nil }
          superscript = script
        } else {
          guard subscriptNode == nil else { return nil }
          subscriptNode = script
        }
      }
      if superscript == nil, subscriptNode == nil { return base }
      // 大算符的上下标是**上下限**，不是角标——`\sum_{k=1}^{n}` 要摆在符号上下。
      if case .bigOperator(let symbol, _, _) = base {
        return .bigOperator(symbol: symbol, lower: subscriptNode, upper: superscript)
      }
      return .script(base: base, superscript: superscript, subscriptNode: subscriptNode)
    }

    /// `^` `_` 后面的参数：一个花括号组，或者单个字符。
    mutating func parseScriptArgument() -> Node? {
      skipSpaces()
      guard index < source.count else { return nil }
      if source[index] == "{" { return parseGroup() }
      if source[index] == "\\" { return parseCommand() }
      let character = source[index]
      guard character != "^", character != "_", character != "}" else { return nil }
      index += 1
      return .text(String(character))
    }

    mutating func parseGroup() -> Node? {
      guard index < source.count, source[index] == "{" else { return nil }
      index += 1
      guard let body = parseRow(until: "}") else { return nil }
      guard index < source.count, source[index] == "}" else { return nil }
      index += 1
      return body
    }

    mutating func skipSpaces() {
      while index < source.count, source[index] == " " { index += 1 }
    }

    mutating func parseAtom() -> Node? {
      guard index < source.count else { return nil }
      let character = source[index]

      if character == " " {
        index += 1
        return .text(" ")
      }
      if character == "{" { return parseGroup() }
      if character == "\\" { return parseCommand() }
      if let fence = openFences[character] {
        index += 1
        guard let body = parseRow(until: fence.close) else { return nil }
        guard index < source.count, source[index] == fence.close else { return nil }
        index += 1
        return .fenced(open: String(character), close: String(fence.close), body: body)
      }
      if operators.contains(character) {
        index += 1
        return .op(String(character))
      }
      if character.isLetter || character.isNumber || character == "." || character == "," {
        var run = ""
        while index < source.count,
          source[index].isLetter || source[index].isNumber || source[index] == "."
            || source[index] == ","
        {
          run.append(source[index])
          index += 1
        }
        return .text(run)
      }
      // 认不出来的字符：整条作废。
      return nil
    }

    mutating func parseCommand() -> Node? {
      guard index < source.count, source[index] == "\\" else { return nil }
      index += 1
      var name = ""
      while index < source.count, source[index].isLetter {
        name.append(source[index])
        index += 1
      }
      if name.isEmpty {
        // `\\`、`\,` 这类：只认无歧义的几个。
        guard index < source.count else { return nil }
        let symbol = source[index]
        index += 1
        if symbol == "," || symbol == ";" || symbol == "!" { return .text(" ") }
        if symbol == "{" || symbol == "}" { return .text(String(symbol)) }
        return nil
      }
      return command(named: name)
    }

    mutating func command(named name: String) -> Node? {
      switch name {
      case "frac", "dfrac", "tfrac":
        skipSpaces()
        guard let numerator = parseGroup() else { return nil }
        skipSpaces()
        guard let denominator = parseGroup() else { return nil }
        return .fraction(numerator: numerator, denominator: denominator)

      case "sqrt":
        skipSpaces()
        var degree: Node?
        if index < source.count, source[index] == "[" {
          index += 1
          guard let inner = parseRow(until: "]") else { return nil }
          guard index < source.count, source[index] == "]" else { return nil }
          index += 1
          degree = inner
        }
        skipSpaces()
        guard let radicand = parseGroup() else { return nil }
        return .radical(radicand: radicand, index: degree)

      case "pmod":
        skipSpaces()
        guard let modulus = parseGroup() else { return nil }
        return .row([.text(" (mod "), modulus, .text(")")])

      case "text", "mathrm", "mathbf", "operatorname":
        skipSpaces()
        guard let body = parseGroup() else { return nil }
        return body

      case "left", "right":
        // 定界符大小提示。渲染时不需要它，但后面那个符号要当成普通括号读出来。
        skipSpaces()
        guard index < source.count else { return nil }
        let symbol = source[index]
        index += 1
        if symbol == "." { return .row([]) }
        return .op(String(symbol))

      case "begin":
        return parseEnvironment()

      case let name where bigOperators[name] != nil:
        return .bigOperator(symbol: bigOperators[name]!, lower: nil, upper: nil)

      case let name where symbols[name] != nil:
        return .text(symbols[name]!)

      case let name where namedOperators.contains(name):
        return .text(name)

      default:
        return nil
      }
    }

    /// 只认矩阵环境，而且必须自己配对收尾。
    mutating func parseEnvironment() -> Node? {
      guard let nameNode = parseGroup(), case .text(let name) = nameNode else {
        return nil
      }
      guard let fence = matrixEnvironments[name] else { return nil }
      var rows: [[Node]] = []
      var currentRow: [Node] = []
      var cell: [Character] = []

      func flushCell() -> Bool {
        var inner = Parser(source: cell)
        guard let node = inner.parseRow(until: nil), inner.isAtEnd else { return false }
        currentRow.append(node)
        cell = []
        return true
      }

      while index < source.count {
        if source[index] == "&" {
          index += 1
          guard flushCell() else { return nil }
          continue
        }
        if matches("\\\\") {
          index += 2
          guard flushCell() else { return nil }
          rows.append(currentRow)
          currentRow = []
          continue
        }
        if matches("\\end") {
          index += 4
          guard let endNode = parseGroup(), case .text(name) = endNode else {
            return nil
          }
          guard flushCell() else { return nil }
          if currentRow.contains(where: { $0 != .row([]) }) || rows.isEmpty {
            rows.append(currentRow)
          }
          guard !rows.isEmpty, rows.allSatisfy({ $0.count == rows[0].count }) else {
            return nil
          }
          return .matrix(open: fence.open, close: fence.close, rows: rows)
        }
        cell.append(source[index])
        index += 1
      }
      return nil
    }

    func matches(_ token: String) -> Bool {
      let characters = Array(token)
      guard index + characters.count <= source.count else { return false }
      return Array(source[index..<(index + characters.count)]) == characters
    }
  }

  // MARK: - 词表

  private static let openFences: [Character: (close: Character, Void)] = [
    "(": (")", ()),
    "[": ("]", ()),
  ]

  private static let operators: Set<Character> = [
    "+", "-", "*", "/", "=", "<", ">", "|", "!", ":", ";", "'",
  ]

  private static let bigOperators: [String: String] = [
    "sum": "∑", "prod": "∏", "int": "∫", "iint": "∬", "oint": "∮",
    "lim": "lim", "max": "max", "min": "min", "bigcup": "⋃", "bigcap": "⋂",
  ]

  private static let namedOperators: Set<String> = [
    "sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos", "arctan",
    "sinh", "cosh", "tanh", "log", "ln", "lg", "exp", "det", "dim", "deg",
    "gcd", "ker", "sup", "inf", "mod", "Re", "Im",
  ]

  private static let matrixEnvironments: [String: (open: String, close: String)] = [
    "matrix": ("", ""),
    "pmatrix": ("(", ")"),
    "bmatrix": ("[", "]"),
    "vmatrix": ("|", "|"),
    "Bmatrix": ("{", "}"),
  ]

  private static let symbols: [String: String] = [
    // 希腊字母
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ",
    "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ",
    "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π",
    "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    // 关系与运算
    "cdot": "·", "times": "×", "div": "÷", "pm": "±", "mp": "∓",
    "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "ne": "≠", "neq": "≠",
    "approx": "≈", "equiv": "≡", "sim": "∼", "propto": "∝",
    "to": "→", "rightarrow": "→", "leftarrow": "←", "Rightarrow": "⇒",
    "Leftarrow": "⇐", "iff": "⇔", "Leftrightarrow": "⇔", "mapsto": "↦",
    "infty": "∞", "partial": "∂", "nabla": "∇", "forall": "∀", "exists": "∃",
    "in": "∈", "notin": "∉", "subset": "⊂", "subseteq": "⊆", "supset": "⊃",
    "cup": "∪", "cap": "∩", "emptyset": "∅", "setminus": "∖",
    "land": "∧", "lor": "∨", "neg": "¬", "therefore": "∴", "because": "∵",
    "cdots": "⋯", "ldots": "…", "dots": "…", "vdots": "⋮", "ddots": "⋱",
    "angle": "∠", "perp": "⊥", "parallel": "∥", "circ": "∘", "prime": "′",
    "quad": "  ", "qquad": "    ",
    "mathbb": "", "displaystyle": "",
  ]
}
