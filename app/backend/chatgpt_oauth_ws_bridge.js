#!/usr/bin/env node

const { randomUUID } = require("crypto");
const WebSocket = require("ws");

const DEFAULT_WS_URL = "wss://api.openai.com/v1/responses";

function sanitizeHeaderValue(value) {
  return String(value || "")
    .replace(/[\u0000-\u001F\u007F]/g, "")
    .trim();
}

function writeSseEvent(event) {
  process.stdout.write(`data: ${JSON.stringify(event)}\n\n`);
}

function buildHeaders(request) {
  const requestId = sanitizeHeaderValue(request.request_id || randomUUID());
  const sessionId = sanitizeHeaderValue(request.session_id || randomUUID());
  const accessToken = sanitizeHeaderValue(request.access_token || "");
  const version = sanitizeHeaderValue(request.version || "1.0.0");
  const originator = sanitizeHeaderValue(request.originator || "openclaw");
  return {
    Authorization: `Bearer ${accessToken}`,
    "OpenAI-Beta": "responses-websocket=v1",
    originator,
    version,
    "User-Agent": sanitizeHeaderValue(
      request.user_agent || `${originator}/${version}`,
    ),
    Origin: "https://chatgpt.com",
    Referer: "https://chatgpt.com/",
    "x-client-request-id": requestId,
    "x-openclaw-session-id": sessionId,
  };
}

function buildPayload(request) {
  const payload = {
    type: "response.create",
    model: String(request.model || "").trim(),
    input: Array.isArray(request.input) ? request.input : [],
    store: false,
  };

  if (request.instructions) {
    payload.instructions = String(request.instructions);
  }
  if (Array.isArray(request.tools) && request.tools.length > 0) {
    payload.tools = request.tools;
  }
  if (request.tool_choice) {
    payload.tool_choice = request.tool_choice;
  }
  if (request.previous_response_id) {
    payload.previous_response_id = String(request.previous_response_id).trim();
  }
  if (typeof request.temperature === "number") {
    payload.temperature = request.temperature;
  }
  if (Number.isInteger(request.max_output_tokens) && request.max_output_tokens > 0) {
    payload.max_output_tokens = request.max_output_tokens;
  }
  if (request.metadata && typeof request.metadata === "object") {
    payload.metadata = request.metadata;
  }
  return payload;
}

function readRequestFromStdin() {
  return new Promise((resolve, reject) => {
    let chunks = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => {
      chunks += chunk;
    });
    process.stdin.on("end", () => {
      try {
        resolve(JSON.parse(chunks || "{}"));
      } catch (error) {
        reject(error);
      }
    });
    process.stdin.on("error", reject);
  });
}

async function main() {
  const request = await readRequestFromStdin();
  const wsUrl = String(request.url || DEFAULT_WS_URL).trim() || DEFAULT_WS_URL;
  const headers = buildHeaders(request);
  const payload = buildPayload(request);

  if (!payload.model) {
    throw new Error("Missing ChatGPT OAuth websocket model.");
  }
  if (!headers.Authorization || headers.Authorization === "Bearer") {
    throw new Error("Missing ChatGPT OAuth access token.");
  }

  const ws = new WebSocket(wsUrl, {
    headers,
    handshakeTimeout: 30_000,
  });

  let finalized = false;

  const finalize = (code) => {
    if (finalized) {
      return;
    }
    finalized = true;
    try {
      ws.close();
    } catch {
      // Ignore cleanup failures during shutdown.
    }
    setTimeout(() => {
      process.exit(code);
    }, 10);
  };

  ws.on("open", () => {
    ws.send(JSON.stringify(payload));
  });

  ws.on("message", (data) => {
    let text = "";
    if (typeof data === "string") {
      text = data;
    } else if (Buffer.isBuffer(data)) {
      text = data.toString("utf8");
    } else {
      text = String(data);
    }

    let event;
    try {
      event = JSON.parse(text);
    } catch (error) {
      writeSseEvent({
        type: "error",
        message: `Invalid websocket payload: ${String(error.message || error)}`,
      });
      finalize(1);
      return;
    }

    writeSseEvent(event);

    if (
      event.type === "response.completed" ||
      event.type === "response.failed" ||
      event.type === "error"
    ) {
      finalize(event.type === "response.completed" ? 0 : 1);
    }
  });

  ws.on("error", (error) => {
    writeSseEvent({
      type: "error",
      message: String(error && error.message ? error.message : error),
    });
    finalize(1);
  });

  ws.on("close", (code, reasonBuffer) => {
    if (finalized) {
      return;
    }
    const reason = Buffer.isBuffer(reasonBuffer) ? reasonBuffer.toString("utf8") : String(reasonBuffer || "");
    writeSseEvent({
      type: "error",
      code,
      message: `WebSocket closed before completion (code=${code}, reason=${reason || "unknown"})`,
    });
    finalize(1);
  });
}

if (require.main === module) {
  main().catch((error) => {
    writeSseEvent({
      type: "error",
      message: String(error && error.message ? error.message : error),
    });
    process.exit(1);
  });
}

module.exports = {
  DEFAULT_WS_URL,
  buildHeaders,
  buildPayload,
  sanitizeHeaderValue,
};
