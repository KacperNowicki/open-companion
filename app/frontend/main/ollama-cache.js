const OLLAMA_BASE_URL = "http://localhost:11434";
const DEFAULT_CACHE_TTL_MS = 2000;
const SHOW_CACHE_TTL_MS = 10000;

function mapOllamaModelSummary(model) {
  if (!model) {
    return null;
  }
  return {
    name: model.name,
    size_gb: model.size_gb ?? (model.size ? (Number(model.size) / 1e9).toFixed(1) : null),
    modified_at: model.modified_at || null,
  };
}

function parseOllamaParameters(parametersStr) {
  const result = { temperature: null, topK: null, topP: null };
  if (!parametersStr || typeof parametersStr !== "string") return result;
  for (const line of parametersStr.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const spaceIdx = trimmed.indexOf(" ");
    if (spaceIdx === -1) continue;
    const key = trimmed.slice(0, spaceIdx).trim();
    const val = trimmed.slice(spaceIdx + 1).trim().replace(/^"(.*)"$/, "$1");
    if (key === "temperature") result.temperature = parseFloat(val) || null;
    else if (key === "top_k") result.topK = parseFloat(val) || null;
    else if (key === "top_p") result.topP = parseFloat(val) || null;
  }
  return result;
}

function extractOllamaContextLength(data) {
  const modelInfo = data?.model_info;
  const candidates = [];
  if (modelInfo && typeof modelInfo === "object") {
    for (const [key, value] of Object.entries(modelInfo)) {
      if (typeof key === "string" && key.endsWith(".context_length")) {
        candidates.push(value);
      }
    }
  }
  candidates.push(data?.parameters?.num_ctx);
  candidates.push(data?.details?.context_length);
  for (const candidate of candidates) {
    const value = Number(candidate);
    if (Number.isFinite(value) && value > 0) {
      return value;
    }
  }
  return null;
}

function buildEnrichedModelInfo(name, data) {
  const params = parseOllamaParameters(data?.parameters);
  return {
    name,
    architecture: data?.details?.family || null,
    parameters: data?.details?.parameter_size || null,
    contextLength: extractOllamaContextLength(data),
    embeddingLength: data?.model_info?.["llama.embedding_length"] || null,
    quantization: data?.details?.quantization_level || null,
    capabilities: Array.isArray(data?.capabilities) ? data.capabilities : [],
    sizeGB: null,
    temperature: params.temperature,
    topK: params.topK,
    topP: params.topP,
  };
}

function buildEnrichedModelInfoFromTestState(name, info) {
  return {
    name,
    architecture: info?.family || null,
    parameters: info?.parameter_size || null,
    contextLength: info?.context_length || null,
    embeddingLength: null,
    quantization: info?.quantization_level || null,
    capabilities: Array.isArray(info?.capabilities) ? info.capabilities : [],
    sizeGB: null,
    temperature: null,
    topK: null,
    topP: null,
  };
}

function buildSettingsModelInfoFromShowData(data) {
  return {
    family: data?.details?.family || null,
    parameter_size: data?.details?.parameter_size || null,
    quantization_level: data?.details?.quantization_level || null,
    context_length: extractOllamaContextLength(data),
    capabilities: Array.isArray(data?.capabilities) ? data.capabilities : [],
  };
}

function buildSettingsModelInfoFromTestState(info) {
  return {
    family: info?.family || null,
    parameter_size: info?.parameter_size || null,
    quantization_level: info?.quantization_level || null,
    context_length: info?.context_length || null,
    capabilities: Array.isArray(info?.capabilities) ? info.capabilities : [],
  };
}

function testShowInfo(testState, modelName) {
  const cleanName = String(modelName || "").trim();
  if (!cleanName) {
    return null;
  }
  return testState?.show?.[cleanName] || null;
}

function createOllamaRuntimeCache(options = {}) {
  const fetchImpl = options.fetchImpl || globalThis.fetch;
  const now = options.now || (() => Date.now());
  const ttlMs = Number.isFinite(options.ttlMs) ? options.ttlMs : DEFAULT_CACHE_TTL_MS;
  const showTtlMs = Number.isFinite(options.showTtlMs) ? options.showTtlMs : SHOW_CACHE_TTL_MS;
  const cache = new Map();

  async function fetchJson(url, requestOptions = {}) {
    if (typeof fetchImpl !== "function") {
      throw new Error("fetch is not available");
    }
    const response = await fetchImpl(url, requestOptions);
    if (!response.ok) {
      throw new Error(`Ollama returned ${response.status}`);
    }
    return response.json();
  }

  async function cachedJson(key, ttl, loader, force = false) {
    const current = now();
    const hit = cache.get(key);
    if (!force && hit && hit.expiresAt > current) {
      return hit.promise || hit.value;
    }

    const promise = Promise.resolve().then(loader);
    cache.set(key, { expiresAt: current + ttl, promise });
    try {
      const value = await promise;
      cache.set(key, { expiresAt: now() + ttl, value });
      return value;
    } catch (error) {
      cache.delete(key);
      throw error;
    }
  }

  async function getTags(options = {}) {
    return cachedJson("tags", ttlMs, () => fetchJson(`${OLLAMA_BASE_URL}/api/tags`), Boolean(options.force));
  }

  async function getPs(options = {}) {
    return cachedJson("ps", ttlMs, () => fetchJson(`${OLLAMA_BASE_URL}/api/ps`), Boolean(options.force));
  }

  async function getShowData(modelName, options = {}) {
    const cleanName = String(modelName || "").trim();
    if (!cleanName) {
      throw new Error("modelName is required");
    }
    return cachedJson(
      `show:${cleanName}`,
      showTtlMs,
      () => fetchJson(`${OLLAMA_BASE_URL}/api/show`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: cleanName }),
      }),
      Boolean(options.force)
    );
  }

  return {
    clear() {
      cache.clear();
    },

    async listModelSummaries(testState) {
      if (testState) {
        return Array.isArray(testState.models)
          ? testState.models.map(mapOllamaModelSummary).filter(Boolean)
          : [];
      }
      const json = await getTags();
      return (json.models || []).map(mapOllamaModelSummary).filter(Boolean);
    },

    async listRunningModels(testState) {
      if (testState) {
        return Array.isArray(testState.running_models) ? testState.running_models : [];
      }
      const data = await getPs();
      return data.models || [];
    },

    async getSettingsModelInfo(modelName, testState) {
      const cleanName = String(modelName || "").trim();
      if (!cleanName) {
        return null;
      }
      const testInfo = testShowInfo(testState, cleanName);
      if (testState) {
        return testInfo ? buildSettingsModelInfoFromTestState(testInfo) : null;
      }
      const data = await getShowData(cleanName);
      return buildSettingsModelInfoFromShowData(data);
    },

    async getContextLength(modelName, testState) {
      const testInfo = testShowInfo(testState, modelName);
      if (testState && !testInfo) {
        return null;
      }
      if (testInfo) {
        const value = Number(testInfo.context_length);
        return Number.isFinite(value) && value > 0 ? value : null;
      }
      const data = await getShowData(modelName);
      return extractOllamaContextLength(data);
    },

    async getRuntimeValidationInfo(modelName, testState) {
      const testInfo = testShowInfo(testState, modelName);
      if (testState && !testInfo) {
        return null;
      }
      if (testInfo) {
        return {
          capabilities: Array.isArray(testInfo.capabilities) ? testInfo.capabilities : [],
          contextLength: Number(testInfo.context_length || 0) || null,
          family: String(testInfo.family || "").trim(),
        };
      }
      const data = await getShowData(modelName);
      return {
        capabilities: Array.isArray(data.capabilities) ? data.capabilities : [],
        contextLength: extractOllamaContextLength(data),
        family: String(data.details?.family || "").trim(),
      };
    },

    async getEnrichedModelInfo(modelName, testState) {
      const cleanName = String(modelName || "").trim();
      if (!cleanName) {
        return null;
      }
      const testInfo = testShowInfo(testState, cleanName);
      if (testState) {
        return testInfo ? buildEnrichedModelInfoFromTestState(cleanName, testInfo) : null;
      }
      const data = await getShowData(cleanName);
      return buildEnrichedModelInfo(cleanName, data);
    },

    async listPulledModels(testState) {
      if (testState) {
        const models = Array.isArray(testState.models) ? testState.models : [];
        return models.map((model) => {
          const name = String(model?.name || "").trim();
          const info = testState.show?.[name] || {};
          const base = buildEnrichedModelInfoFromTestState(name, info);
          base.sizeGB = model.size_gb ?? (model.size ? (Number(model.size) / 1e9).toFixed(1) : null);
          base.modifiedAt = model.modified_at || null;
          return base;
        }).filter((model) => model.name);
      }

      const tagsData = await getTags();
      const tagModels = tagsData.models || [];
      return Promise.all(tagModels.map(async (model) => {
        const name = String(model.name || "").trim();
        const sizeGB = model.size ? (Number(model.size) / 1e9).toFixed(1) : null;
        const modifiedAt = model.modified_at || null;
        try {
          const showData = await getShowData(name);
          const base = buildEnrichedModelInfo(name, showData);
          base.sizeGB = sizeGB;
          base.modifiedAt = modifiedAt;
          return base;
        } catch {
          return {
            name,
            architecture: null,
            parameters: null,
            contextLength: null,
            embeddingLength: null,
            quantization: null,
            capabilities: [],
            sizeGB,
            modifiedAt,
            temperature: null,
            topK: null,
            topP: null,
          };
        }
      }));
    },

    getShowData,
    getTags,
    getPs,
  };
}

module.exports = {
  createOllamaRuntimeCache,
  extractOllamaContextLength,
  mapOllamaModelSummary,
};
