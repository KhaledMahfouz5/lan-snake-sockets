"use strict";

const POLL_INTERVAL_MS = 120;
const REQUEST_TIMEOUT_MS = 1000;

// Monochrome LCD shades and distinct hatch patterns per slot.
const SLOT_STYLES = {
  1: { label: "P1", fill: "#0f380f", pattern: "solid" },
  2: { label: "P2", fill: "#306230", pattern: "stripes" },
  3: { label: "P3", fill: "#1b431b", pattern: "dots" },
  4: { label: "P4", fill: "#234e23", pattern: "cross" },
};

const KEY_LABELS = {
  ArrowUp: "UP",
  ArrowDown: "DOWN",
  ArrowLeft: "LEFT",
  ArrowRight: "RIGHT",
};

const state = {
  token: null,
  playerId: null,
  slot: null,
  ownedKeys: [],
  numPlayers: null,
  latestGameState: null,
  pollTimerId: null,
  pollInFlight: false,
  consecutiveErrors: 0,
  lastKeyFlash: {},
};

const els = {
  statusBadge: document.getElementById("status-badge"),
  connectionBanner: document.getElementById("connection-banner"),
  errorBanner: document.getElementById("error-banner"),
  lobbyPanel: document.getElementById("lobby-panel"),
  joinForm: document.getElementById("join-form"),
  playerNameInput: document.getElementById("player-name"),
  numPlayersGroup: document.getElementById("num-players-group"),
  numPlayersSelect: document.getElementById("num-players"),
  joinBtn: document.getElementById("join-btn"),
  waitingBox: document.getElementById("waiting-box"),
  waitingText: document.getElementById("waiting-text"),
  lobbyPlayers: document.getElementById("lobby-players"),
  gamePanel: document.getElementById("game-panel"),
  canvas: document.getElementById("game-canvas"),
  yourSlot: document.getElementById("your-slot"),
  tickCounter: document.getElementById("tick-counter"),
  ownedKeysBox: document.getElementById("owned-keys-box"),
  scoreboardList: document.getElementById("scoreboard-list"),
  gameoverOverlay: document.getElementById("gameover-overlay"),
  winnerText: document.getElementById("winner-text"),
  finalScores: document.getElementById("final-scores"),
  resetBtn: document.getElementById("reset-btn"),
};

function showError(message) {
  if (!message) {
    els.errorBanner.textContent = "";
    els.errorBanner.classList.add("hidden");
    return;
  }
  els.errorBanner.textContent = message;
  els.errorBanner.classList.remove("hidden");
}

function setConnectionLost(lost) {
  if (lost) {
    els.connectionBanner.classList.remove("hidden");
  } else {
    els.connectionBanner.classList.add("hidden");
  }
}

async function fetchJsonWithTimeout(url, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(url, {
      ...options,
      cache: "no-store",
      signal: controller.signal,
    });
    const text = await response.text();
    let parsed = null;
    if (text) {
      parsed = JSON.parse(text);
    }
    return { ok: response.ok, status: response.status, data: parsed };
  } finally {
    clearTimeout(timer);
  }
}

function describeOwnedKey(key, numPlayers) {
  const arrowName = KEY_LABELS[key] || key;
  if (numPlayers === 1) {
    return `${arrowName} -> ${arrowName}`;
  }
  if (numPlayers === 2) {
    if (key === "ArrowUp" || key === "ArrowLeft") {
      return `${arrowName} -> TURN LEFT (CCW)`;
    }
    return `${arrowName} -> TURN RIGHT (CW)`;
  }
  if (numPlayers === 3) {
    if (key === "ArrowLeft") {
      return `${arrowName} -> TURN LEFT (CCW)`;
    }
    return `${arrowName} -> TURN RIGHT (CW)`;
  }
  // N === 4: each assigned arrow turns clockwise 90 deg.
  return `${arrowName} -> TURN RIGHT (CW)`;
}

function renderOwnedKeys() {
  els.ownedKeysBox.innerHTML = "";
  const count = state.numPlayers || 1;
  if (!state.ownedKeys || state.ownedKeys.length === 0) {
    const span = document.createElement("span");
    span.className = "key-chip";
    span.textContent = "NO ASSIGNED KEYS";
    els.ownedKeysBox.appendChild(span);
    return;
  }

  for (const key of state.ownedKeys) {
    const chip = document.createElement("span");
    chip.className = "key-chip";
    if (state.lastKeyFlash[key] && Date.now() - state.lastKeyFlash[key] < 160) {
      chip.classList.add("active-key");
    }
    chip.textContent = describeOwnedKey(key, count);
    els.ownedKeysBox.appendChild(chip);
  }
}

function inferSlotFromPlayerId(playerId) {
  if (!playerId || typeof playerId !== "string") {
    return 1;
  }
  const match = /^p([1-4])-/.exec(playerId);
  return match ? Number(match[1]) : 1;
}

function makePatternCanvas(styleMode, fgColor) {
  const pCanvas = document.createElement("canvas");
  pCanvas.width = 8;
  pCanvas.height = 8;
  const pCtx = pCanvas.getContext("2d");
  pCtx.fillStyle = "#9bbc0f";
  pCtx.fillRect(0, 0, 8, 8);
  pCtx.fillStyle = fgColor;

  if (styleMode === "solid") {
    pCtx.fillRect(0, 0, 8, 8);
  } else if (styleMode === "stripes") {
    pCtx.fillRect(0, 0, 8, 3);
    pCtx.fillRect(0, 4, 8, 3);
  } else if (styleMode === "dots") {
    pCtx.fillRect(1, 1, 3, 3);
    pCtx.fillRect(5, 5, 3, 3);
  } else {
    pCtx.fillRect(0, 0, 8, 2);
    pCtx.fillRect(0, 0, 2, 8);
  }
  return pCanvas;
}

function drawBoard(gameState) {
  const ctx = els.canvas.getContext("2d");
  const gridW = (gameState && gameState.grid && gameState.grid.w) || 30;
  const gridH = (gameState && gameState.grid && gameState.grid.h) || 20;

  const cellW = els.canvas.width / gridW;
  const cellH = els.canvas.height / gridH;

  // Clear LCD background.
  ctx.fillStyle = "#9bbc0f";
  ctx.fillRect(0, 0, els.canvas.width, els.canvas.height);

  // Subtle Nokia pixel grid lines.
  ctx.strokeStyle = "#8bac0f";
  ctx.lineWidth = 1;
  for (let x = 0; x <= gridW; x += 1) {
    const px = Math.floor(x * cellW) + 0.5;
    ctx.beginPath();
    ctx.moveTo(px, 0);
    ctx.lineTo(px, els.canvas.height);
    ctx.stroke();
  }
  for (let y = 0; y <= gridH; y += 1) {
    const py = Math.floor(y * cellH) + 0.5;
    ctx.beginPath();
    ctx.moveTo(0, py);
    ctx.lineTo(els.canvas.width, py);
    ctx.stroke();
  }

  if (!gameState) {
    return;
  }

  // Draw food items as crisp Nokia pips.
  const foods = Array.isArray(gameState.food) ? gameState.food : [];
  for (const item of foods) {
    if (!Array.isArray(item) || item.length < 2) {
      continue;
    }
    const [fx, fy] = item;
    const padX = Math.max(2, Math.floor(cellW * 0.2));
    const padY = Math.max(2, Math.floor(cellH * 0.2));
    ctx.fillStyle = "#0f380f";
    ctx.fillRect(
      Math.floor(fx * cellW) + padX,
      Math.floor(fy * cellH) + padY,
      Math.max(2, Math.floor(cellW - padX * 2)),
      Math.max(2, Math.floor(cellH - padY * 2))
    );
  }

  // Draw active snakes.
  const snakes = Array.isArray(gameState.snakes) ? gameState.snakes : [];
  for (const snake of snakes) {
    if (!snake || !snake.alive || !Array.isArray(snake.cells)) {
      continue;
    }
    const slotNum = inferSlotFromPlayerId(snake.player_id);
    const styleCfg = SLOT_STYLES[slotNum] || SLOT_STYLES[1];
    const patCanvas = makePatternCanvas(styleCfg.pattern, styleCfg.fill);
    const pattern = ctx.createPattern(patCanvas, "repeat");

    snake.cells.forEach((cell, idx) => {
      if (!Array.isArray(cell) || cell.length < 2) {
        return;
      }
      const [cx, cy] = cell;
      const x0 = Math.floor(cx * cellW) + 1;
      const y0 = Math.floor(cy * cellH) + 1;
      const w0 = Math.max(2, Math.floor(cellW) - 2);
      const h0 = Math.max(2, Math.floor(cellH) - 2);

      ctx.fillStyle = idx === 0 ? "#0f380f" : pattern || styleCfg.fill;
      ctx.fillRect(x0, y0, w0, h0);

      if (idx === 0) {
        ctx.strokeStyle = "#9bbc0f";
        ctx.lineWidth = 1;
        ctx.strokeRect(x0 + 1, y0 + 1, Math.max(1, w0 - 2), Math.max(1, h0 - 2));
      }
    });
  }
}

function renderScoreboard(players) {
  els.scoreboardList.innerHTML = "";
  const list = Array.isArray(players) ? players : [];
  for (const p of list) {
    const li = document.createElement("li");
    const slotNum = inferSlotFromPlayerId(p.id);
    const styleCfg = SLOT_STYLES[slotNum] || SLOT_STYLES[1];
    const isYou = state.playerId && p.id === state.playerId;

    li.className = "score-row";
    if (isYou) {
      li.classList.add("you-row");
    }
    if (!p.alive) {
      li.classList.add("dead-row");
    }

    const leftSpan = document.createElement("span");
    const swatch = document.createElement("span");
    swatch.className = "swatch";
    swatch.style.backgroundColor = styleCfg.fill;
    leftSpan.appendChild(swatch);

    const labelText = document.createTextNode(
      `${styleCfg.label} ${p.name}${isYou ? " (YOU)" : ""}`
    );
    leftSpan.appendChild(labelText);

    const scoreSpan = document.createElement("span");
    scoreSpan.textContent = `${p.score} PTS`;

    li.appendChild(leftSpan);
    li.appendChild(scoreSpan);
    els.scoreboardList.appendChild(li);
  }
}

function renderLobby(gameState) {
  const joined = gameState ? gameState.joined : 0;
  const needed = gameState ? gameState.needed : null;

  // Show player-count selector only when room has no joined players yet.
  if (!state.token && (!needed || joined === 0)) {
    els.numPlayersGroup.classList.remove("hidden");
  } else {
    els.numPlayersGroup.classList.add("hidden");
  }

  if (state.token) {
    els.joinBtn.disabled = true;
    els.playerNameInput.disabled = true;
    els.waitingBox.classList.remove("hidden");
    const targetDisplay = needed !== null && needed !== undefined ? needed : "?";
    els.waitingText.textContent = `WAITING FOR PLAYERS ${joined}/${targetDisplay}`;
  } else {
    els.joinBtn.disabled = false;
    els.playerNameInput.disabled = false;
    if (joined > 0 && needed) {
      els.waitingBox.classList.remove("hidden");
      els.waitingText.textContent = `WAITING FOR PLAYERS ${joined}/${needed} (ROOM ACTIVE)`;
    } else {
      els.waitingBox.classList.add("hidden");
    }
  }

  els.lobbyPlayers.innerHTML = "";
  const players = gameState && Array.isArray(gameState.players) ? gameState.players : [];
  for (const p of players) {
    const li = document.createElement("li");
    li.textContent = `${p.name} (${p.id})`;
    els.lobbyPlayers.appendChild(li);
  }
}

function renderGameOver(gameState) {
  els.gameoverOverlay.classList.remove("hidden");
  const winnerId = gameState.winner;
  const players = Array.isArray(gameState.players) ? gameState.players : [];
  const winnerPlayer = players.find((p) => p.id === winnerId);

  if (winnerPlayer) {
    const isYou = winnerPlayer.id === state.playerId;
    els.winnerText.textContent = isYou
      ? `WINNER: ${winnerPlayer.name} (YOU!)`
      : `WINNER: ${winnerPlayer.name}`;
  } else {
    els.winnerText.textContent = "RESULT: DRAW / ROUND ENDED";
  }

  els.finalScores.innerHTML = "";
  for (const p of players) {
    const li = document.createElement("li");
    li.textContent = `${p.name}: ${p.score} pts (${p.alive ? "Alive" : "Eliminated"})`;
    els.finalScores.appendChild(li);
  }

  // Only a player who joined with a token can trigger POST /api/reset.
  els.resetBtn.disabled = !state.token;
}

function applyGameState(gameState) {
  state.latestGameState = gameState;
  const phase = gameState.phase || "lobby";
  els.statusBadge.textContent = phase.toUpperCase();
  els.tickCounter.textContent = String(gameState.tick ?? 0);

  if (gameState.needed) {
    state.numPlayers = gameState.needed;
  }

  if (gameState.you) {
    state.playerId = gameState.you.player_id;
    state.slot = inferSlotFromPlayerId(state.playerId);
    if (Array.isArray(gameState.you.owned_keys)) {
      state.ownedKeys = gameState.you.owned_keys;
    }
  }

  if (phase === "lobby") {
    els.lobbyPanel.classList.remove("hidden");
    els.gamePanel.classList.add("hidden");
    els.gameoverOverlay.classList.add("hidden");
    renderLobby(gameState);
  } else if (phase === "running") {
    els.lobbyPanel.classList.add("hidden");
    els.gamePanel.classList.remove("hidden");
    els.gameoverOverlay.classList.add("hidden");
    els.yourSlot.textContent = state.slot ? `Player ${state.slot}` : "Spectator";
    renderOwnedKeys();
    renderScoreboard(gameState.players);
    drawBoard(gameState);
  } else if (phase === "finished") {
    els.lobbyPanel.classList.remove("hidden");
    els.gamePanel.classList.remove("hidden");
    renderLobby(gameState);
    renderScoreboard(gameState.players);
    drawBoard(gameState);
    renderGameOver(gameState);
  }
}

async function pollStateOnce() {
  // Never stack overlapping polling requests.
  if (state.pollInFlight) {
    return;
  }
  state.pollInFlight = true;

  try {
    const query = state.token ? `?token=${encodeURIComponent(state.token)}` : "";
    const res = await fetchJsonWithTimeout(`/api/state${query}`, { method: "GET" });

    if (!res.ok) {
      if (res.status === 401 && state.token) {
        // Room was reset or token became invalid.
        state.token = null;
        state.playerId = null;
        state.slot = null;
        state.ownedKeys = [];
      }
      state.consecutiveErrors += 1;
      if (state.consecutiveErrors >= 3) {
        setConnectionLost(true);
      }
      return;
    }

    state.consecutiveErrors = 0;
    setConnectionLost(false);
    if (res.data) {
      applyGameState(res.data);
    }
  } catch (_err) {
    state.consecutiveErrors += 1;
    if (state.consecutiveErrors >= 2) {
      setConnectionLost(true);
    }
  } finally {
    state.pollInFlight = false;
  }
}

async function handleJoinSubmit(event) {
  event.preventDefault();
  showError("");

  const nameVal = els.playerNameInput.value.trim() || "Player";
  const payload = { name: nameVal };

  if (!els.numPlayersGroup.classList.contains("hidden")) {
    payload.num_players = Number(els.numPlayersSelect.value);
  }

  els.joinBtn.disabled = true;
  try {
    const res = await fetchJsonWithTimeout("/api/join", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!res.ok || !res.data) {
      const msg = (res.data && res.data.error) || `Join failed (HTTP ${res.status}).`;
      showError(msg);
      els.joinBtn.disabled = false;
      return;
    }

    state.token = res.data.token;
    state.playerId = res.data.player_id;
    state.slot = res.data.slot;
    state.ownedKeys = Array.isArray(res.data.owned_keys) ? res.data.owned_keys : [];
    state.numPlayers = res.data.num_players;

    renderOwnedKeys();
    await pollStateOnce();
  } catch (_err) {
    showError("Network error while joining room.");
    els.joinBtn.disabled = false;
  }
}

async function sendArrowInput(key) {
  if (!state.token) {
    return;
  }
  // Browser enforces sending only keys owned by this player.
  if (!state.ownedKeys.includes(key)) {
    return;
  }

  state.lastKeyFlash[key] = Date.now();
  renderOwnedKeys();

  try {
    const res = await fetchJsonWithTimeout("/api/input", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: state.token, key }),
    });
    if (!res.ok && res.data && res.data.error && res.status !== 409) {
      showError(res.data.error);
    }
  } catch (_err) {
    setConnectionLost(true);
  }
}

async function handleResetClick() {
  if (!state.token) {
    return;
  }
  showError("");
  els.resetBtn.disabled = true;

  try {
    const res = await fetchJsonWithTimeout("/api/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: state.token }),
    });

    if (!res.ok) {
      const msg = (res.data && res.data.error) || `Reset failed (HTTP ${res.status}).`;
      showError(msg);
      els.resetBtn.disabled = false;
      return;
    }

    state.token = null;
    state.playerId = null;
    state.slot = null;
    state.ownedKeys = [];
    state.numPlayers = null;
    els.gameoverOverlay.classList.add("hidden");
    await pollStateOnce();
  } catch (_err) {
    showError("Network error while resetting room.");
    els.resetBtn.disabled = false;
  }
}

function handleKeyDown(event) {
  if (!Object.prototype.hasOwnProperty.call(KEY_LABELS, event.key)) {
    return;
  }
  event.preventDefault();

  const currentPhase = state.latestGameState && state.latestGameState.phase;
  if (currentPhase === "running" && state.ownedKeys.includes(event.key)) {
    void sendArrowInput(event.key);
  }
}

function init() {
  els.joinForm.addEventListener("submit", (ev) => {
    void handleJoinSubmit(ev);
  });
  els.resetBtn.addEventListener("click", () => {
    void handleResetClick();
  });
  window.addEventListener("keydown", handleKeyDown, { passive: false });

  drawBoard(null);
  void pollStateOnce();
  state.pollTimerId = setInterval(() => {
    void pollStateOnce();
  }, POLL_INTERVAL_MS);
}

init();