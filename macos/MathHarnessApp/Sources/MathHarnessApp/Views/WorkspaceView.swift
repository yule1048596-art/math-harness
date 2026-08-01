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
  @State private var point = "oo"
  @State private var mode: VerificationMode = .asymptoticExpansion
  @State private var remainderPower = "2"

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
            TextField(
              "表达式，例如 sqrt(x**2+x)-x",
              text: $expression
            )
            .font(.body.monospaced())
            TextField("变量", text: $variable)
              .frame(width: 72)
            TextField("趋近点", text: $point)
              .frame(width: 90)
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
          HStack {
            Image(systemName: "checkmark.shield")
            Text("表达式使用受限 SymPy 语法；留空时仍可对话，但结果可能需要人工复核。")
            Spacer()
          }
          .font(.caption)
          .foregroundStyle(.secondary)
        }
        .padding(.top, 6)
      }
      .font(.caption)

      HStack {
        Text(model.isSolving ? "正在检索、求解并验证……" : "⌘↩ 发送")
          .font(.caption)
          .foregroundStyle(.secondary)
        Spacer()
        Button {
          submit()
        } label: {
          if model.isSolving {
            ProgressView()
              .controlSize(.small)
              .frame(width: 58)
          } else {
            Label("求解", systemImage: "arrow.up.circle.fill")
          }
        }
        .keyboardShortcut(.return, modifiers: [.command])
        .buttonStyle(.borderedProminent)
        .disabled(
          problem.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            || model.isSolving
        )
      }
    }
    .padding(.horizontal, 18)
    .padding(.vertical, 12)
    .background(.regularMaterial)
  }

  private func submit() {
    let target: SolveMathTargetRequest?
    let trimmedExpression = expression.trimmingCharacters(in: .whitespacesAndNewlines)
    if trimmedExpression.isEmpty {
      target = nil
    } else {
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
      target = SolveMathTargetRequest(
        expression: trimmedExpression,
        variable: variable.trimmingCharacters(in: .whitespacesAndNewlines),
        point: point.trimmingCharacters(in: .whitespacesAndNewlines),
        mode: mode,
        remainderPower: power
      )
    }

    let normalizedTags =
      tags
      .split(separator: ",")
      .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
      .filter { !$0.isEmpty }
    let submittedProblem = problem
    Task {
      if await model.solve(
        problem: submittedProblem,
        tags: normalizedTags,
        mathTarget: target
      ) {
        problem = ""
        expression = ""
      }
    }
  }
}
