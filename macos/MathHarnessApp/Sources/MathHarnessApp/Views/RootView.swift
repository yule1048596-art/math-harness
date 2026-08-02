import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct RootView: View {
  @EnvironmentObject private var model: AppModel
  @State private var showingCreateWorkspace = false
  @State private var showingKnowledge = true
  @State private var importDraft: CorpusImportDraft?
  @State private var noticeMessage: String?

  var body: some View {
    NavigationSplitView {
      SidebarView(showingCreateWorkspace: $showingCreateWorkspace)
        .navigationSplitViewColumnWidth(min: 210, ideal: 240, max: 300)
    } detail: {
      detail
    }
    .inspector(isPresented: $showingKnowledge) {
      KnowledgeInspectorView()
        .inspectorColumnWidth(min: 260, ideal: 320, max: 420)
    }
    .toolbar {
      ToolbarItemGroup(placement: .primaryAction) {
        if model.selectedWorkspace != nil {
          Button {
            Task { await model.refreshSelectedWorkspace() }
          } label: {
            Label("刷新", systemImage: "arrow.clockwise")
          }
          .help("刷新当前工作区")

          Button {
            showingKnowledge.toggle()
          } label: {
            Label("知识库", systemImage: "books.vertical")
          }
          .help(showingKnowledge ? "隐藏知识库" : "显示知识库")

          Menu {
            Button("导入题库……", systemImage: "square.and.arrow.down.on.square") {
              chooseCorpusFile()
            }
            Button("备份当前工作区……", systemImage: "externaldrive.badge.timemachine") {
              Task { await saveWorkspaceBackup() }
            }
            Divider()
            Button("恢复工作区备份……", systemImage: "arrow.counterclockwise.circle") {
              chooseWorkspaceBackup()
            }
          } label: {
            Label("数据", systemImage: "externaldrive")
          }
          .help("导入题库、备份或恢复工作区")
          .disabled(model.isDataOperationInProgress)
        }
      }
    }
    .sheet(isPresented: $showingCreateWorkspace) {
      CreateWorkspaceSheet()
    }
    .sheet(item: $importDraft) { draft in
      BulkImportView(draft: draft)
    }
    .alert(
      "Math Harness",
      isPresented: Binding(
        get: { model.errorMessage != nil },
        set: { if !$0 { model.errorMessage = nil } }
      )
    ) {
      Button("好") { model.errorMessage = nil }
    } message: {
      Text(model.errorMessage ?? "")
    }
    .alert(
      "操作完成",
      isPresented: Binding(
        get: { noticeMessage != nil },
        set: { if !$0 { noticeMessage = nil } }
      )
    ) {
      Button("好") { noticeMessage = nil }
    } message: {
      Text(noticeMessage ?? "")
    }
  }

  @ViewBuilder
  private var detail: some View {
    switch model.backendPhase {
    case .idle, .starting:
      ContentUnavailableView {
        Label("正在启动数学引擎", systemImage: "function")
      } description: {
        Text("首次启动打包版可能需要几秒钟。")
      } actions: {
        ProgressView()
          .controlSize(.small)
      }

    case .failed(let message):
      ContentUnavailableView {
        Label("数学引擎未能启动", systemImage: "exclamationmark.triangle")
      } description: {
        Text(message)
          .textSelection(.enabled)
      } actions: {
        Button("重试") {
          Task { await model.restartBackend() }
        }
        .buttonStyle(.borderedProminent)
      }

    case .running:
      if model.selectedWorkspace != nil {
        WorkspaceView()
      } else {
        ContentUnavailableView {
          Label("创建第一个数学工作区", systemImage: "square.stack.3d.up")
        } description: {
          Text("不同工作区的题目、方法和成长记录彼此隔离。")
        } actions: {
          Button("新建工作区") {
            showingCreateWorkspace = true
          }
          .buttonStyle(.borderedProminent)
        }
      }
    }
  }

  private func chooseCorpusFile() {
    let panel = NSOpenPanel()
    panel.title = "选择 JSON 或 JSONL 题库"
    panel.allowsMultipleSelection = false
    panel.canChooseDirectories = false
    panel.allowedContentTypes = [
      .json,
      UTType(filenameExtension: "jsonl") ?? .data,
      UTType(filenameExtension: "ndjson") ?? .data,
    ]
    guard panel.runModal() == .OK, let url = panel.url else { return }
    do {
      let data = try readSecurityScopedData(from: url)
      guard let content = String(data: data, encoding: .utf8) else {
        model.showError("题库必须是 UTF-8 编码的 JSON 或 JSONL 文件。")
        return
      }
      importDraft = CorpusImportDraft(content: content, sourceName: url.lastPathComponent)
    } catch {
      model.showError("无法读取题库：\(error.localizedDescription)")
    }
  }

  private func saveWorkspaceBackup() async {
    guard let exported = await model.exportWorkspaceBackup() else { return }
    let panel = NSSavePanel()
    panel.title = "保存 Math Harness 工作区备份"
    panel.nameFieldStringValue = exported.suggestedName
    panel.allowedContentTypes = [UTType(filenameExtension: "mathharness") ?? .data]
    panel.canCreateDirectories = true
    guard panel.runModal() == .OK, let url = panel.url else { return }
    do {
      try exported.data.write(to: url, options: .atomic)
      noticeMessage = "工作区备份已保存到 \(url.lastPathComponent)。"
    } catch {
      model.showError("无法保存工作区备份：\(error.localizedDescription)")
    }
  }

  private func chooseWorkspaceBackup() {
    let panel = NSOpenPanel()
    panel.title = "选择 Math Harness 工作区备份"
    panel.allowsMultipleSelection = false
    panel.canChooseDirectories = false
    panel.allowedContentTypes = [UTType(filenameExtension: "mathharness") ?? .data]
    guard panel.runModal() == .OK, let url = panel.url else { return }
    do {
      let data = try readSecurityScopedData(from: url)
      Task {
        if let result = await model.restoreWorkspaceBackup(data) {
          noticeMessage = "已创建恢复副本“\(result.workspace.name)”，原工作区未被覆盖。"
        }
      }
    } catch {
      model.showError("无法读取工作区备份：\(error.localizedDescription)")
    }
  }

  private func readSecurityScopedData(from url: URL) throws -> Data {
    let accessed = url.startAccessingSecurityScopedResource()
    defer {
      if accessed { url.stopAccessingSecurityScopedResource() }
    }
    return try Data(contentsOf: url)
  }
}

private struct CreateWorkspaceSheet: View {
  @Environment(\.dismiss) private var dismiss
  @EnvironmentObject private var model: AppModel
  @State private var name = ""
  @State private var description = ""
  @State private var isCreating = false

  var body: some View {
    VStack(alignment: .leading, spacing: 20) {
      VStack(alignment: .leading, spacing: 6) {
        Text("新建数学工作区")
          .font(.title2.weight(.semibold))
        Text("它将拥有独立的数据库、方法卡和解题历史。")
          .foregroundStyle(.secondary)
      }

      Form {
        TextField("名称", text: $name, prompt: Text("例如：渐进估计"))
        TextField(
          "说明",
          text: $description,
          prompt: Text("这个工作区准备专精什么？"),
          axis: .vertical
        )
        .lineLimit(2...4)
      }
      .formStyle(.grouped)

      HStack {
        Spacer()
        Button("取消") { dismiss() }
          .keyboardShortcut(.cancelAction)
        Button("创建") {
          isCreating = true
          Task {
            let succeeded = await model.createWorkspace(
              name: name,
              description: description
            )
            isCreating = false
            if succeeded { dismiss() }
          }
        }
        .keyboardShortcut(.defaultAction)
        .buttonStyle(.borderedProminent)
        .disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isCreating)
      }
    }
    .padding(24)
    .frame(width: 480)
  }
}
