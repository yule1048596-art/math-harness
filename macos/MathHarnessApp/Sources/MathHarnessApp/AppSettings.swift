import Foundation

enum AppSettingsKey {
  static let solverProvider = "solverProvider"
  static let mimoBaseURL = "mimoBaseURL"
  static let mimoModel = "mimoModel"
  static let maxOutputTokens = "maxOutputTokens"
}

enum SolverProvider: String, CaseIterable, Identifiable {
  case sympy
  case mimo

  var id: String { rawValue }

  var displayName: String {
    switch self {
    case .sympy: "离线 SymPy"
    case .mimo: "小米 MiMo"
    }
  }
}

enum AppSettings {
  static var solverProvider: SolverProvider {
    let value = UserDefaults.standard.string(forKey: AppSettingsKey.solverProvider)
    return SolverProvider(rawValue: value ?? "") ?? .sympy
  }

  static var mimoBaseURL: String {
    UserDefaults.standard.string(forKey: AppSettingsKey.mimoBaseURL)
      ?? "https://api.xiaomimimo.com/v1"
  }

  static var mimoModel: String {
    UserDefaults.standard.string(forKey: AppSettingsKey.mimoModel)
      ?? "mimo-v2.5-pro"
  }

  static var maxOutputTokens: Int {
    let value = UserDefaults.standard.integer(forKey: AppSettingsKey.maxOutputTokens)
    return value == 0 ? 3_000 : value
  }
}
