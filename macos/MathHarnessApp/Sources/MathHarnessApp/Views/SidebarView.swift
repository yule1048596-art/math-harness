import MathHarnessCore
import SwiftUI

/// 侧栏：上面切工作区，下面是这个工作区的会话。
///
/// v0.17 之前会话藏在标题栏的一个下拉菜单里，窄窗口下被截成「求 x…」——而会话是这个
/// 软件里最高频的东西。工作区切换反过来是低频的，收进一个带名字的菜单按钮就够。
struct SidebarView: View {
  @EnvironmentObject private var model: AppModel
  @State private var renamingConversation: Conversation?
  @State private var renameDraft = ""

  var body: some View {
    VStack(spacing: 0) {
      workspaceSwitcher
      Divider()
      if model.selectedWorkspace != nil {
        newConversationButton
        conversationList
      } else {
        Spacer()
        Text("先创建一个工作区。")
          .font(.callout)
          .foregroundStyle(.secondary)
        Spacer()
      }
      Divider()
      footer
    }
    .alert("重命名会话", isPresented: isRenaming) {
      TextField("标题", text: $renameDraft)
      Button("取消", role: .cancel) { renamingConversation = nil }
      Button("保存") {
        guard let conversation = renamingConversation else { return }
        let title = renameDraft
        renamingConversation = nil
        Task { await model.renameConversation(conversation.id, title: title) }
      }
    }
  }

  private var isRenaming: Binding<Bool> {
    Binding(
      get: { renamingConversation != nil },
      set: { if !$0 { renamingConversation = nil } }
    )
  }

  private var workspaceSwitcher: some View {
    Menu {
      ForEach(model.workspaces) { workspace in
        Button {
          model.selectWorkspace(workspace.id)
        } label: {
          if workspace.id == model.selectedWorkspaceID {
            Label(workspace.name, systemImage: "checkmark")
          } else {
            Text(workspace.name)
          }
        }
      }
      if !model.workspaces.isEmpty {
        Divider()
      }
      Button {
        model.showingCreateWorkspace = true
      } label: {
        Label("新建工作区……", systemImage: "plus.square.on.square")
      }
    } label: {
      HStack(spacing: 10) {
        Image(systemName: "function")
          .font(.title3.weight(.semibold))
          .foregroundStyle(.tint)
          .frame(width: 28, height: 28)
          .background(.tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 7))
        VStack(alignment: .leading, spacing: 1) {
          Text(model.selectedWorkspace?.name ?? "Math Harness")
            .font(.headline)
            .lineLimit(1)
          Text(workspaceSubtitle)
            .font(.caption)
            .foregroundStyle(.secondary)
            .lineLimit(1)
        }
        Spacer(minLength: 4)
        Image(systemName: "chevron.up.chevron.down")
          .font(.caption2)
          .foregroundStyle(.secondary)
      }
      .contentShape(Rectangle())
    }
    // 不能用 `.borderlessButton`：它只画得下一个最简label，副标题和箭头会被整个吞掉。
    .menuStyle(.button)
    .buttonStyle(.plain)
    .menuIndicator(.hidden)
    .padding(.horizontal, 12)
    .padding(.vertical, 10)
    .help("切换工作区")
  }

  private var workspaceSubtitle: String {
    guard let workspace = model.selectedWorkspace else { return "数学成长空间" }
    return workspace.description.isEmpty
      ? "\(model.methods.count) 张方法卡"
      : workspace.description
  }

  private var newConversationButton: some View {
    Button {
      Task { _ = await model.createConversation() }
    } label: {
      Label("新建会话", systemImage: "square.and.pencil")
        .font(.callout.weight(.medium))
        .foregroundStyle(.tint)
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(Rectangle())
    }
    .buttonStyle(.plain)
    .padding(.horizontal, 12)
    .padding(.vertical, 8)
    .disabled(model.isSolving)
  }

  private var conversationList: some View {
    List(selection: selectedConversation) {
      if model.conversations.isEmpty {
        Text("还没有会话")
          .font(.callout)
          .foregroundStyle(.secondary)
      } else {
        ForEach(model.conversations) { conversation in
          ConversationRow(conversation: conversation)
            .tag(conversation.id)
            .contextMenu {
              Button {
                renameDraft = conversation.title
                renamingConversation = conversation
              } label: {
                Label("重命名……", systemImage: "pencil")
              }
              Button {
                Task { await model.toggleConversationArchive(conversation) }
              } label: {
                Label(
                  conversation.status == .archived ? "取消归档" : "归档",
                  systemImage: conversation.status == .archived
                    ? "tray.and.arrow.up" : "archivebox"
                )
              }
            }
        }
      }
    }
    .listStyle(.sidebar)
  }

  private var footer: some View {
    HStack(spacing: 8) {
      backendIndicator
      Spacer()
      // 只能归档、看不到归档就是单程票：消息一条没少，界面上却再也找不回来。
      Toggle(isOn: showsArchived) {
        Image(systemName: "archivebox")
      }
      .toggleStyle(.button)
      .buttonStyle(.borderless)
      .controlSize(.small)
      .help(
        model.showsArchivedConversations ? "隐藏已归档的会话" : "显示已归档的会话"
      )
    }
    .padding(.horizontal, 12)
    .frame(height: 40)
  }

  private var showsArchived: Binding<Bool> {
    Binding(
      get: { model.showsArchivedConversations },
      set: { value in Task { await model.setShowsArchivedConversations(value) } }
    )
  }

  private var selectedConversation: Binding<String?> {
    Binding(
      get: { model.selectedConversationID },
      set: { model.selectConversation($0) }
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

private struct ConversationRow: View {
  let conversation: Conversation

  var body: some View {
    HStack(spacing: 8) {
      VStack(alignment: .leading, spacing: 2) {
        Text(conversation.title)
          .lineLimit(1)
        HStack(spacing: 5) {
          if conversation.status == .archived {
            Image(systemName: "archivebox")
          }
          Text("\(conversation.messageCount) 条消息")
        }
        .font(.caption)
        .foregroundStyle(.secondary)
      }
      Spacer(minLength: 0)
    }
    .padding(.vertical, 2)
  }
}
