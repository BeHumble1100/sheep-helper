// Evaluate a read-only expression through the local WMPF-to-CDP bridge.
// The bridge is deliberately loopback-only; this helper never touches game RPCs.
"use strict";

const fs = require("fs");
const path = require("path");
const { WebSocket } = require(path.join(
  __dirname,
  "..",
  "vendor",
  "WMPFDebugger",
  "node_modules",
  "ws",
));

const args = process.argv.slice(2);
const inputIndex = args.indexOf("--input");
const outputIndex = args.indexOf("--output");
if (inputIndex < 0) {
  throw new Error("Usage: node tools/cdp_eval.cjs --input expression.js [--output result.json]");
}

const expression = fs.readFileSync(args[inputIndex + 1], "utf8");
const output = outputIndex < 0 ? null : args[outputIndex + 1];
const ws = new WebSocket("ws://127.0.0.1:62000");
let nextId = 1;
const pending = new Map();

function command(method, params = {}) {
  const id = nextId++;
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`CDP timeout for ${method}`));
    }, 15000);
    pending.set(id, { resolve, reject, timeout });
  });
}

ws.on("message", (wire) => {
  let message;
  try {
    message = JSON.parse(wire.toString());
  } catch {
    return;
  }
  const waiter = pending.get(message.id);
  if (!waiter) return;
  pending.delete(message.id);
  clearTimeout(waiter.timeout);
  if (message.error) waiter.reject(new Error(JSON.stringify(message.error)));
  else waiter.resolve(message.result);
});

ws.on("open", async () => {
  try {
    await command("Runtime.enable");
    const result = await command("Runtime.evaluate", {
      expression,
      returnByValue: true,
      awaitPromise: true,
      userGesture: false,
    });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    const value = result.result?.value;
    const serialized = JSON.stringify(value, null, 2);
    if (output) fs.writeFileSync(output, `${serialized}\n`, "utf8");
    else process.stdout.write(`${serialized}\n`);
    ws.close();
  } catch (error) {
    console.error(String(error));
    ws.close();
    process.exitCode = 1;
  }
});

ws.on("error", (error) => {
  console.error(String(error));
  process.exitCode = 1;
});
