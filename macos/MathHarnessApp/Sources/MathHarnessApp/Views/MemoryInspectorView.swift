import MathHarnessCore
import SwiftUI

struct MemoryInspectorView: View {
  @EnvironmentObject private var model: AppModel
  @State private var searchText = ""
  @State private var status: MemoryStatus = .active
  @State private var selectedKind: MemoryKind?
  @State private var showingAdd = false
  @State private var editingMemory: MemoryItem?
  @State private var confirmingBackfill = false

  var body: some View {
    VStack(spacing: 0) {
      header
      Divider()
      controls
      Divider()
      memoryList
    }
    .frame(minWidth: 310)
    .sheet(isPresented: $showingAdd) {
      MemoryEditorView()
        .environmentObject(model)
    }
    .sheet(item: $editingMemory) { memory in
      MemoryEditorView(memory: memory)
        .environmentObject(model)
    }
    .confirmationDialog(
      "整理已有对话？",
      isPresented: $confirmingBackfill,
      titleVisibility: .visible
    ) {
      Button("开始整理") {
        Task { _ = await model.backfillMemories() }
      }
      Button("取消", role: .cancel) {}
    } message: {
      Text("系统会读取本工作区的历史用户消息，并产生额外的模型调用；不会处理其他工作区。")
    }
  }

  private var header: some View {
    VStack(alignment: .leading, spacing: 10) {
      HStack {
        Label("长期记忆", systemImage: "brain.head.profile")
          .font(.headline)
        Spacer()
        Button {
          Task { await model.refreshMemoryState() }
        } label: {
          Image(systemName: "arrow.clockwise")
        }
        .buttonStyle(.borderless)
        .help("刷新记忆")
      }

      Toggle(
        "每轮对话后自动整理",
        isOn: Binding(
          get: { model.memorySettings?.automaticExtractionEnabled ?? false },
          set: { enabled in
            Task { await model.setAutomaticMemoryEnabled(enabled) }
          }
        )
      )
      .disabled(model.memoryHealth?.extractorAvailable == false)

      if let health = model.memoryHealth {
        HStack(spacing: 7) {
          Circle()
            .fill(healthColor(health))
            .frame(width: 7, height: 7)
          Text(healthDescription(health))
            .font(.caption)
            .foregroundStyle(.secondary)
        }
      }

      Text("软记忆只调整交流背景和讲解方式，不能作为数学证明或验算依据。")
        .font(.caption)
        .foregroundStyle(.secondary)
    }
    .padding(14)
  }

  private var controls: some View {
    VStack(spacing: 9) {
      TextField("搜索记忆", text: $searchText)
        .textFieldStyle(.roundedBorder)

      Picker("状态", selection: $status) {
        Text("正在使用").tag(MemoryStatus.active)
        Text("已归档").tag(MemoryStatus.archived)
        Text("已替代").tag(MemoryStatus.superseded)
      }
      .pickerStyle(.segmented)

      HStack {
        Menu {
          Button("全部类型") { selectedKind = nil }
          Divider()
          ForEach(MemoryKind.allCases) { kind in
            Button(kind.displayName) { selectedKind = kind }
          }
        } label: {
          Label(selectedKind?.displayName ?? "全部类型", systemImage: "line.3.horizontal.decrease")
        }

        Spacer()

        Menu {
          Button("整理当前会话", systemImage: "arrow.triangle.2.circlepath") {
            Task { await model.retrySelectedConversationMemory() }
          }
          .disabled(
            model.selectedConversationID == nil
              || model.memoryHealth?.extractorAvailable == false
          )
          Button("整理已有对话……", systemImage: "clock.arrow.trianglehead.counterclockwise.rotate.90") {
            confirmingBackfill = true
          }
        } label: {
          Image(systemName: "ellipsis.circle")
        }
        .menuStyle(.borderlessButton)

        Button {
          showingAdd = true
        } label: {
          Label("添加", systemImage: "plus")
        }
        .buttonStyle(.borderedProminent)
      }
    }
    .padding(12)
  }

  @ViewBuilder
  private var memoryList: some View {
    if filteredMemories.isEmpty {
      ContentUnavailableView {
        Label(emptyTitle, systemImage: "brain")
      } description: {
        Text("可以手工添加，或在 MiMo 模式下通过对话自动形成软记忆。")
      }
    } else {
      List(filteredMemories) { memory in
        MemoryRow(memory: memory) {
          editingMemory = memory
        }
      }
      .listStyle(.inset)
    }
  }

  private var filteredMemories: [MemoryItem] {
    let needle = searchText.trimmingCharacters(in: .whitespacesAndNewlines).localizedLowercase
    return model.memories.filter { memory in
      guard memory.status == status else { return false }
      if let selectedKind, memory.kind != selectedKind { return false }
      guard !needle.isEmpty else { return true }
      return memory.content.localizedLowercase.contains(needle)
        || memory.tags.contains { $0.localizedLowercase.contains(needle) }
    }
  }

  private var emptyTitle: String {
    switch status {
    case .active: "还没有长期记忆"
    case .archived: "没有已归档记忆"
    case .superseded: "没有已替代记忆"
    }
  }

  private func healthColor(_ health: MemoryHealth) -> Color {
    if health.failedCount > 0 { return .orange }
    if health.runningCount > 0 || health.queuedCount > 0 { return .blue }
    if !health.extractorAvailable { return .secondary }
    return .green
  }

  private func healthDescription(_ health: MemoryHealth) -> String {
    if !health.extractorAvailable { return "自动整理需要先配置 MiMo" }
    if health.runningCount > 0 { return "正在整理 \(health.runningCount) 个任务" }
    if health.queuedCount > 0 { return "等待整理 \(health.queuedCount) 个任务" }
    if health.failedCount > 0 { return "有 \(health.failedCount) 个任务需要重试" }
    return "记忆系统正常"
  }
}

private struct MemoryRow: View {
  @EnvironmentObject private var model: AppModel
  let memory: MemoryItem
  let onEdit: () -> Void

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      HStack(spacing: 6) {
        if memory.pinned {
          Image(systemName: "pin.fill")
            .foregroundStyle(.tint)
        }
        Text(memory.kind.displayName)
          .font(.caption.weight(.medium))
          .foregroundStyle(.secondary)
        Spacer()
        Text(memory.source == "automatic" ? "自动" : "手工")
          .font(.caption2)
          .foregroundStyle(.tertiary)
      }

      Text(memory.content)
        .textSelection(.enabled)
        .fixedSize(horizontal: false, vertical: true)

      if !memory.tags.isEmpty {
        Text(memory.tags.map { "#\($0)" }.joined(separator: "  "))
          .font(.caption2)
          .foregroundStyle(.secondary)
      }

      HStack(spacing: 12) {
        if memory.status == .active {
          Button(memory.pinned ? "取消置顶" : "置顶") {
            Task {
              _ = await model.updateMemory(
                memory,
                request: MemoryUpdateRequest(pinned: !memory.pinned)
              )
            }
          }
          Button("编辑", action: onEdit)
          Button("归档", role: .destructive) {
            Task { await model.archiveMemory(memory) }
          }
        } else if memory.status == .archived {
          Button("恢复") {
            Task {
              _ = await model.updateMemory(
                memory,
                request: MemoryUpdateRequest(status: .active)
              )
            }
          }
        }
        if let conversationID = memory.conversationID {
          Spacer()
          Button("来源会话") {
            model.selectConversation(conversationID)
          }
        }
      }
      .font(.caption)
      .buttonStyle(.borderless)
    }
    .padding(.vertical, 5)
  }
}

private struct MemoryEditorView: View {
  @Environment(\.dismiss) private var dismiss
  @EnvironmentObject private var model: AppModel
  private let memory: MemoryItem?
  @State private var content: String
  @State private var kind: MemoryKind
  @State private var tags: String
  @State private var pinned: Bool

  init(memory: MemoryItem? = nil) {
    self.memory = memory
    _content = State(initialValue: memory?.content ?? "")
    _kind = State(initialValue: memory?.kind ?? .manualNote)
    _tags = State(initialValue: memory?.tags.joined(separator: "，") ?? "")
    _pinned = State(initialValue: memory?.pinned ?? false)
  }

  var body: some View {
    VStack(alignment: .leading, spacing: 18) {
      Text(memory == nil ? "添加软记忆" : "编辑软记忆")
        .font(.title2.weight(.semibold))

      Form {
        TextField("内容", text: $content, axis: .vertical)
          .lineLimit(3...7)
        Picker("类型", selection: $kind) {
          ForEach(MemoryKind.allCases) { item in
            Text(item.displayName).tag(item)
          }
        }
        TextField("标签（逗号分隔）", text: $tags)
        Toggle("置顶到每轮对话", isOn: $pinned)
      }
      .formStyle(.grouped)

      HStack {
        Spacer()
        Button("取消") { dismiss() }
        Button("保存") {
          Task {
            let tagValues =
              tags
              .split(whereSeparator: { $0 == "," || $0 == "，" })
              .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
              .filter { !$0.isEmpty }
            let succeeded: Bool
            if let memory {
              succeeded = await model.updateMemory(
                memory,
                request: MemoryUpdateRequest(
                  content: content,
                  kind: kind,
                  tags: tagValues,
                  pinned: pinned
                )
              )
            } else {
              succeeded = await model.createMemory(
                content: content,
                kind: kind,
                tags: tagValues,
                pinned: pinned
              )
            }
            if succeeded { dismiss() }
          }
        }
        .buttonStyle(.borderedProminent)
        .disabled(
          content.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            || model.isMemoryOperationInProgress
        )
      }
    }
    .padding(22)
    .frame(width: 460)
  }
}

extension MemoryKind {
  fileprivate var displayName: String {
    switch self {
    case .profile: "用户画像"
    case .learningGoal: "学习目标"
    case .explanationPreference: "讲解偏好"
    case .topicContext: "专题背景"
    case .manualNote: "手工备注"
    }
  }
}
