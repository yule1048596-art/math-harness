import MathHarnessCore
import SwiftUI

struct SidebarView: View {
  @EnvironmentObject private var model: AppModel
  @Binding var showingCreateWorkspace: Bool

  var body: some View {
    VStack(spacing: 0) {
      HStack(spacing: 10) {
        Image(systemName: "function")
          .font(.title2.weight(.semibold))
          .foregroundStyle(.tint)
          .frame(width: 30, height: 30)
          .background(.tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 8))
        VStack(alignment: .leading, spacing: 1) {
          Text("Math Harness")
            .font(.headline)
          Text("数学成长空间")
            .font(.caption)
            .foregroundStyle(.secondary)
        }
        Spacer()
      }
      .padding(.horizontal, 14)
      .padding(.vertical, 12)

      List(selection: selection) {
        Section("工作区") {
          ForEach(model.workspaces) { workspace in
            WorkspaceRow(workspace: workspace)
              .tag(workspace.id)
          }
        }
      }
      .listStyle(.sidebar)

      Divider()
      HStack(spacing: 8) {
        backendIndicator
        Spacer()
        Button {
          showingCreateWorkspace = true
        } label: {
          Image(systemName: "plus")
        }
        .buttonStyle(.borderless)
        .help("新建工作区")
      }
      .padding(.horizontal, 12)
      .frame(height: 42)
    }
  }

  private var selection: Binding<String?> {
    Binding(
      get: { model.selectedWorkspaceID },
      set: { model.selectWorkspace($0) }
    )
  }

  @ViewBuilder
  private var backendIndicator: some View {
    switch model.backendPhase {
    case .running(let version):
      Label("引擎 \(version)", systemImage: "circle.fill")
        .font(.caption)
        .foregroundStyle(.secondary)
        .symbolRenderingMode(.palette)
        .foregroundStyle(.green, .secondary)
    case .starting:
      Label("正在启动", systemImage: "circle.dotted")
        .font(.caption)
        .foregroundStyle(.secondary)
    case .failed:
      Label("启动失败", systemImage: "exclamationmark.circle.fill")
        .font(.caption)
        .foregroundStyle(.red)
    case .idle:
      Label("未启动", systemImage: "circle")
        .font(.caption)
        .foregroundStyle(.secondary)
    }
  }
}

private struct WorkspaceRow: View {
  let workspace: Workspace

  var body: some View {
    HStack(spacing: 9) {
      Image(systemName: "square.stack.3d.up.fill")
        .foregroundStyle(.tint)
      VStack(alignment: .leading, spacing: 2) {
        Text(workspace.name)
          .lineLimit(1)
        if !workspace.description.isEmpty {
          Text(workspace.description)
            .font(.caption)
            .foregroundStyle(.secondary)
            .lineLimit(1)
        }
      }
    }
    .padding(.vertical, 2)
  }
}
