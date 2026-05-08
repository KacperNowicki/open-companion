const { DEFAULT_CONFIG } = require('./defaults')

function describeLegacyPersonality(config) {
  const companion = config.companion && typeof config.companion === 'object' ? config.companion : {}
  const soul = companion.soul && typeof companion.soul === 'object' ? companion.soul : {}

  const identity = String(soul.identity || '').trim()
  if (identity) {
    return identity
  }

  const customPersonality = String(soul.personality_custom || '').trim()
  if (String(soul.personality || '').trim().toLowerCase() === 'custom' && customPersonality) {
    return customPersonality
  }

  const mappedSoulPersonality = {
    warm: 'Warm, curious, and slightly playful.',
    direct: 'Direct, clear, and grounded.',
    playful: 'Playful, witty, and upbeat.',
    serious: 'Serious, composed, and thoughtful.',
  }[String(soul.personality || '').trim().toLowerCase()]
  if (mappedSoulPersonality) {
    return mappedSoulPersonality
  }

  const legacyPersonality = String(companion.personality || '').trim()
  if (legacyPersonality) {
    return legacyPersonality
  }

  const mappedTone = {
    warm: 'Warm, curious, and slightly playful.',
    direct: 'Direct, clear, and grounded.',
    playful: 'Playful, witty, and upbeat.',
    serious: 'Serious, composed, and thoughtful.',
  }[String(companion.personality_tone || '').trim().toLowerCase()]
  if (mappedTone) {
    return mappedTone
  }

  return DEFAULT_CONFIG.companion.soul.identity
}

function describeLegacyRelationship(config) {
  const companion = config.companion && typeof config.companion === 'object' ? config.companion : {}
  const soul = companion.soul && typeof companion.soul === 'object' ? companion.soul : {}

  const currentRelationship = String(soul.relationship || '').trim()
  const customRelationship = String(soul.relationship_custom || '').trim()
  if (currentRelationship.toLowerCase() === 'custom' && customRelationship) {
    return customRelationship
  }

  const mappedRelationship = {
    companion: 'Trusted companion.',
    collaborator: 'Close collaborator.',
    assistant: 'Professional assistant.',
  }[currentRelationship.toLowerCase()]
  if (mappedRelationship) {
    return mappedRelationship
  }
  if (currentRelationship) {
    return currentRelationship
  }

  const legacyRelationship = String(companion.relationship || '').trim()
  if (legacyRelationship) {
    return legacyRelationship
  }

  return DEFAULT_CONFIG.companion.soul.relationship
}

function normalizeBrain(config) {
  if (!config.brain || typeof config.brain !== 'object') {
    config.brain = {}
  }
  if (config.brain.api_url && !config.brain.base_url) {
    config.brain.base_url = config.brain.api_url
  }
  if (config.brain.base_url && !config.brain.api_url) {
    config.brain.api_url = config.brain.base_url
  }
  if (config.brain.context_window == null) {
    config.brain.context_window = DEFAULT_CONFIG.brain.context_window
  }
  const layers = config.brain.layers && typeof config.brain.layers === 'object'
    ? { ...config.brain.layers }
    : {}
  const assistantLayer = layers.assistant || layers.assistant_low || layers.assistant_high || layers.pc_doctor || {}
  const normalizeLayerProvider = (layer) => {
    const next = { ...(layer || {}) }
    return next
  }
  config.brain.layers = {
    companion: { ...DEFAULT_CONFIG.brain.layers.companion, ...normalizeLayerProvider(layers.companion) },
    assistant: { ...DEFAULT_CONFIG.brain.layers.assistant, ...normalizeLayerProvider(assistantLayer) },
  }
}

function normalizeSoul(config) {
  if (!config.companion || typeof config.companion !== 'object') {
    config.companion = {}
  }

  const companion = config.companion
  const legacySoul = companion.soul && typeof companion.soul === 'object'
    ? { ...companion.soul }
    : {}

  const nextSoul = {
    name: String(legacySoul.name || companion.name || DEFAULT_CONFIG.companion.soul.name).trim() || DEFAULT_CONFIG.companion.soul.name,
    pronouns: String(legacySoul.pronouns || companion.pronouns || DEFAULT_CONFIG.companion.soul.pronouns).trim() || DEFAULT_CONFIG.companion.soul.pronouns,
    identity: describeLegacyPersonality(config),
    backstory: String(legacySoul.backstory || companion.backstory || '').trim(),
    relationship: describeLegacyRelationship(config),
    user_name: String(legacySoul.user_name || companion.user_name || DEFAULT_CONFIG.companion.soul.user_name).trim() || DEFAULT_CONFIG.companion.soul.user_name,
    user_context: String(legacySoul.user_context || legacySoul.user_description || companion.user_description || '').trim(),
  }

  config.companion.soul = nextSoul
  config.companion.name = nextSoul.name
  config.companion.user_name = nextSoul.user_name
  config.companion.pronouns = nextSoul.pronouns
  delete config.companion.age
  delete config.companion.personality
  delete config.companion.communication_style
  delete config.companion.backstory
  delete config.companion.relationship
  delete config.companion.user_description
  delete config.companion.personality_tone
}

function normalizeHeartbeat(config) {
  const heartbeat = config.heartbeat && typeof config.heartbeat === 'object'
    ? { ...config.heartbeat }
    : {}
  const legacyVision = config.vision && typeof config.vision === 'object'
    ? config.vision
    : {}

  const legacyEnabled = legacyVision.heartbeat_enabled
  const legacyInterval = legacyVision.heartbeat_interval

  config.heartbeat = {
    ...DEFAULT_CONFIG.heartbeat,
    ...heartbeat,
    enabled: heartbeat.enabled != null ? Boolean(heartbeat.enabled) : Boolean(legacyEnabled),
    interval: heartbeat.interval != null
      ? Number(heartbeat.interval) || DEFAULT_CONFIG.heartbeat.interval
      : Number(legacyInterval) || DEFAULT_CONFIG.heartbeat.interval,
    only_when_idle: heartbeat.only_when_idle != null
      ? Boolean(heartbeat.only_when_idle)
      : Boolean(legacyVision.only_when_idle),
    idle_threshold_minutes: heartbeat.idle_threshold_minutes != null
      ? Math.max(1, Number(heartbeat.idle_threshold_minutes) || DEFAULT_CONFIG.heartbeat.idle_threshold_minutes)
      : Math.max(1, Number(legacyVision.idle_threshold_minutes) || DEFAULT_CONFIG.heartbeat.idle_threshold_minutes),
  }

  delete config.vision
  delete config.tool_rag
  delete config.animations
}

function normalizeMemory(config) {
  if (!config.memory || typeof config.memory !== 'object') {
    config.memory = {}
  }
  const extractionSource = String(config.memory.extraction_source || '').trim().toLowerCase()
  if (!['brain', 'local', 'api'].includes(extractionSource)) {
    const legacyMode = String(config.memory.extraction_mode || '').trim().toLowerCase()
    config.memory.extraction_source = legacyMode === 'provider'
      ? 'brain'
      : DEFAULT_CONFIG.memory.extraction_source
  }
  if (config.memory.extraction_provider == null) {
    config.memory.extraction_provider = DEFAULT_CONFIG.memory.extraction_provider
  }
  if (config.memory.extraction_base_url == null) {
    config.memory.extraction_base_url = DEFAULT_CONFIG.memory.extraction_base_url
  }
  if (config.memory.extraction_model == null) {
    config.memory.extraction_model = DEFAULT_CONFIG.memory.extraction_model
  }
  config.memory.extraction_mode = config.memory.extraction_source === 'local' ? 'local' : 'provider'
  const budgets = config.memory.layer_budgets && typeof config.memory.layer_budgets === 'object'
    ? { ...config.memory.layer_budgets }
    : {}
  const assistantBudget = budgets.assistant || budgets.assistant_low || budgets.assistant_high || budgets.pc_doctor || DEFAULT_CONFIG.memory.layer_budgets.assistant
  config.memory.layer_budgets = {
    companion: { ...DEFAULT_CONFIG.memory.layer_budgets.companion, ...(budgets.companion || {}) },
    assistant: { ...DEFAULT_CONFIG.memory.layer_budgets.assistant, ...assistantBudget },
  }
}

function normalizeContext(config) {
  if (!config.context || typeof config.context !== 'object') {
    config.context = {}
  }
  if (!config.context.session_summaries || typeof config.context.session_summaries !== 'object') {
    config.context.session_summaries = {}
  }
  if (!config.context.skills || typeof config.context.skills !== 'object') {
    config.context.skills = {}
  }
  config.context = {
    session_summaries: {
      ...DEFAULT_CONFIG.context.session_summaries,
      ...config.context.session_summaries,
    },
    skills: {
      ...DEFAULT_CONFIG.context.skills,
      ...config.context.skills,
    },
  }
}

function normalizeUi(config) {
  if (!config.ui || typeof config.ui !== 'object') {
    config.ui = {}
  }
  if (config.ui.show_animation_label == null) {
    config.ui.show_animation_label = DEFAULT_CONFIG.ui.show_animation_label
  }
  if (config.ui.overlay_scale == null) {
    config.ui.overlay_scale = DEFAULT_CONFIG.ui.overlay_scale
  }
  const selected = String(config.ui.selected_target_layer || DEFAULT_CONFIG.ui.selected_target_layer).trim()
  config.ui.selected_target_layer = selected === 'companion' ? 'companion' : 'assistant'
}

function normalizeOnboarding(config) {
  if (!config.onboarding || typeof config.onboarding !== 'object' || Array.isArray(config.onboarding)) {
    config.onboarding = {}
  }
  const completed = config.onboarding.completed != null
    ? Boolean(config.onboarding.completed)
    : Boolean(config.onboarding_complete)
  const modelsDownloaded = Array.isArray(config.onboarding.modelsDownloaded)
    ? config.onboarding.modelsDownloaded
        .map((model) => String(model || '').trim())
        .filter(Boolean)
    : []

  config.onboarding = {
    ...DEFAULT_CONFIG.onboarding,
    ...config.onboarding,
    completed,
    modelsDownloaded: Array.from(new Set(modelsDownloaded)),
  }
  if (config.onboarding_complete == null) {
    config.onboarding_complete = completed
  }
}

function normalizeLayersBlock(config) {
  if (!config.layers || typeof config.layers !== 'object') {
    config.layers = {}
  }
  const layers = { ...config.layers }
  const assistantLayer = layers.assistant || layers.assistant_low || layers.assistant_high || layers.pc_doctor || {}
  const nextCompanion = { ...DEFAULT_CONFIG.layers.companion, ...(layers.companion || {}) }
  const nextAssistant = { ...DEFAULT_CONFIG.layers.assistant, ...assistantLayer }
  delete nextCompanion.policy_path
  delete nextAssistant.policy_path
  config.layers = {
    companion: nextCompanion,
    assistant: nextAssistant,
  }
}

function normalizeTools(config) {
  if (!config.tools || typeof config.tools !== 'object') {
    config.tools = { overrides: {}, custom: [] }
  }
  if (!config.tools.overrides || typeof config.tools.overrides !== 'object') {
    config.tools.overrides = {}
  }
  const overrides = config.tools.overrides
  const assistantOverride = overrides.assistant || overrides.assistant_low || overrides.assistant_high || overrides.pc_doctor || {}
  config.tools.overrides = {
    companion: { ...(overrides.companion || {}) },
    assistant: { ...assistantOverride },
  }
  if (!Array.isArray(config.tools.custom)) {
    config.tools.custom = []
  }
}

const MIGRATIONS = [
  {
    from_version: null,
    to_version: '1.0.0',
    migrate: (config) => ({ ...(config || {}) })
  },
  {
    from_version: '1.0.0',
    to_version: '1.1.0',
    migrate: (config) => ({ ...(config || {}) })
  },
  {
    from_version: '1.1.0',
    to_version: '1.2.0',
    migrate: (config) => ({ ...(config || {}) })
  },
  {
    from_version: '1.2.0',
    to_version: '1.3.0',
    migrate: (config) => ({ ...(config || {}) })
  },
  {
    from_version: '1.3.0',
    to_version: '1.4.0',
    migrate: (config) => ({ ...(config || {}) })
  }
]

function runMigrations(userConfig) {
  let config = { ...(userConfig || {}) }
  let currentVersion = config.version || null
  const visited = new Set()

  while (!visited.has(currentVersion)) {
    visited.add(currentVersion)
    const migration = MIGRATIONS.find((entry) => entry.from_version === currentVersion)
    if (!migration) {
      break
    }
    config = migration.migrate(config)
    config.version = migration.to_version
    currentVersion = config.version || null
  }

  if (!config.version) {
    config.version = DEFAULT_CONFIG.version
  }

  normalizeBrain(config)
  normalizeSoul(config)
  normalizeMemory(config)
  normalizeContext(config)
  normalizeHeartbeat(config)
  normalizeUi(config)
  normalizeOnboarding(config)
  normalizeLayersBlock(config)
  normalizeTools(config)

  return config
}

module.exports = { MIGRATIONS, runMigrations }
