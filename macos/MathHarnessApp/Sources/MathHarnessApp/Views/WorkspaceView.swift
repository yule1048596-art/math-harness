import MathHarnessCore
import SwiftUI

struct WorkspaceView: View {
  @EnvironmentObject private var model: AppModel

  var body: some View {
    VStack(spacing: 0) {
      workspaceHeader
      Divider()
      AttemptTimeline()
      Divider()
      SolveComposer()
    }
    .navigationTitle(model.selectedWorkspace?.name ?? "Math Harness")
  }

  private var workspaceHeader: some View {
    HStack(spacing: 12) {
      VStack(alignment: .leading, spacing: 3) {
        Text(model.selectedWorkspace?.name ?? "")
          .font(.headline)
        if let description = model.selectedWorkspace?.description,
          !description.isEmpty
        {
          Text(description)
            .font(.caption)
            .foregroundStyle(.secondary)
            .lineLimit(1)
        }
      }
      Spacer()
      if !model.pendingExamples.isEmpty {
        Label("\(model.pendingExamples.count) 待复核", systemImage: "tray.full")
          .font(.caption)
          .foregroundStyle(.orange)
      }
      Label("\(model.methods.count) 张方法卡", systemImage: "books.vertical")
        .font(.caption)
        .foregroundStyle(.secondary)
      Label("\(model.attempts.count) 次求解", systemImage: "bubble.left.and.text.bubble.right")
        .font(.caption)
        .foregroundStyle(.secondary)
    }
    .padding(.horizontal, 18)
    .frame(minHeight: 54)
  }
}

private struct AttemptTimeline: View {
  @EnvironmentObject private var model: AppModel

  var body: some View {
    ScrollViewReader { proxy in
      ScrollView {
        LazyVStack(spacing: 22) {
          if model.attempts.isEmpty {
            ContentUnavailableView {
              Label("开始第一次数学对话", systemImage: "sum")
            } description: {
              Text("输入题目；若提供可验证目标，Harness 会用 SymPy 独立验收答案。")
            }
            .frame(maxWidth: .infinity, minHeight: 300)
          } else {
            ForEach(model.attempts) { attempt in
              AttemptConversation(attempt: attempt)
                .id(attempt.id)
            }
          }
        }
        .padding(24)
        .frame(maxWidth: 860)
        .frame(maxWidth: .infinity)
      }
      .onChange(of: model.attempts.count) {
        if let id = model.attempts.last?.id {
          withAnimation { proxy.scrollTo(id, anchor: .bottom) }
        }
      }
    }
  }
}

private struct AttemptConversation: View {
  @EnvironmentObject private var model: AppModel
  let attempt: SolutionAttempt

  var body: some View {
    VStack(spacing: 12) {
      HStack {
        Spacer(minLength: 90)
        Text(attempt.problem)
          .textSelection(.enabled)
          .padding(.horizontal, 14)
          .padding(.vertical, 10)
          .background(.tint.opacity(0.14), in: RoundedRectangle(cornerRadius: 14))
      }

      HStack(alignment: .top, spacing: 10) {
        Image(systemName: "function")
          .font(.headline)
          .foregroundStyle(.tint)
          .frame(width: 30, height: 30)
          .background(.tint.opacity(0.10), in: Circle())

        VStack(alignment: .leading, spacing: 12) {
          HStack {
            Text("Math Harness")
              .font(.subheadline.weight(.semibold))
            StatusBadge(status: attempt.status)
            Spacer()
            Text(attempt.generation.model ?? attempt.generation.provider)
              .font(.caption2)
              .foregroundStyle(.tertiary)
          }

          if let candidate = attempt.candidate {
            Text(candidate.answerText)
              .textSelection(.enabled)

            if !candidate.steps.isEmpty {
              VStack(alignment: .leading, spacing: 8) {
                ForEach(Array(candidate.steps.enumerated()), id: \.offset) { index, step in
                  HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text("\(index + 1)")
                      .font(.caption2.monospacedDigit())
                      .foregroundStyle(.secondary)
                      .frame(width: 18, height: 18)
                      .background(.quaternary, in: Circle())
                    VStack(alignment: .leading, spacing: 3) {
                      Text(step.explanation)
                      if let expression = step.expression, !expression.isEmpty {
                        Text(expression)
                          .font(.body.monospaced())
                          .foregroundStyle(.secondary)
                          .textSelection(.enabled)
                      }
                    }
                  }
                }
              }
            }
          } else {
            Text(attempt.generation.error ?? attempt.verification.summary)
              .foregroundStyle(.secondary)
          }

          Divider()
          HStack(alignment: .top, spacing: 8) {
            Image(systemName: verificationIcon)
              .foregroundStyle(verificationColor)
            VStack(alignment: .leading, spacing: 3) {
              Text(attempt.verification.summary)
                .font(.caption)
              if model.isAttemptCaptured(attempt.id) {
                Label("已自动记入知识草稿", systemImage: "tray.and.arrow.down.fill")
                  .font(.caption2)
                  .foregroundStyle(.secondary)
              }
              if !attempt.recommendedMethods.isEmpty {
                Text(
                  "检索方法："
                    + attempt.recommendedMethods
                    .map(\.method.name)
                    .joined(separator: "、")
                )
                .font(.caption2)
                .foregroundStyle(.secondary)
              }
            }
          }
        }
        .padding(14)
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 14))
        Spacer(minLength: 40)
      }
    }
  }

  private var verificationIcon: String {
    attempt.verification.status == "verified"
      ? "checkmark.seal.fill"
      : "exclamationmark.triangle.fill"
  }

  private var verificationColor: Color {
    attempt.verification.status == "verified" ? .green : .orange
  }
}

private struct SolveComposer: View {
  @EnvironmentObject private var model: AppModel
  @State private var problem = ""
  @State private var tags = ""
  @State private var showingTarget = true
  @State private var expression = ""
  @State private var variable = "x"
  @State private var parameters = ""
  @State private var assumptions = ""
  @State private var point = "oo"
  @State private var direction = "two_sided"
  @State private var mode: VerificationMode = .asymptoticExpansion
  @State private var remainderPower = "2"
  @State private var targetDraftSummary: String?
  @State private var targetDraftWarnings: [String] = []
  @State private var draftedProblem: String?
  @State private var awaitingTargetConfirmation = false

  var body: some View {
    VStack(spacing: 10) {
      TextEditor(text: $problem)
        .font(.body)
        .scrollContentBackground(.hidden)
        .frame(minHeight: 54, maxHeight: 110)
        .padding(8)
        .background(.background, in: RoundedRectangle(cornerRadius: 10))
        .overlay {
          RoundedRectangle(cornerRadius: 10)
            .stroke(.separator, lineWidth: 1)
        }
        .overlay(alignment: .topLeading) {
          if problem.isEmpty {
            Text("输入数学问题……")
              .foregroundStyle(.tertiary)
              .padding(.horizontal, 13)
              .padding(.vertical, 16)
              .allowsHitTesting(false)
          }
        }

      DisclosureGroup("可验证数学目标", isExpanded: $showingTarget) {
        VStack(spacing: 8) {
          HStack {
            Button {
              requestTargetDraft()
            } label: {
              if model.isDraftingTarget {
                ProgressView()
                  .controlSize(.mini)
              } else {
                Label("自动整理", systemImage: "wand.and.stars")
              }
            }
            .buttonStyle(.bordered)
            .disabled(
              problem.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                || model.isDraftingTarget || model.isSolving
            )
            TextField(
              "表达式，例如 sqrt(x**2+x)-x",
              text: $expression
            )
            .font(.body.monospaced())
          }
          HStack {
            TextField("变量", text: $variable)
              .frame(width: 72)
            TextField("参数（逗号分隔）", text: $parameters)
              .frame(minWidth: 130)
            TextField("趋近点", text: $point)
              .frame(width: 90)
            Picker("方向", selection: $direction) {
              Text("双侧").tag("two_sided")
              Text("左侧").tag("left")
              Text("右侧").tag("right")
            }
            .frame(width: 150)
          }
          HStack {
            Picker("验算模式", selection: $mode) {
              ForEach(VerificationMode.allCases) { item in
                Text(item.displayName).tag(item)
              }
            }
            .frame(maxWidth: 240)
            if mode == .asymptoticExpansion {
              TextField("余项阶数", text: $remainderPower)
                .frame(width: 110)
            }
            TextField("标签（逗号分隔）", text: $tags)
            Spacer()
          }
          TextField(
            "假设，例如 a:positive; n:integer（可选）",
            text: $assumptions
          )
          if let targetDraftSummary {
            VStack(alignment: .leading, spacing: 3) {
              Label(
                awaitingTargetConfirmation
                  ? "\(targetDraftSummary) 请检查后确认求解。"
                  : targetDraftSummary,
                systemImage: awaitingTargetConfirmation
                  ? "checkmark.bubble" : "info.bubble"
              )
              ForEach(targetDraftWarnings, id: \.self) { warning in
                Text("• \(warning)")
              }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .foregroundStyle(awaitingTargetConfirmation ? .blue : .orange)
          }
          HStack {
            Image(systemName: "checkmark.shield")
            Text("自动整理只生成建议稿；表达式、变量、趋近点和假设均由你确认后才会用于验算。")
            Spacer()
          }
          .font(.caption)
          .foregroundStyle(.secondary)
        }
        .padding(.top, 6)
      }
      .font(.caption)

      HStack {
        Text(
          model.isSolving
            ? "正在检索、求解并验证……"
            : model.isDraftingTarget ? "正在整理可验证目标……" : "⌘↩ 发送"
        )
        .font(.caption)
        .foregroundStyle(.secondary)
        Spacer()
        Button {
          submit()
        } label: {
          if model.isSolving || model.isDraftingTarget {
            ProgressView()
              .controlSize(.small)
              .frame(width: 58)
          } else {
            Label(
              awaitingTargetConfirmation
                ? "确认并求解"
                : isUnstructuredContinuation ? "继续非结构化" : "求解",
              systemImage: awaitingTargetConfirmation
                ? "checkmark.shield.fill"
                : isUnstructuredContinuation
                  ? "bubble.left.and.text.bubble.right" : "arrow.up.circle.fill"
            )
          }
        }
        .keyboardShortcut(.return, modifiers: [.command])
        .buttonStyle(.borderedProminent)
        .disabled(
          problem.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            || model.isSolving
            || model.isDraftingTarget
        )
      }
    }
    .padding(.horizontal, 18)
    .padding(.vertical, 12)
    .background(.regularMaterial)
    .onChange(of: problem) { _, _ in
      if awaitingTargetConfirmation {
        resetTargetFields()
      }
      draftedProblem = nil
      awaitingTargetConfirmation = false
      targetDraftSummary = nil
      targetDraftWarnings = []
    }
  }

  private var isUnstructuredContinuation: Bool {
    let trimmedProblem = problem.trimmingCharacters(in: .whitespacesAndNewlines)
    return draftedProblem == trimmedProblem
      && expression.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
      && targetDraftSummary != nil
  }

  private func submit() {
    let submittedProblem = problem.trimmingCharacters(in: .whitespacesAndNewlines)
    let trimmedExpression = expression.trimmingCharacters(in: .whitespacesAndNewlines)
    if trimmedExpression.isEmpty, draftedProblem != submittedProblem {
      requestTargetDraft()
      return
    }

    let target: SolveMathTargetRequest?
    if trimmedExpression.isEmpty {
      target = nil
    } else {
      let trimmedVariable = variable.trimmingCharacters(in: .whitespacesAndNewlines)
      guard !trimmedVariable.isEmpty else {
        model.errorMessage = "数学目标的变量不能为空。"
        return
      }
      let power: Int?
      if mode == .asymptoticExpansion {
        guard let parsed = Int(remainderPower), (1...50).contains(parsed) else {
          model.errorMessage = "余项阶数必须是 1 到 50 之间的整数。"
          return
        }
        power = parsed
      } else {
        power = nil
      }
      let normalizedParameters = parseCommaSeparated(parameters)
      guard
        let parsedAssumptions = parseAssumptions(
          assumptions,
          allowedSymbols: Set([trimmedVariable] + normalizedParameters)
        )
      else {
        return
      }
      target = SolveMathTargetRequest(
        expression: trimmedExpression,
        variable: trimmedVariable,
        parameters: normalizedParameters,
        assumptions: parsedAssumptions,
        point: point.trimmingCharacters(in: .whitespacesAndNewlines),
        direction: direction,
        mode: mode,
        remainderPower: power
      )
    }

    let normalizedTags =
      tags
      .split(separator: ",")
      .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
      .filter { !$0.isEmpty }
    Task {
      if await model.solve(
        problem: submittedProblem,
        tags: normalizedTags,
        mathTarget: target
      ) {
        problem = ""
        resetTargetFields()
      }
    }
  }

  private func requestTargetDraft() {
    let submittedProblem = problem.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !submittedProblem.isEmpty else {
      model.errorMessage = "请先输入数学问题。"
      return
    }
    Task {
      guard let result = await model.draftMathTarget(problem: submittedProblem) else {
        return
      }
      guard problem.trimmingCharacters(in: .whitespacesAndNewlines) == submittedProblem else {
        return
      }
      draftedProblem = submittedProblem
      targetDraftSummary = result.summary
      targetDraftWarnings = result.warnings
      guard let target = result.target else {
        awaitingTargetConfirmation = false
        showingTarget = true
        return
      }
      applyTargetDraft(target)
      awaitingTargetConfirmation = true
      showingTarget = true
    }
  }

  private func applyTargetDraft(_ target: SolveMathTargetRequest) {
    expression = target.expression
    variable = target.variable
    parameters = target.parameters.joined(separator: ", ")
    assumptions = target.assumptions
      .keys.sorted()
      .map { key in "\(key):\(target.assumptions[key, default: []].joined(separator: ","))" }
      .joined(separator: "; ")
    point = target.point
    direction = target.direction
    mode = target.mode
    remainderPower = target.remainderPower.map(String.init) ?? ""
  }

  private func parseCommaSeparated(_ value: String) -> [String] {
    var seen = Set<String>()
    return
      value
      .split(separator: ",")
      .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
      .filter { !$0.isEmpty && seen.insert($0).inserted }
  }

  private func parseAssumptions(
    _ value: String,
    allowedSymbols: Set<String>
  ) -> [String: [String]]? {
    let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
    if trimmed.isEmpty { return [:] }
    let allowedProperties: Set<String> = [
      "real", "positive", "negative", "nonzero", "integer",
      "nonnegative", "nonpositive",
    ]
    var result: [String: [String]] = [:]
    for rawClause in trimmed.split(separator: ";") {
      let parts = rawClause.split(separator: ":", maxSplits: 1)
      guard parts.count == 2 else {
        model.errorMessage = "假设格式应为 a:positive; n:integer。"
        return nil
      }
      let symbol = parts[0].trimmingCharacters(in: .whitespacesAndNewlines)
      guard allowedSymbols.contains(symbol) else {
        model.errorMessage = "假设中的符号 \(symbol) 必须先声明为变量或参数。"
        return nil
      }
      let properties = parts[1]
        .split(separator: ",")
        .map { $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() }
        .filter { !$0.isEmpty }
      guard !properties.isEmpty, properties.allSatisfy(allowedProperties.contains) else {
        model.errorMessage = "假设属性只支持 positive、integer、nonzero 等受限值。"
        return nil
      }
      result[symbol] = Array(Set(properties)).sorted()
    }
    return result
  }

  private func resetTargetFields() {
    expression = ""
    variable = "x"
    parameters = ""
    assumptions = ""
    point = "oo"
    direction = "two_sided"
    mode = .asymptoticExpansion
    remainderPower = "2"
    draftedProblem = nil
    awaitingTargetConfirmation = false
    targetDraftSummary = nil
    targetDraftWarnings = []
  }
}
