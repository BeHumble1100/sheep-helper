// Local-only bridge adapted from WMPFDebugger (GPL-2.0-only).
// It translates the miniapp's private debug WebSocket into Chrome DevTools
// Protocol messages. It never records auth data or forwards traffic remotely.

"use strict";

const { EventEmitter } = require("events");
const path = require("path");
const root = path.resolve(__dirname, "..", "vendor", "WMPFDebugger");
const { WebSocket, WebSocketServer } = require(path.join(root, "node_modules", "ws"));
const codex = require(path.join(root, "src", "third-party", "RemoteDebugCodex.js"));
const messages = require(path.join(root, "src", "third-party", "WARemoteDebugProtobuf.js"));

const emitter = new EventEmitter();
let sequence = 0;

const debugServer = new WebSocketServer({ host: "127.0.0.1", port: 9421 });
const cdpServer = new WebSocketServer({ host: "127.0.0.1", port: 62000 });

debugServer.on("connection", (miniapp) => {
  console.log("miniapp-connected");
  miniapp.on("message", (wire) => {
    try {
      const decoded =
        messages.mmbizwxadevremote.WARemoteDebug_DebugMessage.decode(wire);
      const unwrapped = codex.unwrapDebugMessageData(decoded);
      if (unwrapped?.category === "chromeDevtoolsResult") {
        emitter.emit("cdp", unwrapped.data.payload);
      }
    } catch (error) {
      console.error("miniapp-decode-error", String(error));
    }
  });
  miniapp.on("close", () => console.log("miniapp-disconnected"));
});

cdpServer.on("connection", (client) => {
  console.log("cdp-client-connected");
  client.on("message", (message) => {
    const payload = {
      jscontext_id: "",
      op_id: ++sequence,
      payload: message.toString(),
    };
    const wrapped = codex.wrapDebugMessageData(payload, "chromeDevtools", 0);
    const outgoing = messages.mmbizwxadevremote.WARemoteDebug_DebugMessage.encode({
      seq: ++sequence,
      category: "chromeDevtools",
      data: wrapped.buffer,
      compressAlgo: 0,
      originalSize: wrapped.originalSize,
    }).finish();
    for (const socket of debugServer.clients) {
      if (socket.readyState === WebSocket.OPEN) socket.send(outgoing, { binary: true });
    }
  });
});

emitter.on("cdp", (message) => {
  for (const socket of cdpServer.clients) {
    if (socket.readyState === WebSocket.OPEN) socket.send(message);
  }
});

console.log("bridge-ready ws://127.0.0.1:62000");
