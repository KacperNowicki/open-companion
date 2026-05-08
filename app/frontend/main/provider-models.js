const CHATGPT_OAUTH_MODEL_CARDS = require("../../shared/chatgpt_oauth_models.json");

const OLLAMA_BACKED_PROVIDERS = new Set(["gemma", "qwen", "ollama"]);

const QWEN_CLOUD_MODEL_IDS = [
  "qwen3-max",
  "qwen3-max-preview",
  "qwen3.5-plus",
  "qwen3-235b-a22b",
  "qwen3-coder-plus",
  "qwen3.5-flash",
];

const GEMINI_MODEL_IDS = [
  "gemini-3.1-pro-preview",
  "gemini-3-pro-preview",
  "gemini-3-flash-preview",
  "gemini-2.5-pro",
  "gemini-2.5-flash",
  "gemini-2.0-flash",
];

const OPENROUTER_FALLBACK_MODEL_IDS = [
  "deepseek/deepseek-v4-pro",
  "deepseek/deepseek-v4-flash",
  "moonshotai/kimi-k2.6",
  "meta-llama/llama-3.3-70b-instruct",
  "google/gemini-2.5-flash",
  "anthropic/claude-sonnet-4",
  "qwen/qwen3-235b-a22b",
];

const OPENAI_MODEL_CARDS = [
  {
    id: "gpt-4.1",
    aliases: ["gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano"],
    label: "GPT-4.1",
    family: "openai_chat",
    preferredApi: "chat_completions",
    description: "General-purpose chat profile with tools, image input, and structured outputs.",
    supportsTools: true,
    supportsStructuredOutputs: true,
    supportsImageInput: true,
    contextWindow: 1000000,
    maxOutputTokens: 32000,
  },
  {
    id: "gpt-5.4",
    aliases: ["gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.4-pro"],
    label: "GPT-5.4",
    family: "openai_reasoning",
    preferredApi: "responses",
    description: "Native OpenAI reasoning profile with Responses API tool calling.",
    reasoningEfforts: ["none", "minimal", "low", "medium", "high", "xhigh"],
    defaultReasoningEffort: "none",
    defaultReasoningSummary: "auto",
    supportsTools: true,
    supportsStructuredOutputs: true,
    supportsImageInput: true,
    contextWindow: 1050000,
    maxOutputTokens: 128000,
    knowledgeCutoff: "2025-08-31",
  },
  {
    id: "gpt-5",
    aliases: ["gpt-5", "gpt-5-mini", "gpt-5-nano"],
    label: "GPT-5",
    family: "openai_reasoning",
    preferredApi: "responses",
    description: "Reasoning-oriented GPT-5 profile with native Responses support.",
    reasoningEfforts: ["none", "minimal", "low", "medium", "high", "xhigh"],
    defaultReasoningEffort: "none",
    defaultReasoningSummary: "auto",
    supportsTools: true,
    supportsStructuredOutputs: true,
    supportsImageInput: true,
    contextWindow: 400000,
    maxOutputTokens: 128000,
    knowledgeCutoff: "2024-06-01",
  },
  {
    id: "o4-mini",
    aliases: ["o4-mini"],
    label: "o4-mini",
    family: "openai_reasoning",
    preferredApi: "responses",
    description: "Compact OpenAI reasoning profile.",
    reasoningEfforts: ["low", "medium", "high"],
    defaultReasoningEffort: "medium",
    defaultReasoningSummary: "auto",
    supportsTools: true,
    supportsStructuredOutputs: true,
    supportsImageInput: true,
  },
  {
    id: "o3",
    aliases: ["o3", "o3-mini"],
    label: "o3",
    family: "openai_reasoning",
    preferredApi: "responses",
    description: "OpenAI reasoning model with native tool support.",
    reasoningEfforts: ["low", "medium", "high"],
    defaultReasoningEffort: "medium",
    defaultReasoningSummary: "auto",
    supportsTools: true,
    supportsStructuredOutputs: true,
    supportsImageInput: true,
  },
  {
    id: "gpt-4o",
    aliases: ["gpt-4o", "gpt-4o-mini"],
    label: "GPT-4o",
    family: "openai_chat",
    preferredApi: "chat_completions",
    description: "Multimodal chat profile with tool support.",
    supportsTools: true,
    supportsStructuredOutputs: true,
    supportsImageInput: true,
    contextWindow: 128000,
    maxOutputTokens: 16384,
  },
];

const OPENAI_LEGACY_FALLBACK_CARD = {
  id: "openai-legacy",
  aliases: [],
  label: "OpenAI legacy fallback",
  family: "openai_chat",
  preferredApi: "chat_completions",
  description: "Conservative compatibility profile for unrecognized OpenAI model ids.",
  supportsTools: true,
  supportsStructuredOutputs: false,
  supportsImageInput: false,
  contextWindow: null,
  maxOutputTokens: null,
  reasoningEfforts: [],
  defaultReasoningEffort: null,
  defaultReasoningSummary: null,
  knowledgeCutoff: null,
};

const CHATGPT_OAUTH_MODELS = Object.freeze(
  CHATGPT_OAUTH_MODEL_CARDS.map((card) => String(card?.id || "").trim()).filter(Boolean)
);

// Aliases are canonical short names only. Dated variants (e.g.
// claude-sonnet-4-5-20250514) are discovered at runtime via the
// /v1/models API and matched to cards through prefix matching.
const ANTHROPIC_MODEL_CARDS = [
  {
    id: "claude-opus-4",
    aliases: ["claude-opus-4"],
    label: "Claude Opus 4",
    family: "anthropic_claude_reasoning",
    preferredApi: "messages",
    description: "Most capable Claude model with extended thinking support.",
    reasoningEfforts: ["low", "medium", "high"],
    defaultReasoningEffort: "medium",
    supportsTools: true,
    supportsStructuredOutputs: false,
    supportsImageInput: true,
    contextWindow: 200000,
    maxOutputTokens: 32000,
    knowledgeCutoff: "2025-03-01",
  },
  {
    id: "claude-sonnet-4",
    aliases: ["claude-sonnet-4"],
    label: "Claude Sonnet 4",
    family: "anthropic_claude_reasoning",
    preferredApi: "messages",
    description: "High-capability Claude model with extended thinking support.",
    reasoningEfforts: ["low", "medium", "high"],
    defaultReasoningEffort: "medium",
    supportsTools: true,
    supportsStructuredOutputs: false,
    supportsImageInput: true,
    contextWindow: 200000,
    maxOutputTokens: 16000,
    knowledgeCutoff: "2025-03-01",
  },
  {
    id: "claude-haiku-4",
    aliases: ["claude-haiku-4"],
    label: "Claude Haiku 4",
    family: "anthropic_claude_chat",
    preferredApi: "messages",
    description: "Fast and compact Claude model for everyday tasks.",
    supportsTools: true,
    supportsStructuredOutputs: false,
    supportsImageInput: true,
    contextWindow: 200000,
    maxOutputTokens: 8192,
    knowledgeCutoff: "2025-03-01",
  },
  {
    id: "claude-3-5-sonnet",
    aliases: ["claude-3-5-sonnet"],
    label: "Claude 3.5 Sonnet",
    family: "anthropic_claude_chat",
    preferredApi: "messages",
    description: "Previous-generation Claude chat model.",
    supportsTools: true,
    supportsStructuredOutputs: false,
    supportsImageInput: true,
    contextWindow: 200000,
    maxOutputTokens: 8192,
    knowledgeCutoff: "2024-04-01",
  },
  {
    id: "claude-3-5-haiku",
    aliases: ["claude-3-5-haiku"],
    label: "Claude 3.5 Haiku",
    family: "anthropic_claude_chat",
    preferredApi: "messages",
    description: "Fast previous-generation Claude model.",
    supportsTools: true,
    supportsStructuredOutputs: false,
    supportsImageInput: true,
    contextWindow: 200000,
    maxOutputTokens: 8192,
    knowledgeCutoff: "2024-04-01",
  },
];

const ANTHROPIC_FALLBACK_CARD = {
  id: "anthropic-unknown",
  aliases: [],
  label: "Claude (unknown)",
  family: "anthropic_claude_chat",
  preferredApi: "messages",
  description: "Conservative fallback profile for unrecognized Anthropic model ids.",
  supportsTools: true,
  supportsStructuredOutputs: false,
  supportsImageInput: false,
  contextWindow: null,
  maxOutputTokens: null,
  reasoningEfforts: [],
  defaultReasoningEffort: null,
  knowledgeCutoff: null,
};

function isOllamaBackedProvider(provider) {
  return OLLAMA_BACKED_PROVIDERS.has(String(provider || "").trim().toLowerCase());
}

function getChatGptOauthModelCard(modelName) {
  const normalized = String(modelName || "").trim().toLowerCase();
  if (!normalized) {
    return CHATGPT_OAUTH_MODEL_CARDS[0] || null;
  }
  for (const card of CHATGPT_OAUTH_MODEL_CARDS) {
    const aliases = Array.isArray(card?.aliases) ? card.aliases : [card?.id];
    for (const aliasValue of aliases) {
      const alias = String(aliasValue || "").trim().toLowerCase();
      if (!alias) continue;
      if (normalized === alias || normalized.startsWith(`${alias}-`)) {
        return card;
      }
    }
  }
  return null;
}

function getAnthropicModelCard(modelName) {
  const normalized = String(modelName || "").trim().toLowerCase();
  if (!normalized) {
    return ANTHROPIC_FALLBACK_CARD;
  }
  for (const card of ANTHROPIC_MODEL_CARDS) {
    for (const alias of card.aliases || []) {
      const candidate = String(alias || "").trim().toLowerCase();
      if (!candidate) {
        continue;
      }
      if (normalized === candidate || normalized.startsWith(`${candidate}-`)) {
        return card;
      }
    }
  }
  return ANTHROPIC_FALLBACK_CARD;
}

function getOpenAiModelCard(modelName) {
  const normalized = String(modelName || "").trim().toLowerCase();
  if (!normalized) {
    return OPENAI_LEGACY_FALLBACK_CARD;
  }
  for (const card of OPENAI_MODEL_CARDS) {
    for (const alias of card.aliases || []) {
      const candidate = String(alias || "").trim().toLowerCase();
      if (!candidate) {
        continue;
      }
      if (normalized === candidate || normalized.startsWith(`${candidate}-`)) {
        return card;
      }
    }
  }
  return OPENAI_LEGACY_FALLBACK_CARD;
}

function getCuratedProviderModelIds(provider) {
  if (provider === "openai") {
    return OPENAI_MODEL_CARDS.flatMap((card) => card.aliases || []).filter(Boolean);
  }
  if (provider === "anthropic") {
    return ANTHROPIC_MODEL_CARDS.flatMap((card) => card.aliases || []).filter(Boolean);
  }
  if (provider === "gemini") {
    return [...GEMINI_MODEL_IDS];
  }
  if (provider === "qwen_cloud") {
    return [...QWEN_CLOUD_MODEL_IDS];
  }
  if (provider === "openrouter") {
    return [...OPENROUTER_FALLBACK_MODEL_IDS];
  }
  return [];
}

function mergeProviderModelIds(provider, discoveredModels) {
  const merged = [];
  const seen = new Set();
  const add = (value) => {
    const cleanValue = String(value || "").trim();
    if (!cleanValue) {
      return;
    }
    const key = cleanValue.toLowerCase();
    if (seen.has(key)) {
      return;
    }
    seen.add(key);
    merged.push(cleanValue);
  };

  const discovered = Array.isArray(discoveredModels) ? discoveredModels : [];
  const curated = getCuratedProviderModelIds(provider);

  if (discovered.length) {
    for (const model of discovered) {
      add(model);
    }
    for (const model of curated) {
      add(model);
    }
  } else {
    for (const model of curated) {
      add(model);
    }
  }
  return merged;
}

function detectModelFamily(modelName, explicitFamily = "") {
  const family = String(explicitFamily || "").trim().toLowerCase();
  if ([
    "google_gemma",
    "qwen",
    "openai_reasoning",
    "openai_chat",
    "anthropic_claude_reasoning",
    "anthropic_claude_chat",
  ].includes(family)) {
    return family;
  }
  const model = String(modelName || "").trim().toLowerCase();
  if (model.includes("gemma")) {
    return "google_gemma";
  }
  if (model.includes("qwen")) {
    return "qwen";
  }
  if (model.includes("claude")) {
    return getAnthropicModelCard(model).family;
  }
  return "generic";
}

module.exports = {
  ANTHROPIC_FALLBACK_CARD,
  CHATGPT_OAUTH_MODEL_CARDS,
  CHATGPT_OAUTH_MODELS,
  OPENAI_LEGACY_FALLBACK_CARD,
  QWEN_CLOUD_MODEL_IDS,
  detectModelFamily,
  getAnthropicModelCard,
  getChatGptOauthModelCard,
  getCuratedProviderModelIds,
  getOpenAiModelCard,
  isOllamaBackedProvider,
  mergeProviderModelIds,
};
