import MathHarnessCore
import SwiftUI

struct KnowledgeInspectorView: View {
  @EnvironmentObject private var model: AppModel
  @State private var query = ""

  private var filteredMethods: [MethodCard] {
    let normalized = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    guard !normalized.isEmpty else { return model.methods }
    return model.methods.filter {
      $0.name.lowercased().contains(normalized)
        || $0.key.lowercased().contains(normalized)
        || $0.tags.contains(where: { $0.lowercased().contains(normalized) })
    }
  }

  var body: some View {
    VStack(spacing: 0) {
      HStack {
        VStack(alignment: .leading, spacing: 2) {
          Text("知识库")
            .font(.headline)
          Text("\(model.methods.count) 张方法卡")
            .font(.caption)
            .foregroundStyle(.secondary)
        }
        Spacer()
      }
      .padding(14)

      TextField("搜索方法", text: $query)
        .textFieldStyle(.roundedBorder)
        .padding(.horizontal, 12)
        .padding(.bottom, 10)

      Divider()

      if filteredMethods.isEmpty {
        ContentUnavailableView {
          Label("还没有方法卡", systemImage: "books.vertical")
        } description: {
          Text("录入并复核例题后，方法会出现在这里。")
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
            Button("人工晋级") {
              Task { await model.updateMethod(method, status: "promoted") }
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.small)
          }
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
