#!/usr/bin/env node

const { randomUUID } = require("crypto");
const os = require("os");
const readline = require("readline");
const WebSocket = require("ws");

const DEFAULT_WS_URL = "wss://chatgpt.com/backend-api/codex/responses";
const DEFAULT_ORIGINATOR = "pi";
const DEFAULT_USER_AGENT = `pi (${os.platform()} ${os.release()}; ${os.arch()})`;
const DEFAULT_RESPONSES_WEBSOCKETS_BETA = "responses_websockets=2026-02-06";
const DONE_SENTINEL = "[DONE]";
const TERMINAL_EVENT_TYPES = new Set([
  "response.completed",
  "response.failed",
  "response.incomplete",
  "error",
]);

function sanitizeHeaderValue(value) {
  return String(value || "")
    .replace(/[\u0000-\u001F\u007F]/g, "")
    .trim();
}

function writeSseEvent(event) {
  process.stdout.write(`data: ${JSON.stringify(event)}\n\n`);
}

function writeSseDone() {
  process.stdout.write(`data: ${DONE_SENTINEL}\n\n`);
}

function buildHeaders(request) {
  const requestId = sanitizeHeaderValue(request.request_id || randomUUID());
  const sessionId = sanitizeHeaderValue(request.session_id || randomUUID());
  const accessToken = sanitizeHeaderValue(request.access_token || "");
  const accountId = sanitizeHeaderValue(request.account_id || "");
  const version = sanitizeHeaderValue(request.version || "1.0.0");
  const originator = sanitizeHeaderValue(request.originator || DEFAULT_ORIGINATOR);
  const headers = {
    Authorization: `Bearer ${accessToken}`,
    originator,
    "User-Agent": sanitizeHeaderValue(request.user_agent || DEFAULT_USER_AGENT),
    "x-client-request-id": requestId,
    "session_id": sessionId,
  };
  if (accountId) {
    headers["chatgpt-account-id"] = accountId;
  }
  if (
    request.beta_header !== false &&
    process.env.OPEN_COMPANION_RESPONSES_WS_BETA_HEADER !== "0"
  ) {
    headers["OpenAI-Beta"] = sanitizeHeaderValue(
      request.beta_header_value ||
      process.env.OPEN_COMPANION_RESPONSES_WS_BETA_HEADER ||
      DEFAULT_RESPONSES_WEBSOCKETS_BETA
    );
  }
  return headers;
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

function buildConnectionKey(request) {
  const originator = sanitizeHeaderValue(request.originator || DEFAULT_ORIGINATOR);
  const version = sanitizeHeaderValue(request.version || "1.0.0");
  return JSON.stringify({
    url: String(request.url || DEFAULT_WS_URL).trim() || DEFAULT_WS_URL,
    accessToken: sanitizeHeaderValue(request.access_token || ""),
    originator,
    version,
    userAgent: sanitizeHeaderValue(request.user_agent || DEFAULT_USER_AGENT),
    sessionId: sanitizeHeaderValue(request.session_id || ""),
    accountId: sanitizeHeaderValue(request.account_id || ""),
    betaHeader: request.beta_header !== false &&
      process.env.OPEN_COMPANION_RESPONSES_WS_BETA_HEADER !== "0",
    betaHeaderValue: sanitizeHeaderValue(
      request.beta_header_value ||
      process.env.OPEN_COMPANION_RESPONSES_WS_BETA_HEADER ||
      DEFAULT_RESPONSES_WEBSOCKETS_BETA
    ),
  });
}

function isTerminalEvent(event) {
  return TERMINAL_EVENT_TYPES.has(String(event && event.type ? event.type : ""));
}

class ResponsesWebSocketBridge {
  constructor() {
    this.ws = null;
    this.connectionKey = "";
    this.inFlight = null;
    this.closed = false;
  }

  async close() {
    this.closed = true;
    const ws = this.ws;
    this.ws = null;
    this.connectionKey = "";
    if (ws) {
      try {
        ws.removeAllListeners("message");
        ws.removeAllListeners("error");
        ws.removeAllListeners("close");
        ws.close();
      } catch {
        // Ignore cleanup failures during shutdown.
      }
    }
  }

  async ensureConnected(request) {
    const nextKey = buildConnectionKey(request);
    if (
      this.ws &&
      this.ws.readyState === WebSocket.OPEN &&
      this.connectionKey === nextKey
    ) {
      return;
    }

    await this.close();
    this.closed = false;

    const wsUrl = String(request.url || DEFAULT_WS_URL).trim() || DEFAULT_WS_URL;
    const headers = buildHeaders(request);

    if (!sanitizeHeaderValue(request.access_token || "")) {
      throw new Error("Missing ChatGPT OAuth access token.");
    }

    this.ws = await new Promise((resolve, reject) => {
      const ws = new WebSocket(wsUrl, {
        headers,
        handshakeTimeout: 30_000,
      });

      const cleanup = () => {
        ws.removeListener("open", onOpen);
        ws.removeListener("error", onError);
        ws.removeListener("unexpected-response", onUnexpectedResponse);
      };
      const onOpen = () => {
        cleanup();
        resolve(ws);
      };
      const onError = (error) => {
        cleanup();
        reject(error);
      };
      const onUnexpectedResponse = (_request, response) => {
        cleanup();
        const statusCode = response && response.statusCode ? response.statusCode : "unknown";
        let body = "";
        response.setEncoding("utf8");
        response.on("data", (chunk) => {
          body += chunk;
        });
        response.on("end", () => {
          const detail = body.trim() ? `: ${body.trim().slice(0, 500)}` : "";
          const error = new Error(`Unexpected server response: ${statusCode}${detail}`);
          error.statusCode = Number(statusCode) || 0;
          error.body = body;
          reject(error);
        });
        response.on("error", (error) => {
          reject(error);
        });
      };

      ws.once("open", onOpen);
      ws.once("error", onError);
      ws.once("unexpected-response", onUnexpectedResponse);
    });

    this.connectionKey = nextKey;
    this.ws.on("message", (data) => this.handleMessage(data));
    this.ws.on("error", (error) => this.handleSocketError(error));
    this.ws.on("close", (code, reasonBuffer) => this.handleClose(code, reasonBuffer));
  }

  handleMessage(data) {
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
      this.writeTurnError(`Invalid websocket payload: ${String(error.message || error)}`);
      return;
    }

    writeSseEvent(event);

    if (isTerminalEvent(event)) {
      writeSseDone();
      this.finishTurn();
    }
  }

  handleSocketError(error) {
    this.writeTurnError(String(error && error.message ? error.message : error));
    this.resetConnection();
  }

  handleClose(code, reasonBuffer) {
    if (this.closed) {
      return;
    }
    const reason = Buffer.isBuffer(reasonBuffer) ? reasonBuffer.toString("utf8") : String(reasonBuffer || "");
    this.writeTurnError(`WebSocket closed before completion (code=${code}, reason=${reason || "unknown"})`, code);
    this.resetConnection();
  }

  resetConnection() {
    const ws = this.ws;
    this.ws = null;
    this.connectionKey = "";
    if (ws) {
      try {
        ws.removeAllListeners("message");
        ws.removeAllListeners("error");
        ws.removeAllListeners("close");
        ws.close();
      } catch {
        // Ignore cleanup failures during reset.
      }
    }
  }

  writeTurnError(message, code) {
    if (!this.inFlight) {
      return;
    }
    writeSseEvent({
      type: "error",
      code,
      message,
    });
    writeSseDone();
    this.finishTurn();
  }

  finishTurn() {
    const current = this.inFlight;
    this.inFlight = null;
    if (current) {
      current.resolve();
    }
  }

  async handleRequest(request) {
    if (request && request.type === "bridge.close") {
      await this.close();
      return;
    }

    const payload = buildPayload(request || {});
    if (!payload.model) {
      throw new Error("Missing ChatGPT OAuth websocket model.");
    }

    await this.ensureConnected(request || {});

    await new Promise((resolve) => {
      this.inFlight = { resolve };
      this.ws.send(JSON.stringify(payload), (error) => {
        if (error) {
          this.writeTurnError(String(error && error.message ? error.message : error));
          this.resetConnection();
        }
      });
    });
  }
}

async function main() {
  const bridge = new ResponsesWebSocketBridge();
  const rl = readline.createInterface({
    input: process.stdin,
    crlfDelay: Infinity,
  });

  for await (const line of rl) {
    const clean = String(line || "").trim();
    if (!clean) {
      continue;
    }
    let request;
    try {
      request = JSON.parse(clean);
    } catch (error) {
      writeSseEvent({
        type: "error",
        message: `Invalid bridge request: ${String(error.message || error)}`,
      });
      writeSseDone();
      continue;
    }

    try {
      await bridge.handleRequest(request);
    } catch (error) {
      writeSseEvent({
        type: "error",
        status: error && error.statusCode ? error.statusCode : undefined,
        message: String(error && error.message ? error.message : error),
      });
      writeSseDone();
      bridge.resetConnection();
    }
  }

  await bridge.close();
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
  DEFAULT_ORIGINATOR,
  DEFAULT_RESPONSES_WEBSOCKETS_BETA,
  DONE_SENTINEL,
  TERMINAL_EVENT_TYPES,
  ResponsesWebSocketBridge,
  buildHeaders,
  buildConnectionKey,
  buildPayload,
  isTerminalEvent,
  readRequestFromStdin,
  sanitizeHeaderValue,
  writeSseDone,
};
