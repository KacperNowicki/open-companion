const { SETTINGS_OPTIONS } = require("../app/shared/settings-options");

const DEFAULT_CONFIG = {
  version: "1.4.0",

  vault: {
    path: "./companion/vault",
    enabled: true
  },

  companion: {
    name: "Nova",
    user_name: "User",
    system_prompt_override: "",
    layer_visibility: {
      show_assistant_label: true
    },
    soul: {
      name: "Nova",
      pronouns: "she/her",
      identity: "Warm, curious, and slightly playful.",
      backstory: "",
      relationship: "Trusted companion.",
      user_name: "User",
      user_context: ""
    },
    pronouns: "she/her"
  },

  brain: {
    provider: "gemma",
    base_url: "http://localhost:11434/v1",
    api_url: "http://localhost:11434/v1",
    model: "",
    api_key: "",
    temperature: 0.8,
    context_window: "auto",
    max_tokens: 1024,
    stream: true,
    fallback_cpu: false,
    layers: {
      companion: {
        provider: "",
        model: "",
        temperature: "",
        max_tokens: "",
        reasoning_effort: "",
      },
      assistant: {
        provider: "",
        model: "",
        temperature: "",
        max_tokens: "",
        reasoning_effort: "",
      },
    }
  },

  providers: {
    custom: {
      base_url: "",
      model: ""
    }
  },

  memory: {
    enabled: true,
    write_back_enabled: true,
    extraction_source: "local",
    extraction_provider: "",
    extraction_base_url: "",
    extraction_model: "gemma4:e4b",
    extraction_mode: "local",
    embedding_enabled: true,
    embedding_model: "nomic-embed-text",
    max_context_memories: 5,
    layer_budgets: {
      companion: { percent: 0.15, min_tokens: 2048, max_tokens: 12000 },
      assistant: { percent: 0.2, min_tokens: 2048, max_tokens: 16000 }
    },
    top_k: 5,
    dream_enabled: true,
    dream_schedule: "session_start",
    max_entries: 200
  },

  context: {
    session_summaries: {
      enabled: true,
      max_recent: 6,
      max_injected: 2,
      compact_after_messages: 40,
      compact_after_tokens: 12000,
      budget_tokens: 1500,
      retain_recent_messages: 6
    },
    skills: {
      index_enabled: true,
      max_full_skills: 3,
      index_budget_tokens: 700,
      full_budget_tokens: 2200
    }
  },

  heartbeat: {
    enabled: false,
    interval: 1800,
    only_when_idle: false,
    idle_threshold_minutes: 5
  },

  voice: {
    tts_enabled: true,
    tts_provider: "kokoro",
    kokoro_voice: "af_nova",
    kokoro_model_path: "",
    kokoro_voices_path: "",
    tts_speed: 1.0,
    volume: 1.0,
    stt_enabled: true,
    stt_provider: "whisper",
    stt_model: "base.en",
    whisper_model: "base.en",
    language: "en-US",
    push_to_talk_key: "",
    auto_send_on_silence: true
  },

  ui: {
    always_on_top: true,
    transparent: true,
    overlay_scale: 50,
    window_width: 360,
    window_height: 516,
    selected_target_layer: "companion",
    avatar_image_path: "",
    avatar_model_path: "",
    layer_visibility: true,
    idle_timeout_seconds: 5,
    show_animation_label: true,
    layer_label_visibility: {
      show_assistant_label: true
    },
    theme: {
      mode: "dark",
      accent_rgb: [139, 92, 246]
    }
  },

  onboarding_complete: false,
  onboarding_reset_memory_on_next_launch: false,
  onboarding: {
    completed: false,
    modelsDownloaded: []
  },

  updater: {
    check_on_startup: true,
    last_check_ts: 0,
    dismissed_version: ""
  },

  layers: {
    companion: {
      enabled: true,
      persistent: true,
      permission_profile: "companion",
      display_name: "",
      identity_path: "companion/soul/active/soul_companion.md",
      max_tool_iterations: 20,
      brain: {}
    },
    assistant: {
      enabled: true,
      persistent: false,
      permission_profile: "assistant",
      display_name: "Assistant",
      identity_path: "companion/soul/active/soul_assistant.md",
      max_tool_iterations: 100,
      brain: {}
    }
  },

  tools: {
    overrides: {
      companion: {},
      assistant: {}
    },
    custom: []
  },

  settings_options: SETTINGS_OPTIONS
}

module.exports = { DEFAULT_CONFIG }
