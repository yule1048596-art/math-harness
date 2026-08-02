import MathHarnessCore
import SwiftUI

struct CorpusImportDraft: Identifiable {
  let id = UUID()
  let content: String
  let sourceName: String
}

struct BulkImportView: View {
  @Environment(\.dismiss) private var dismiss
  @EnvironmentObject private var model: AppModel

  let draft: CorpusImportDraft

  @State private var reviewPolicy: ImportReviewPolicy = .pending
  @State private var extractorPolicy: ImportExtractorPolicy = .rules
  @State private var preview: BulkExampleImportResult?
  @State private var committed: BulkExampleImportResult?
  @State private var isLoading = false

  var body: some View {
    VStack(alignment: .leading, spacing: 18) {
      header
      policyControls
      Divider()
      report
      Spacer(minLength: 0)
      actions
    }
    .padding(24)
    .frame(minWidth: 680, minHeight: 560)
    .task(id: previewTaskID) {
      await runPreview()
    }
  }

  private var header: some View {
    VStack(alignment: .leading, spacing: 5) {
      Label("导入题库", systemImage: "square.and.arrow.down.on.square")
        .font(.title2.weight(.semibold))
      Text(draft.sourceName)
        .font(.callout.monospaced())
        .foregroundStyle(.secondary)
      Text("先验证整份文件；存在任何格式错误时不会写入一条数据。")
        .font(.callout)
        .foregroundStyle(.secondary)
    }
  }

  private var policyControls: some View {
    Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 12) {
      GridRow {
        Text("复核策略")
          .foregroundStyle(.secondary)
        Picker("复核策略", selection: $reviewPolicy) {
          Text("全部进入待复核").tag(ImportReviewPolicy.pending)
          Text("保留文件 reviewed 标记").tag(ImportReviewPolicy.preserve)
        }
        .labelsHidden()
        .pickerStyle(.segmented)
      }
      GridRow {
        Text("方法提炼")
          .foregroundStyle(.secondary)
        Picker("方法提炼", selection: $extractorPolicy) {
          Text("本地规则（免费）").tag(ImportExtractorPolicy.rules)
          Text("当前提炼器").tag(ImportExtractorPolicy.configured)
        }
        .labelsHidden()
        .pickerStyle(.segmented)
      }
    }
    .disabled(isLoading || committed != nil)
  }

  @ViewBuilder
  private var report: some View {
    if let committed {
      ContentUnavailableView {
        Label("题库导入完成", systemImage: "checkmark.circle.fill")
          .foregroundStyle(.green)
      } description: {
        Text("新增 \(committed.importedCount) 题，跳过 \(committed.duplicateCount) 个重复项。")
      }
      .frame(maxWidth: .infinity, maxHeight: .infinity)
    } else if isLoading || preview == nil {
      VStack(spacing: 12) {
        ProgressView()
        Text("正在解析并验证题库……")
          .foregroundStyle(.secondary)
      }
      .frame(maxWidth: .infinity, maxHeight: .infinity)
    } else if let preview {
      VStack(alignment: .leading, spacing: 14) {
        HStack(spacing: 12) {
          ImportMetric(title: "总计", value: preview.totalCount, color: .primary)
          ImportMetric(title: "可导入", value: preview.readyCount, color: .blue)
          ImportMetric(title: "重复", value: preview.duplicateCount, color: .secondary)
          ImportMetric(title: "错误", value: preview.invalidCount, color: .red)
        }
        if preview.invalidCount > 0 {
          Label("请修正下列项目后重新选择文件；当前批次不会写入。", systemImage: "xmark.octagon")
            .font(.callout.weight(.medium))
            .foregroundStyle(.red)
          List(preview.items.filter { $0.status == "invalid" }) { item in
            VStack(alignment: .leading, spacing: 4) {
              Text("第 \(item.index) 项")
                .font(.headline)
              ForEach(item.errors, id: \.self) { error in
                Text(error)
                  .font(.caption.monospaced())
                  .textSelection(.enabled)
              }
            }
            .padding(.vertical, 4)
          }
          .frame(minHeight: 180)
        } else {
          VStack(alignment: .leading, spacing: 8) {
            Label("预检通过，可以原子导入", systemImage: "checkmark.shield")
              .foregroundStyle(.green)
            Text(policyNote)
              .font(.callout)
              .foregroundStyle(.secondary)
            if extractorPolicy == .configured && preview.readyCount > 0 {
              Text("确认后最多产生 \(preview.readyCount) 次方法提炼请求；预检本身不会调用模型。")
                .font(.callout)
                .foregroundStyle(.orange)
            }
          }
          .padding(16)
          .frame(maxWidth: .infinity, alignment: .leading)
          .background(.green.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
        }
      }
    }
  }

  private var actions: some View {
    HStack {
      Text("支持 JSON、JSONL；单批最多 500 题。")
        .font(.caption)
        .foregroundStyle(.secondary)
      Spacer()
      Button(committed == nil ? "取消" : "完成") { dismiss() }
        .keyboardShortcut(.cancelAction)
      if let preview, committed == nil {
        Button("确认导入 \(preview.readyCount) 题") {
          Task { await commitImport() }
        }
        .keyboardShortcut(.defaultAction)
        .buttonStyle(.borderedProminent)
        .disabled(!preview.canCommit || preview.readyCount == 0 || isLoading)
      }
    }
  }

  private var previewTaskID: String {
    "\(reviewPolicy.rawValue)-\(extractorPolicy.rawValue)"
  }

  private var policyNote: String {
    reviewPolicy == .pending
      ? "所有新题都会进入待复核区，不会直接改写已晋级知识。"
      : "文件中 reviewed=true 且数学验证通过的题目会直接晋级，请只用于可信语料。"
  }

  private func runPreview() async {
    committed = nil
    preview = nil
    isLoading = true
    defer { isLoading = false }
    preview = await model.importExamples(
      content: draft.content,
      sourceName: draft.sourceName,
      reviewPolicy: reviewPolicy,
      extractorPolicy: extractorPolicy,
      commit: false
    )
  }

  private func commitImport() async {
    isLoading = true
    defer { isLoading = false }
    committed = await model.importExamples(
      content: draft.content,
      sourceName: draft.sourceName,
      reviewPolicy: reviewPolicy,
      extractorPolicy: extractorPolicy,
      commit: true
    )
  }
}

private struct ImportMetric: View {
  let title: String
  let value: Int
  let color: Color

  var body: some View {
    VStack(alignment: .leading, spacing: 3) {
      Text(title)
        .font(.caption)
        .foregroundStyle(.secondary)
      Text(value.formatted())
        .font(.title2.weight(.semibold))
        .foregroundStyle(color)
    }
    .padding(12)
    .frame(maxWidth: .infinity, alignment: .leading)
    .background(.background.secondary, in: RoundedRectangle(cornerRadius: 10))
  }
}
