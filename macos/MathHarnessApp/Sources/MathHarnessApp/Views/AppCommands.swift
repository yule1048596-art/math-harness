import SwiftUI

/// 菜单栏命令。
///
/// v0.17 之前一条自定义命令都没有：新建会话、搜索、刷新、开关面板全靠鼠标点，而
/// 「显示」菜单里只有系统默认的标签页项。日常用得最多的几件事应该有快捷键，而且
/// 快捷键要能在菜单里被看见——否则等于没有。
struct MathHarnessCommands: Commands {
  @ObservedObject var model: AppModel

  var body: some Commands {
    // 单窗口应用，「新建窗口」在这里没有意义，换成真正会新建的东西。
    CommandGroup(replacing: .newItem) {
      Button("新建会话") {
        Task { _ = await model.createConversation() }
      }
      .keyboardShortcut("n", modifiers: [.command])
      .disabled(model.selectedWorkspaceID == nil || model.isSolving)

      Button("新建工作区……") {
        model.showingCreateWorkspace = true
      }
      .keyboardShortcut("n", modifiers: [.command, .shift])
    }

    CommandGroup(after: .textEditing) {
      Divider()
      Button("搜索会话消息……") {
        model.showingConversationSearch = true
      }
      .keyboardShortcut("f", modifiers: [.command])
      .disabled(model.selectedWorkspaceID == nil)
    }

    CommandMenu("工作区") {
      Button("刷新") {
        Task { await model.refreshSelectedWorkspace() }
      }
      .keyboardShortcut("r", modifiers: [.command])
      .disabled(model.selectedWorkspaceID == nil)

      Divider()

      Button(model.inspectorPane == .knowledge ? "隐藏知识库" : "显示知识库") {
        model.inspectorPane = model.inspectorPane == .knowledge ? nil : .knowledge
      }
      .keyboardShortcut("i", modifiers: [.command, .option])
      .disabled(model.selectedWorkspaceID == nil)

      Button(model.inspectorPane == .memory ? "隐藏记忆工坊" : "显示记忆工坊") {
        model.inspectorPane = model.inspectorPane == .memory ? nil : .memory
      }
      .keyboardShortcut("m", modifiers: [.command, .option])
      .disabled(model.selectedWorkspaceID == nil)

      Divider()

      Button("导出到 Vault") {
        Task { await model.exportToVault() }
      }
      .keyboardShortcut("e", modifiers: [.command, .shift])
      .disabled(model.selectedWorkspaceID == nil || model.isExportingToVault)

      Divider()

      Button("停止生成") {
        Task { await model.stopCurrentTurn() }
      }
      .keyboardShortcut(".", modifiers: [.command])
      .disabled(model.stoppableTurnID == nil || model.isStoppingTurn)
    }
  }
}
