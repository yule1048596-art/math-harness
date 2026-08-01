import SwiftUI

struct RootView: View {
  @EnvironmentObject private var model: AppModel
  @State private var showingCreateWorkspace = false
  @State private var showingKnowledge = true

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
        }
      }
    }
    .sheet(isPresented: $showingCreateWorkspace) {
      CreateWorkspaceSheet()
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
