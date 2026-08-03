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
  @AppStorage(AppSettingsKey.maxOutputTokens) private var maxOutputTokens = 3_000
  @AppStorage(AppSettingsKey.verificationRepair) private var verificationRepair = true
  @AppStorage(AppSettingsKey.verificationFallback) private var verificationFallback = true
  @AppStorage(AppSettingsKey.appearance) private var appearance = AppAppearance.system
    .rawValue

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
      generalTab
        .tabItem { Label("通用", systemImage: "gearshape") }
      aboutTab
        .tabItem { Label("关于", systemImage: "info.circle") }
    }
    .frame(width: 640, height: 560)
    .onAppear(perform: load)
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
      .onChange(of: selectedProfileID) { _, _ in loadKeyForSelection() }
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

      Section("外观") {
        Picker("主题", selection: $appearance) {
          ForEach(AppAppearance.allCases) { item in
            Text(item.displayName).tag(item.rawValue)
          }
        }
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
        HStack {
          Spacer()
          Button("应用并重启数学引擎") { apply() }
            .buttonStyle(.borderedProminent)
            .disabled(isRestarting)
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
    apiKeyDraft = (try? KeychainStore.readAPIKey(forProfile: id)) as? String ?? ""
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

  private func apply() {
    do {
      if let id = selectedProfileID {
        try KeychainStore.saveAPIKey(apiKeyDraft, forProfile: id)
      }
      AppSettings.providerSettings = settings
      isRestarting = true
      Task {
        await model.restartBackend()
        isRestarting = false
      }
    } catch {
      errorMessage = error.localizedDescription
    }
  }
}
