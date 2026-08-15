import AppKit
import MathHarnessCore
import SwiftUI

struct SettingsView: View {
  @EnvironmentObject private var model: AppModel
  @State private var settings = AppSettings.providerSettings
  @State private var selectedProfileID: String?
  @State private var apiKeyDraft = ""
  @State private var probeState: ProbeState = .idle
  @State private var errorMessage: String?
  @State private var isRestarting = false
  /// 配置已经存下来了，但跑着的后端还是旧的。后端在启动时读配置，所以改完要重启才生效。
  @State private var needsRestart = false
  @AppStorage(AppSettingsKey.maxOutputTokens) private var maxOutputTokens = 3_000
  @AppStorage(AppSettingsKey.verificationRepair) private var verificationRepair = true
  @AppStorage(AppSettingsKey.verificationFallback) private var verificationFallback = true
  @AppStorage(AppSettingsKey.appearance) private var appearance = AppAppearance.system
    .rawValue
  @AppStorage(AppSettingsKey.sendShortcut) private var sendShortcut = SendShortcut
    .commandReturn.rawValue
  @AppStorage(AppSettingsKey.messageTextSize) private var messageTextSize = MessageTextSize
    .medium.rawValue
  @AppStorage(AppSettingsKey.rendersMarkdown) private var rendersMarkdown = true
  @AppStorage(AppSettingsKey.typesetsFormulas) private var typesetsFormulas = true
  @AppStorage(AppSettingsKey.notifiesWhenFinished) private var notifiesWhenFinished = true
  @AppStorage(AppSettingsKey.exportsAutomatically) private var exportsAutomatically = true
  @StateObject private var vault = VaultLocation()

  enum ProbeState: Equatable {
    case idle
    case running
    case success(String)
    case failure(String)
  }

  var body: some View {
    TabView {
      providerTab
        .tabItem { Label("模型服务", systemImage: "cpu") }
      rolesTab
        .tabItem { Label("角色分配", systemImage: "slider.horizontal.3") }
      // 不用 `textformat`：中文环境下 SF Symbols 会把它画成「格式」两个字，和下面的
      // 「界面」叠在一起像两个标题。
      interfaceTab
        .tabItem { Label("界面", systemImage: "paintbrush") }
      generalTab
        .tabItem { Label("通用", systemImage: "gearshape") }
      aboutTab
        .tabItem { Label("关于", systemImage: "info.circle") }
    }
    .frame(width: 640, height: 560)
    .onAppear(perform: load)
    // 配置改一下存一下，不等按钮。
    //
    // 这是一个实打实的数据丢失 bug：v0.20 之前唯一写盘的地方是「应用并重启数学引擎」，
    // 而那个按钮在「通用」页。于是在「模型服务」页加一个档案、填好密钥、**测试连接
    // 还显示成功**，然后关掉窗口——全没了。绿色的对勾给的信号是「配置好了」，实际上
    // 一个字节都没落盘。
    .onChange(of: settings) { _, _ in persist() }
    // 密钥不在 `settings` 里（它只进钥匙串），所以单独盯着。写钥匙串是本地操作、不弹
    // 授权框，每次按键存一下的代价可以忽略；而少存一次的代价是用户得回控制台重取。
    .onChange(of: apiKeyDraft) { _, _ in saveKey(for: selectedProfileID) }
    .onDisappear(perform: persist)
    .alert(
      "无法保存设置",
      isPresented: Binding(
        get: { errorMessage != nil },
        set: { if !$0 { errorMessage = nil } }
      )
    ) {
      Button("好") { errorMessage = nil }
    } message: {
      Text(errorMessage ?? "")
    }
  }

  // MARK: - 模型服务

  private var providerTab: some View {
    HSplitView {
      profileList
        .frame(minWidth: 180, idealWidth: 200, maxWidth: 260)
      profileEditor
        .frame(minWidth: 360)
    }
  }

  private var profileList: some View {
    VStack(spacing: 0) {
      List(selection: $selectedProfileID) {
        ForEach(settings.profiles) { profile in
          VStack(alignment: .leading, spacing: 2) {
            Text(profile.name.isEmpty ? "未命名" : profile.name)
            Text(profile.defaultModel)
              .font(.caption)
              .foregroundStyle(.secondary)
          }
          .tag(profile.id)
        }
      }
      Divider()
      HStack(spacing: 4) {
        Menu {
          ForEach(ProviderPreset.allCases) { preset in
            Button(preset.displayName) { addProfile(preset) }
          }
        } label: {
          Label("添加", systemImage: "plus")
        }
        .menuStyle(.borderlessButton)
        .fixedSize()

        Button {
          removeSelectedProfile()
        } label: {
          Label("删除", systemImage: "minus")
        }
        .buttonStyle(.borderless)
        .disabled(selectedProfileID == nil)
        Spacer()
      }
      .padding(6)
    }
  }

  @ViewBuilder
  private var profileEditor: some View {
    if let index = settings.profiles.firstIndex(where: { $0.id == selectedProfileID }) {
      Form {
        Section("连接") {
          TextField("名称", text: $settings.profiles[index].name)
          TextField("Base URL", text: $settings.profiles[index].baseURL)
          TextField("默认模型", text: $settings.profiles[index].defaultModel)
          SecureField("API Key", text: $apiKeyDraft)
          Text("密钥只保存在 macOS 钥匙串中，不会写入工作区数据库、项目文件或备份。")
            .font(.caption)
            .foregroundStyle(.secondary)
        }

        Section("结构化输出") {
          Picker("模式", selection: $settings.profiles[index].structuredOutputMode) {
            ForEach(StructuredOutputMode.allCases, id: \.self) { mode in
              Text(mode.displayName).tag(mode)
            }
          }
          Text(settings.profiles[index].structuredOutputMode.explanation)
            .font(.caption)
            .foregroundStyle(.secondary)
        }

        Section("限制") {
          Stepper(
            "超时：\(Int(settings.profiles[index].timeoutSeconds)) 秒",
            value: $settings.profiles[index].timeoutSeconds,
            in: 10...300,
            step: 10
          )
          Stepper(
            "最大输出 Token：\(settings.profiles[index].maxOutputTokens)",
            value: $settings.profiles[index].maxOutputTokens,
            in: 256...16_000,
            step: 256
          )
        }

        Section {
          HStack {
            Button("测试连接") { runProbe(profile: settings.profiles[index]) }
              .disabled(probeState == .running)
            probeStatusView
            Spacer()
          }
        }
      }
      .formStyle(.grouped)
      // 切换档案前先把上一个的密钥存下来。不然编辑框会被下一个档案的密钥直接盖掉，
      // 刚填的那个连去处都没有。
      .onChange(of: selectedProfileID) { previous, _ in
        saveKey(for: previous)
        loadKeyForSelection()
      }
    } else {
      ContentUnavailableView(
        "还没有模型服务",
        systemImage: "cpu",
        description: Text(
          "点击左下角「添加」选择一个服务商。也可以完全不添加——离线 SymPy 仍可验算带可验证目标的数学问题。"
        )
      )
    }
  }

  @ViewBuilder
  private var probeStatusView: some View {
    switch probeState {
    case .idle:
      EmptyView()
    case .running:
      ProgressView().controlSize(.small)
    case .success(let text):
      Label(text, systemImage: "checkmark.circle.fill")
        .foregroundStyle(.green)
        .font(.caption)
    case .failure(let text):
      Label(text, systemImage: "exclamationmark.triangle.fill")
        .foregroundStyle(.orange)
        .font(.caption)
        .textSelection(.enabled)
    }
  }

  // MARK: - 角色分配

  private var rolesTab: some View {
    Form {
      Section {
        Toggle("简单模式：所有环节使用同一个服务", isOn: $settings.useSimpleMode)
        if settings.useSimpleMode {
          Picker("使用", selection: profileSelection) {
            Text("离线（不接模型）").tag("")
            ForEach(settings.profiles) { profile in
              Text(profile.name.isEmpty ? profile.defaultModel : profile.name)
                .tag(profile.id)
            }
          }
        }
      } footer: {
        Text(
          "关闭简单模式后可以逐环节指定。记忆提取和目标整理调用频繁、难度不高，配便宜模型能明显省钱；求解建议配强模型。"
        )
        .font(.caption)
        .foregroundStyle(.secondary)
      }

      if !settings.useSimpleMode {
        ForEach(ModelRole.allCases) { role in
          Section(role.displayName) {
            Picker("服务", selection: binding(for: role).profile) {
              Text("离线").tag(RoleBinding.offlineProfile)
              ForEach(settings.profiles) { profile in
                Text(profile.name.isEmpty ? profile.defaultModel : profile.name)
                  .tag(profile.id)
              }
            }
            if !(settings.roles[role.rawValue]?.isOffline ?? true) {
              TextField(
                "模型（留空用服务默认）",
                text: binding(for: role).modelText
              )
              Picker("推理强度", selection: binding(for: role).reasoningEffort) {
                ForEach(["none", "low", "medium", "high"], id: \.self) { effort in
                  Text(effort).tag(effort)
                }
              }
            }
            Text(role.explanation)
              .font(.caption)
              .foregroundStyle(.secondary)
          }
        }
      }
    }
    .formStyle(.grouped)
  }

  // MARK: - 界面

  private var interfaceTab: some View {
    Form {
      Section {
        Picker("正文字号", selection: $messageTextSize) {
          ForEach(MessageTextSize.allCases) { item in
            Text(item.displayName).tag(item.rawValue)
          }
        }
        Toggle("按 Markdown 渲染回答", isOn: $rendersMarkdown)
        Toggle("排版 LaTeX 公式", isOn: $typesetsFormulas)
        previewCard
      } header: {
        Text("阅读")
      } footer: {
        Text(
          "渲染前会先把 x**2、a*b、x_1 里的运算符保护起来，公式不会被 Markdown 改写。"
            + "LaTeX 只排版认得的那部分记号，认不出来的整条按原文显示。"
        )
        .font(.caption)
        .foregroundStyle(.secondary)
      }

      Section {
        Picker("发送快捷键", selection: $sendShortcut) {
          ForEach(SendShortcut.allCases) { item in
            Text(item.displayName).tag(item.rawValue)
          }
        }
      } header: {
        Text("输入")
      } footer: {
        Text("默认 ⌘↩ 发送：数学问题常常要分几行写。⌘↩ 在两种设置下都能发送。")
          .font(.caption)
          .foregroundStyle(.secondary)
      }

      Section {
        Picker("主题", selection: $appearance) {
          ForEach(AppAppearance.allCases) { item in
            Text(item.displayName).tag(item.rawValue)
          }
        }
        Toggle("回答完成时提醒", isOn: $notifiesWhenFinished)
      } header: {
        Text("外观与提醒")
      } footer: {
        Text("提醒只在窗口不在最前面时跳一下 Dock 图标，不需要任何系统权限。")
          .font(.caption)
          .foregroundStyle(.secondary)
      }
    }
    .formStyle(.grouped)
  }

  /// Vault 位置这一行。
  ///
  /// 够不着时**只报原因加一个重试**，不弹模态、不阻塞、也不清掉配置——vault 可能只是
  /// 这次没挂上外置盘，下次就回来了。清掉的话用户还得重新找一遍。
  @ViewBuilder
  private var vaultRow: some View {
    switch vault.status {
    case .notConfigured:
      LabeledContent("Vault 位置") {
        Button("选择文件夹……") { vault.chooseFolder() }
      }
    case .ready(let url):
      LabeledContent("Vault 位置") {
        HStack(spacing: 8) {
          Text(url.path)
            .font(.caption)
            .foregroundStyle(.secondary)
            .lineLimit(1)
            .truncationMode(.head)
          Button("更改……") { vault.chooseFolder() }
          Button("移除") { vault.forget() }
        }
      }
    case .unavailable(let reason):
      VStack(alignment: .leading, spacing: 6) {
        Label(reason, systemImage: "exclamationmark.triangle")
          .font(.callout)
          .foregroundStyle(.orange)
        if !vault.displayPath.isEmpty {
          Text(vault.displayPath)
            .font(.caption)
            .foregroundStyle(.secondary)
            .lineLimit(1)
            .truncationMode(.head)
        }
        HStack {
          Spacer()
          Button("重试") { vault.retry() }
          Button("重新选择……") { vault.chooseFolder() }
        }
      }
    }
  }

  /// 字号和渲染是「看了才知道合不合适」的设置，所以就地给一段样例。
  private var previewCard: some View {
    VStack(alignment: .leading, spacing: 6) {
      Text("预览")
        .font(.caption)
        .foregroundStyle(.secondary)
      MessageTextView(
        text: """
          **幂法则**：对 x**2 求导得到 2*x。
          由 $\\frac{d}{dx}x^{n} = n x^{n-1}$ 立刻得到 $\\sum_{k=1}^{n} k$ 的形式。
          - 先看指数 n = 2
          - 再乘回原来的系数
          """
      )
      .padding(10)
      .frame(maxWidth: .infinity, alignment: .leading)
      .background(.background.secondary, in: RoundedRectangle(cornerRadius: 8))
    }
  }

  // MARK: - 通用

  private var generalTab: some View {
    Form {
      Section("生成") {
        Stepper(
          "最大输出 Token：\(maxOutputTokens)", value: $maxOutputTokens, in: 256...8_000, step: 256)
      }

      Section {
        Toggle("验证失败后让模型纠正一次", isOn: $verificationRepair)
        Toggle("纠正仍失败时使用 SymPy 重新生成", isOn: $verificationFallback)
      } header: {
        Text("验证恢复")
      } footer: {
        Text("两项都会产生额外的模型调用。关闭后验证失败的结果直接进入待复核，不再自动重试。")
          .font(.caption)
          .foregroundStyle(.secondary)
      }

      Section("数据") {
        LabeledContent("数据目录") {
          Button("在访达中显示") { revealDataDirectory() }
        }
        Text("每个工作区一个独立 SQLite 数据库。密钥不在其中。")
          .font(.caption)
          .foregroundStyle(.secondary)
      }

      Section {
        vaultRow
        if vault.isConfigured {
          Toggle("晋级和复核后自动导出", isOn: $exportsAutomatically)
        }
      } header: {
        Text("导出到 Obsidian")
      } footer: {
        Text(
          "只写 Vault 根目录下的 math-harness 文件夹，不碰其它笔记。"
            + "frontmatter 由本 App 维护，正文归你——你改过的正文不会被导出覆盖。"
        )
        .font(.caption)
        .foregroundStyle(.secondary)
      }

      Section {
        HStack(spacing: 10) {
          if needsRestart {
            Label("配置已保存，重启后生效", systemImage: "info.circle")
              .font(.caption)
              .foregroundStyle(.orange)
          } else {
            Text("配置改动会自动保存。")
              .font(.caption)
              .foregroundStyle(.secondary)
          }
          Spacer()
          if needsRestart {
            Button("重启数学引擎") { restartEngine() }
              .buttonStyle(.borderedProminent)
              .disabled(isRestarting)
          } else {
            Button("重启数学引擎") { restartEngine() }
              .disabled(isRestarting)
          }
        }
      }
    }
    .formStyle(.grouped)
  }

  private var aboutTab: some View {
    VStack(spacing: 12) {
      Image(systemName: "function")
        .font(.system(size: 48))
        .foregroundStyle(.tint)
      Text("Math Harness").font(.title2).bold()
      Text("版本 \(AppInfo.version)").foregroundStyle(.secondary)
      Link(
        "查看全部版本",
        destination: URL(string: "https://github.com/yule1048596-art/math-harness/releases")!
      )
      Text("MIT License").font(.caption).foregroundStyle(.secondary)
      Spacer()
    }
    .padding(40)
    .frame(maxWidth: .infinity, maxHeight: .infinity)
  }

  // MARK: - 绑定辅助

  private var profileSelection: Binding<String> {
    Binding(
      get: { settings.simpleProfileID ?? "" },
      set: { settings.simpleProfileID = $0.isEmpty ? nil : $0 }
    )
  }

  private struct RoleBindingProxy {
    var profile: Binding<String>
    var modelText: Binding<String>
    var reasoningEffort: Binding<String>
  }

  private func binding(for role: ModelRole) -> RoleBindingProxy {
    let key = role.rawValue
    return RoleBindingProxy(
      profile: Binding(
        get: { settings.roles[key]?.profile ?? RoleBinding.offlineProfile },
        set: { newValue in
          var binding = settings.roles[key] ?? .offline
          binding.profile = newValue
          settings.roles[key] = binding
        }
      ),
      modelText: Binding(
        get: { settings.roles[key]?.model ?? "" },
        set: { newValue in
          var binding = settings.roles[key] ?? .offline
          binding.model = newValue.isEmpty ? nil : newValue
          settings.roles[key] = binding
        }
      ),
      reasoningEffort: Binding(
        get: { settings.roles[key]?.reasoningEffort ?? "medium" },
        set: { newValue in
          var binding = settings.roles[key] ?? .offline
          binding.reasoningEffort = newValue
          settings.roles[key] = binding
        }
      )
    )
  }

  // MARK: - 动作

  private func load() {
    AppSettings.migrateLegacySettingsIfNeeded()
    settings = AppSettings.providerSettings
    if selectedProfileID == nil {
      selectedProfileID = settings.profiles.first?.id
    }
    loadKeyForSelection()
  }

  private func loadKeyForSelection() {
    probeState = .idle
    guard let id = selectedProfileID else {
      apiKeyDraft = ""
      return
    }
    apiKeyDraft = (try? KeychainStore.readAPIKey(forProfile: id)) ?? ""
  }

  private func addProfile(_ preset: ProviderPreset) {
    var profile = preset.makeProfile()
    if preset == .custom {
      profile.name = "自定义服务"
      profile.baseURL = "https://"
    }
    settings.profiles.append(profile)
    if settings.simpleProfileID == nil {
      settings.simpleProfileID = profile.id
    }
    selectedProfileID = profile.id
    apiKeyDraft = ""
    probeState = .idle
  }

  private func removeSelectedProfile() {
    guard let id = selectedProfileID else { return }
    settings.profiles.removeAll { $0.id == id }
    if settings.simpleProfileID == id {
      settings.simpleProfileID = settings.profiles.first?.id
    }
    // 密钥随档案一并删除，不留孤儿条目。
    try? KeychainStore.deleteAPIKey(forProfile: id)
    selectedProfileID = settings.profiles.first?.id
    loadKeyForSelection()
  }

  private func runProbe(profile: ProviderProfile) {
    probeState = .running
    let request = ProviderTestRequest(
      baseURL: profile.baseURL,
      model: profile.defaultModel,
      apiKey: apiKeyDraft.isEmpty ? nil : apiKeyDraft
    )
    Task {
      switch await model.testProvider(request) {
      case .completed(let value) where value.ok:
        let echoed = value.model.map { "（\($0)）" } ?? ""
        probeState = .success("连接成功 \(value.durationMs) ms \(echoed)")
      case .completed(let value):
        probeState = .failure(value.error ?? "连接失败")
      case .transportFailure(let message):
        probeState = .failure(message)
      }
    }
  }

  private func revealDataDirectory() {
    guard let url = try? BackendProcessController.applicationSupportDirectory() else {
      return
    }
    NSWorkspace.shared.activateFileViewerSelecting([url])
  }

  /// 把当前编辑状态落盘。改一下存一下，不依赖任何按钮。
  ///
  /// **保存和重启是两件事**，以前被混在一个按钮里。保存该是自动的；重启不能自动——
  /// 后端是在启动时读配置的，重启会掐掉正在进行的对话，那必须由用户决定什么时候做。
  private func persist() {
    saveKey(for: selectedProfileID)
    guard AppSettings.providerSettings != settings else { return }
    AppSettings.providerSettings = settings
    // 存下来了，但跑着的后端还是旧配置。这件事要说出来，否则用户会以为改完就生效了。
    needsRestart = true
  }

  /// 把编辑框里的密钥写进钥匙串。
  ///
  /// 空字符串会删掉已存的密钥——这是 `KeychainStore` 的既定语义，用户清空输入框就是
  /// 要删。但**新建档案时也是空的**，那种情况删一个本来就不存在的条目，无害。
  private func saveKey(for profileID: String?) {
    guard let profileID else { return }
    do {
      try KeychainStore.saveAPIKey(apiKeyDraft, forProfile: profileID)
    } catch {
      errorMessage = "无法保存 API Key：\(error.localizedDescription)"
    }
  }

  private func restartEngine() {
    persist()
    isRestarting = true
    Task {
      await model.restartBackend()
      isRestarting = false
      needsRestart = false
    }
  }
}
