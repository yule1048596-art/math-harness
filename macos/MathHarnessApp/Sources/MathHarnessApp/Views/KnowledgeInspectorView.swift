import MathHarnessCore
import SwiftUI

private enum KnowledgePane: String, CaseIterable, Identifiable {
  case review = "待复核"
  case methods = "方法卡"

  var id: String { rawValue }
}

struct KnowledgeInspectorView: View {
  @EnvironmentObject private var model: AppModel
  @State private var query = ""
  @State private var pane: KnowledgePane = .review

  private var filteredMethods: [MethodCard] {
    let normalized = normalizedQuery
    guard !normalized.isEmpty else { return model.methods }
    return model.methods.filter {
      $0.name.lowercased().contains(normalized)
        || $0.key.lowercased().contains(normalized)
        || $0.tags.contains(where: { $0.lowercased().contains(normalized) })
    }
  }

  private var filteredExamples: [ProblemExample] {
    let normalized = normalizedQuery
    guard !normalized.isEmpty else { return model.pendingExamples }
    return model.pendingExamples.filter {
      $0.problem.lowercased().contains(normalized)
        || $0.solution.lowercased().contains(normalized)
        || $0.tags.contains(where: { $0.lowercased().contains(normalized) })
        || ($0.extraction?.extractedMethodKeys.contains {
          $0.lowercased().contains(normalized)
        } ?? false)
    }
  }

  private var normalizedQuery: String {
    query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
  }

  var body: some View {
    VStack(spacing: 0) {
      HStack {
        VStack(alignment: .leading, spacing: 2) {
          Text("知识库")
            .font(.headline)
          Text("\(model.pendingExamples.count) 待复核 · \(model.methods.count) 张方法卡")
            .font(.caption)
            .foregroundStyle(.secondary)
        }
        Spacer()
      }
      .padding(14)

      Picker("知识视图", selection: $pane) {
        ForEach(KnowledgePane.allCases) { item in
          Text(item.rawValue).tag(item)
        }
      }
      .pickerStyle(.segmented)
      .labelsHidden()
      .padding(.horizontal, 12)
      .padding(.bottom, 10)

      TextField(pane == .review ? "搜索待复核例题" : "搜索方法", text: $query)
        .textFieldStyle(.roundedBorder)
        .padding(.horizontal, 12)
        .padding(.bottom, 10)

      Divider()

      switch pane {
      case .review:
        reviewQueue
      case .methods:
        methodLibrary
      }
    }
  }

  @ViewBuilder
  private var reviewQueue: some View {
    if filteredExamples.isEmpty {
      ContentUnavailableView {
        Label(
          normalizedQuery.isEmpty ? "没有待复核草稿" : "没有匹配的草稿",
          systemImage: "checkmark.seal"
        )
      } description: {
        Text("每次成功求解都会自动进入这里；只有验证通过并经你确认后才会晋级。")
      }
    } else {
      ScrollView {
        LazyVStack(spacing: 10) {
          ForEach(filteredExamples) { example in
            KnowledgeDraftCard(example: example)
          }
        }
        .padding(12)
      }
    }
  }

  @ViewBuilder
  private var methodLibrary: some View {
    if filteredMethods.isEmpty {
      ContentUnavailableView {
        Label("还没有方法卡", systemImage: "books.vertical")
      } description: {
        Text("复核已验证例题后，方法会出现在这里。")
      }
    } else {
      ScrollView {
        LazyVStack(spacing: 10) {
          ForEach(filteredMethods) { method in
            MethodCardView(method: method)
          }
        }
        .padding(12)
      }
    }
  }
}

private struct KnowledgeDraftCard: View {
  @EnvironmentObject private var model: AppModel
  let example: ProblemExample
  @State private var expanded = false
  @State private var reviewerNote = ""
  @State private var confirmingRejection = false

  private var canApprove: Bool {
    example.verification.status == "verified"
  }

  private var isReviewing: Bool {
    model.reviewingExampleID == example.id
  }

  var body: some View {
    VStack(alignment: .leading, spacing: 10) {
      HStack(alignment: .top, spacing: 8) {
        VStack(alignment: .leading, spacing: 4) {
          Text(example.problem)
            .font(.subheadline.weight(.semibold))
            .lineLimit(expanded ? nil : 3)
            .textSelection(.enabled)
          Label(
            example.origin == "conversation" ? "对话自动记忆" : "手动录入",
            systemImage: example.origin == "conversation"
              ? "bubble.left.and.exclamationmark.bubble.right"
              : "square.and.pencil"
          )
          .font(.caption2)
          .foregroundStyle(.secondary)
        }
        Spacer()
        StatusBadge(status: example.verification.status)
      }

      Text(example.solution)
        .font(.caption)
        .foregroundStyle(.secondary)
        .lineLimit(expanded ? nil : 5)
        .textSelection(.enabled)

      if !example.methodDrafts.isEmpty {
        VStack(alignment: .leading, spacing: 3) {
          Text("准备写入的方法")
            .font(.caption2.weight(.semibold))
          ForEach(example.methodDrafts, id: \.key) { draft in
            VStack(alignment: .leading, spacing: 3) {
              Text(draft.name)
                .font(.caption.weight(.medium))
              Text(draft.key)
                .font(.caption2.monospaced())
                .foregroundStyle(.secondary)
              Text(draft.goal)
                .font(.caption2)
                .foregroundStyle(.secondary)
              if expanded, !draft.procedure.isEmpty {
                ForEach(Array(draft.procedure.enumerated()), id: \.offset) { index, step in
                  Text("\(index + 1). \(step)")
                    .font(.caption2)
                    .textSelection(.enabled)
                }
              }
            }
            .padding(7)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 7))
          }
        }
      } else {
        Label("已保留为例题，尚未提炼出明确方法", systemImage: "doc.text.magnifyingglass")
          .font(.caption2)
          .foregroundStyle(.secondary)
      }

      HStack(alignment: .top, spacing: 7) {
        Image(
          systemName: canApprove ? "checkmark.shield.fill" : "lock.trianglebadge.exclamationmark"
        )
        .foregroundStyle(canApprove ? .green : .orange)
        Text(
          canApprove
            ? "独立数学验证已通过；确认答案无误后可晋级。"
            : "缺少可验证数学目标，当前只能保留或驳回，不能晋级。"
        )
        .font(.caption2)
        .foregroundStyle(.secondary)
      }

      if expanded {
        TextField("复核意见（可选）", text: $reviewerNote, axis: .vertical)
          .textFieldStyle(.roundedBorder)
          .lineLimit(1...3)
      }

      HStack {
        Button(expanded ? "收起" : "展开复核") { expanded.toggle() }
          .buttonStyle(.plain)
        Spacer()
        if expanded {
          Button("驳回", role: .destructive) {
            confirmingRejection = true
          }
          .disabled(isReviewing)
          Button("确认晋级") {
            Task {
              await model.reviewExample(
                example,
                decision: .approve,
                reviewerNote: reviewerNote
              )
            }
          }
          .buttonStyle(.borderedProminent)
          .disabled(!canApprove || isReviewing)
        }
        if isReviewing {
          ProgressView()
            .controlSize(.small)
        }
      }
      .font(.caption)
    }
    .padding(12)
    .background(.background.secondary, in: RoundedRectangle(cornerRadius: 10))
    .overlay {
      RoundedRectangle(cornerRadius: 10)
        .stroke(.separator.opacity(0.5), lineWidth: 0.5)
    }
    .confirmationDialog(
      "驳回这条知识草稿？",
      isPresented: $confirmingRejection,
      titleVisibility: .visible
    ) {
      Button("驳回草稿", role: .destructive) {
        Task {
          await model.reviewExample(
            example,
            decision: .reject,
            reviewerNote: reviewerNote
          )
        }
      }
      Button("取消", role: .cancel) {}
    } message: {
      Text("草稿会保留在历史中，但不会进入正式方法库。")
    }
  }
}

private struct MethodCardView: View {
  @EnvironmentObject private var model: AppModel
  let method: MethodCard
  @State private var expanded = false

  var body: some View {
    VStack(alignment: .leading, spacing: 9) {
      HStack(alignment: .top) {
        VStack(alignment: .leading, spacing: 2) {
          Text(method.name)
            .font(.subheadline.weight(.semibold))
          Text(method.key)
            .font(.caption2.monospaced())
            .foregroundStyle(.secondary)
        }
        Spacer()
        StatusBadge(status: method.status)
      }

      Text(method.goal)
        .font(.caption)
        .foregroundStyle(.secondary)
        .lineLimit(expanded ? nil : 2)

      HStack(spacing: 12) {
        Label("\(method.successCount)", systemImage: "checkmark")
        Label("\(method.failureCount)", systemImage: "xmark")
        Text("v\(method.version)")
        Spacer()
        Button(expanded ? "收起" : "详情") { expanded.toggle() }
          .buttonStyle(.plain)
      }
      .font(.caption2)
      .foregroundStyle(.secondary)

      if expanded {
        if !method.procedure.isEmpty {
          Divider()
          VStack(alignment: .leading, spacing: 5) {
            Text("步骤")
              .font(.caption.weight(.semibold))
            ForEach(Array(method.procedure.enumerated()), id: \.offset) { index, step in
              Text("\(index + 1). \(step)")
                .font(.caption)
                .textSelection(.enabled)
            }
          }
        }

        HStack {
          if method.status == "pending_review" {
            Label("请从对应例题完成复核", systemImage: "arrow.turn.up.right")
              .font(.caption2)
              .foregroundStyle(.secondary)
          }
          Spacer()
          if method.status != "deprecated" {
            Button("废弃") {
              Task { await model.updateMethod(method, status: "deprecated") }
            }
            .controlSize(.small)
          }
        }
      }
    }
    .padding(12)
    .background(.background.secondary, in: RoundedRectangle(cornerRadius: 10))
    .overlay {
      RoundedRectangle(cornerRadius: 10)
        .stroke(.separator.opacity(0.5), lineWidth: 0.5)
    }
  }
}

struct StatusBadge: View {
  let status: String

  var body: some View {
    Text(label)
      .font(.caption2.weight(.medium))
      .foregroundStyle(color)
      .padding(.horizontal, 7)
      .padding(.vertical, 3)
      .background(color.opacity(0.12), in: Capsule())
  }

  private var label: String {
    switch status {
    case "verified": "已验证"
    case "needs_review": "待复核"
    case "rejected": "未通过"
    case "generation_failed": "生成失败"
    case "promoted": "已晋级"
    case "pending_review": "待审"
    case "deprecated": "已废弃"
    default: status
    }
  }

  private var color: Color {
    switch status {
    case "verified": .green
    case "promoted": .blue
    case "needs_review", "pending_review": .orange
    case "rejected", "generation_failed", "deprecated": .red
    default: .secondary
    }
  }
}
