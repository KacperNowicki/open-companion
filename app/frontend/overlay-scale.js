"use strict";

const BASE_OVERLAY_WIDTH = 360;
const BASE_OVERLAY_HEIGHT = 516;
const MIN_OVERLAY_SCALE_FACTOR = 0.9;
const MAX_OVERLAY_SCALE_FACTOR = 1.5;
const DEFAULT_COMPACT_OVERLAY_WIDTH = Math.round(BASE_OVERLAY_WIDTH * MIN_OVERLAY_SCALE_FACTOR);
const DEFAULT_COMPACT_OVERLAY_HEIGHT = Math.round(BASE_OVERLAY_HEIGHT * MIN_OVERLAY_SCALE_FACTOR);
const MIN_WIDTH = DEFAULT_COMPACT_OVERLAY_WIDTH;
const MIN_HEIGHT = DEFAULT_COMPACT_OVERLAY_HEIGHT;
const MID_WIDTH = BASE_OVERLAY_WIDTH;
const MID_HEIGHT = BASE_OVERLAY_HEIGHT;
const MAX_WIDTH = Math.round(BASE_OVERLAY_WIDTH * MAX_OVERLAY_SCALE_FACTOR);
const MAX_HEIGHT = Math.round(BASE_OVERLAY_HEIGHT * MAX_OVERLAY_SCALE_FACTOR);
const MIN_OVERLAY_SCALE = 0;
const MAX_OVERLAY_SCALE = 100;
const DEFAULT_OVERLAY_SCALE = 50;

function clamp(value, min, max) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return min;
  }
  return Math.max(min, Math.min(max, numeric));
}

function normalizeOverlayScale(value) {
  return Math.round(clamp(value, MIN_OVERLAY_SCALE, MAX_OVERLAY_SCALE));
}

function parseOverlayScaleValue(value, fallback = DEFAULT_OVERLAY_SCALE) {
  if (value == null) {
    return normalizeOverlayScale(fallback);
  }
  const text = typeof value === "string" ? value.trim() : value;
  if (text === "") {
    return normalizeOverlayScale(fallback);
  }
  const parsed = Number.parseInt(text, 10);
  if (!Number.isFinite(parsed)) {
    return normalizeOverlayScale(fallback);
  }
  return normalizeOverlayScale(parsed);
}

function lerp(start, end, amount) {
  return start + (end - start) * amount;
}

function unlerp(start, end, value) {
  if (end === start) {
    return 0;
  }
  return (value - start) / (end - start);
}

function overlayScaleFactor(value) {
  const scale = normalizeOverlayScale(value);
  if (scale <= DEFAULT_OVERLAY_SCALE) {
    const amount = scale / DEFAULT_OVERLAY_SCALE;
    return lerp(MIN_OVERLAY_SCALE_FACTOR, 1, amount);
  }
  const amount = (scale - DEFAULT_OVERLAY_SCALE) / (MAX_OVERLAY_SCALE - DEFAULT_OVERLAY_SCALE);
  return lerp(1, MAX_OVERLAY_SCALE_FACTOR, amount);
}

function dimensionsFromOverlayScale(value) {
  const factor = overlayScaleFactor(value);
  return {
    width: Math.round(BASE_OVERLAY_WIDTH * factor),
    height: Math.round(BASE_OVERLAY_HEIGHT * factor),
  };
}

function overlayScaleFromDimensions(width, height) {
  const normalizedWidth = clamp(width, MIN_WIDTH, MAX_WIDTH);
  const normalizedHeight = clamp(height, MIN_HEIGHT, MAX_HEIGHT);
  const averageFactor = ((normalizedWidth / BASE_OVERLAY_WIDTH) + (normalizedHeight / BASE_OVERLAY_HEIGHT)) / 2;
  if (averageFactor <= 1) {
    const amount = unlerp(MIN_OVERLAY_SCALE_FACTOR, 1, averageFactor);
    return normalizeOverlayScale(amount * DEFAULT_OVERLAY_SCALE);
  }
  const amount = unlerp(1, MAX_OVERLAY_SCALE_FACTOR, averageFactor);
  return normalizeOverlayScale(DEFAULT_OVERLAY_SCALE + amount * (MAX_OVERLAY_SCALE - DEFAULT_OVERLAY_SCALE));
}

module.exports = {
  BASE_OVERLAY_HEIGHT,
  BASE_OVERLAY_WIDTH,
  DEFAULT_COMPACT_OVERLAY_HEIGHT,
  DEFAULT_COMPACT_OVERLAY_WIDTH,
  DEFAULT_OVERLAY_SCALE,
  MAX_HEIGHT,
  MAX_OVERLAY_SCALE,
  MAX_WIDTH,
  MID_HEIGHT,
  MID_WIDTH,
  MIN_HEIGHT,
  MIN_OVERLAY_SCALE,
  MIN_WIDTH,
  dimensionsFromOverlayScale,
  normalizeOverlayScale,
  overlayScaleFromDimensions,
  overlayScaleFactor,
  parseOverlayScaleValue,
};
