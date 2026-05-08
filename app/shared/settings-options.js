const ONBOARDING_MODELS = [
  {
    id: "companion",
    tag: "gemma4:e4b",
    label: "Companion Model",
    desc: "Lightweight conversational model. Powers the persistent companion layer.",
    size: "~4 GB",
    layer: "companion",
  },
  {
    id: "assistant",
    tag: "gemma4:26b",
    label: "Assistant Model",
    desc: "Larger execution model. Handles complex tasks, file operations, and tool use.",
    size: "~17 GB",
    layer: "assistant",
  },
  {
    id: "embedding",
    tag: "nomic-embed-text",
    label: "Embedding Model",
    desc: "Tiny vector model for memory retrieval. Required for long-term memory.",
    size: "~270 MB",
    layer: "memory",
  },
];

const CURATED_OLLAMA_MODELS = [
  { name: "gemma4:e4b", description: "Google Gemma 4 - efficient 4B model", tags: ["e4b", "12b", "27b"] },
  { name: "gemma4:12b", description: "Google Gemma 4 - balanced 12B model", tags: ["12b", "27b"] },
  { name: "qwen2.5:7b", description: "Alibaba Qwen 2.5 - fast 7B model", tags: ["3b", "7b", "14b", "32b", "72b"] },
  { name: "llama3.2:3b", description: "Meta Llama 3.2 - lightweight 3B model", tags: ["1b", "3b"] },
  { name: "llama3.3:70b", description: "Meta Llama 3.3 - powerful 70B model", tags: ["70b"] },
  { name: "mistral:7b", description: "Mistral 7B - fast general-purpose model", tags: ["7b"] },
  { name: "phi4:14b", description: "Microsoft Phi-4 - reasoning 14B model", tags: ["14b", "mini"] },
  { name: "deepseek-r1:8b", description: "DeepSeek R1 - reasoning model", tags: ["1.5b", "7b", "8b", "14b", "32b", "70b"] },
  { name: "nomic-embed-text", description: "Nomic embed-text - embedding model", tags: ["latest"] },
];

const EMBEDDING_MODEL_HINTS = [
  "nomic-embed-text",
  "mxbai-embed-large",
  "all-minilm",
  "snowflake-arctic-embed",
  "granite-embedding",
  "bge-",
];

const MODEL_LAYERS = [
  { uiId: "companion", configKey: "companion" },
  { uiId: "assistant", configKey: "assistant", legacyConfigKey: "assistant_low" },
];

const CLOUD_PROVIDER_SPECS = {
  openai: {
    label: "OpenAI",
    keyAccount: "openai",
    keyLabel: "OpenAI API key",
    keyPlaceholder: "sk-...",
    models: [],
  },
  anthropic: {
    label: "Anthropic",
    keyAccount: "anthropic",
    keyLabel: "Anthropic API key",
    keyPlaceholder: "sk-ant-...",
    models: [],
  },
  gemini: {
    label: "Gemini",
    keyAccount: "gemini",
    keyLabel: "Gemini API key",
    keyPlaceholder: "AIza...",
    models: ["gemini-3.1-pro-preview", "gemini-3-pro-preview", "gemini-3-flash-preview", "gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash"],
  },
  openrouter: {
    label: "OpenRouter",
    keyAccount: "openrouter",
    keyLabel: "OpenRouter API key",
    keyPlaceholder: "sk-or-...",
    models: ["deepseek/deepseek-v4-pro", "deepseek/deepseek-v4-flash", "moonshotai/kimi-k2.6", "meta-llama/llama-3.3-70b-instruct", "google/gemini-2.5-flash", "anthropic/claude-sonnet-4", "qwen/qwen3-235b-a22b"],
  },
  qwen_cloud: {
    label: "Qwen DashScope",
    keyAccount: "qwen",
    keyLabel: "DashScope API key",
    keyPlaceholder: "sk-...",
    models: ["qwen3-max", "qwen3-max-preview", "qwen3.5-plus", "qwen3-235b-a22b", "qwen3-coder-plus", "qwen3.5-flash"],
  },
  custom: {
    label: "Custom",
    keyAccount: "custom",
    keyLabel: "Custom API key",
    keyPlaceholder: "Optional",
    models: [],
    customOnly: true,
    urlAccount: "custom_url",
  },
};

const CLOUD_PROVIDER_ORDER = ["anthropic", "chatgpt_oauth", "custom", "gemini", "openai", "openrouter", "qwen_cloud"];

const API_KEY_FIELD_MAP = {
  openai: "apikey-openai",
  anthropic: "apikey-anthropic",
  gemini: "apikey-gemini",
  openrouter: "apikey-openrouter",
  qwen: "apikey-qwen",
  qwen_cloud: "apikey-qwen",
  custom: "apikey-custom",
  custom_url: "apikey-custom-url",
};

const OLLAMA_BACKED_PROVIDERS = ["gemma", "qwen", "ollama"];

const HEARTBEAT_INTERVAL_OPTIONS = [
  { seconds: 600, label: "10 min" },
  { seconds: 1800, label: "30 min" },
  { seconds: 3600, label: "1 hour" },
  { seconds: 10800, label: "3 hours" },
  { seconds: 21600, label: "6 hours" },
];

const VOICE_GROUP_ORDER = [
  "US English / Female",
  "US English / Male",
  "British English / Female",
  "British English / Male",
];

const VOICES = [
  { id: "af_alloy", label: "Alloy", tag: "US English", group: "US English / Female" },
  { id: "af_aoede", label: "Aoede", tag: "US English", group: "US English / Female" },
  { id: "af_bella", label: "Bella", tag: "US English", group: "US English / Female" },
  { id: "af_heart", label: "Heart", tag: "US English", group: "US English / Female" },
  { id: "af_jessica", label: "Jessica", tag: "US English", group: "US English / Female" },
  { id: "af_kore", label: "Kore", tag: "US English", group: "US English / Female" },
  { id: "af_nicole", label: "Nicole", tag: "US English", group: "US English / Female" },
  { id: "af_nova", label: "Nova", tag: "US English", group: "US English / Female" },
  { id: "af_river", label: "River", tag: "US English", group: "US English / Female" },
  { id: "af_sarah", label: "Sarah", tag: "US English", group: "US English / Female" },
  { id: "af_sky", label: "Sky", tag: "US English", group: "US English / Female" },
  { id: "am_adam", label: "Adam", tag: "US English", group: "US English / Male" },
  { id: "am_echo", label: "Echo", tag: "US English", group: "US English / Male" },
  { id: "am_eric", label: "Eric", tag: "US English", group: "US English / Male" },
  { id: "am_fenrir", label: "Fenrir", tag: "US English", group: "US English / Male" },
  { id: "am_liam", label: "Liam", tag: "US English", group: "US English / Male" },
  { id: "am_michael", label: "Michael", tag: "US English", group: "US English / Male" },
  { id: "am_onyx", label: "Onyx", tag: "US English", group: "US English / Male" },
  { id: "am_puck", label: "Puck", tag: "US English", group: "US English / Male" },
  { id: "am_santa", label: "Santa", tag: "US English", group: "US English / Male" },
  { id: "bf_alice", label: "Alice", tag: "British English", group: "British English / Female" },
  { id: "bf_emma", label: "Emma", tag: "British English", group: "British English / Female" },
  { id: "bf_isabella", label: "Isabella", tag: "British English", group: "British English / Female" },
  { id: "bf_lily", label: "Lily", tag: "British English", group: "British English / Female" },
  { id: "bm_daniel", label: "Daniel", tag: "British English", group: "British English / Male" },
  { id: "bm_fable", label: "Fable", tag: "British English", group: "British English / Male" },
  { id: "bm_george", label: "George", tag: "British English", group: "British English / Male" },
  { id: "bm_lewis", label: "Lewis", tag: "British English", group: "British English / Male" },
];

const SETTINGS_OPTIONS = {
  onboarding_models: ONBOARDING_MODELS,
  curated_ollama_models: CURATED_OLLAMA_MODELS,
  embedding_model_hints: EMBEDDING_MODEL_HINTS,
  model_layers: MODEL_LAYERS,
  cloud_provider_specs: CLOUD_PROVIDER_SPECS,
  cloud_provider_order: CLOUD_PROVIDER_ORDER,
  api_key_field_map: API_KEY_FIELD_MAP,
  ollama_backed_providers: OLLAMA_BACKED_PROVIDERS,
  heartbeat_interval_options: HEARTBEAT_INTERVAL_OPTIONS,
  voice_group_order: VOICE_GROUP_ORDER,
  voices: VOICES,
  persona_soul_identity_block_marker: "## OpenCompanion Identity",
};

module.exports = {
  API_KEY_FIELD_MAP,
  CLOUD_PROVIDER_ORDER,
  CLOUD_PROVIDER_SPECS,
  CURATED_OLLAMA_MODELS,
  EMBEDDING_MODEL_HINTS,
  HEARTBEAT_INTERVAL_OPTIONS,
  MODEL_LAYERS,
  OLLAMA_BACKED_PROVIDERS,
  ONBOARDING_MODELS,
  SETTINGS_OPTIONS,
  VOICE_GROUP_ORDER,
  VOICES,
};
