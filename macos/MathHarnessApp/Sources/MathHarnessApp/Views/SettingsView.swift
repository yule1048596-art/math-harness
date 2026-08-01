import SwiftUI

struct SettingsView: View {
  @EnvironmentObject private var model: AppModel
  @AppStorage(AppSettingsKey.solverProvider) private var provider = SolverProvider.sympy.rawValue
  @AppStorage(AppSettingsKey.mimoBaseURL) private var baseURL = "https://api.xiaomimimo.com/v1"
  @AppStorage(AppSettingsKey.mimoModel) private var modelName = "mimo-v2.5-pro"
  @AppStorage(AppSettingsKey.mimoMethodExtraction) private var mimoMethodExtraction = true
  @AppStorage(AppSettingsKey.maxOutputTokens) private var maxOutputTokens = 3_000
  @State private var apiKey = ""
  @State private var keychainError: String?
  @State private var isRestarting = false

  var body: some View {
    Form {
      Section("求解器") {
        Picker("默认求解器", selection: $provider) {
          ForEach(SolverProvider.allCases) { item in
            Text(item.displayName).tag(item.rawValue)
          }
        }

        if provider == SolverProvider.mimo.rawValue {
          SecureField("MiMo API Key", text: $apiKey)
          TextField("Base URL", text: $baseURL)
          TextField("模型", text: $modelName)
          Toggle("求解后使用 MiMo 提炼方法", isOn: $mimoMethodExtraction)
          Text("自动整理数学目标会使用一次模型请求；方法提炼开启后，可记忆答案还会再使用一次。")
            .font(.caption)
            .foregroundStyle(.secondary)
          Text("密钥只保存在 macOS 钥匙串中，不会写入工作区数据库或项目文件。")
            .font(.caption)
            .foregroundStyle(.secondary)
        } else {
          Text("离线模式不发送网络请求，适合已提供结构化数学目标的问题。")
            .font(.caption)
            .foregroundStyle(.secondary)
        }
      }

      Section("生成") {
        Stepper(
          "最大输出 Token：\(maxOutputTokens)",
          value: $maxOutputTokens,
          in: 256...8_000,
          step: 256
        )
      }

      Section {
        HStack {
          Spacer()
          Button("应用并重启数学引擎") {
            applySettings()
          }
          .buttonStyle(.borderedProminent)
          .disabled(isRestarting)
        }
      }
    }
    .formStyle(.grouped)
    .padding(10)
    .frame(width: 520, height: 460)
    .onAppear(perform: loadAPIKey)
    .alert(
      "无法保存设置",
      isPresented: Binding(
        get: { keychainError != nil },
        set: { if !$0 { keychainError = nil } }
      )
    ) {
      Button("好") { keychainError = nil }
    } message: {
      Text(keychainError ?? "")
    }
  }

  private func loadAPIKey() {
    do {
      apiKey = try KeychainStore.readMiMoAPIKey() ?? ""
    } catch {
      keychainError = error.localizedDescription
    }
  }

  private func applySettings() {
    do {
      try KeychainStore.saveMiMoAPIKey(apiKey)
      isRestarting = true
      Task {
        await model.restartBackend()
        isRestarting = false
      }
    } catch {
      keychainError = error.localizedDescription
    }
  }
}
