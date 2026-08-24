#!/usr/bin/env node
//
// mock-messages-api.mjs - a loopback-only stand-in for the Anthropic Messages
// API, just capable enough to drive one full agentic turn of the extracted
// Claude Code artifact.
//
// WHY THIS EXISTS: docs/findings.md section 11 and docs/verification-2026-08-22.md
// carry end-to-end measurements ("as shipped, Read on a 3000x3000 PNG returns
// 'Unable to resize image ...'") that were taken through a mock living in /tmp.
// That mock is gone, so those numbers stopped being reproducible and one of
// them had to be retracted. Committing the harness is the fix.
//
// SAFETY: binds 127.0.0.1 only, on an ephemeral port by default. It is a test
// double, not a proxy - there is deliberately no upstream code path at all, so
// nothing it receives can leave the host. Measured: while it is listening,
// /proc/net/tcp shows local_address 0100007F (127.0.0.1) and no tcp6 row, and a
// connect() to this host's routable address on that port is refused while
// 127.0.0.1 accepts.
//
// Node's built-in http module only - no dependencies, per this repo's rules.
// Runs under `node` and under `~/.bun-1.3.14/bun` (both measured).
//
// Usage:
//   node scripts/mock-messages-api.mjs --tool bash
//   node scripts/mock-messages-api.mjs --tool read --tool-input '{"file_path":"/tmp/x.png"}'
//   node scripts/mock-messages-api.mjs --tool none        # text-only turn
//
// Flags (each has an env equivalent, so the same server can be driven from a
// shell script that cannot easily edit argv):
//   --tool NAME         NRC_MOCK_TOOL        bash|grep|read|none|<any tool name>
//   --tool-input JSON   NRC_MOCK_TOOL_INPUT  overrides the preset's input
//   --text STR          NRC_MOCK_TEXT        final assistant text (default MOCK-DONE)
//   --port N            NRC_MOCK_PORT        0 = ephemeral (default)
//   --log PATH          NRC_MOCK_LOG         append a one-line summary per request
//   --log-bodies PATH   NRC_MOCK_LOG_BODIES  append every request body as JSONL
//   --ready-file PATH   NRC_MOCK_READY_FILE  write the chosen port here once listening
//
// On listen it prints two lines to stdout, in this order:
//   PORT <n>
//   BASE_URL http://127.0.0.1:<n>
// A caller that wants the port without parsing stdout should use --ready-file.

import http from "node:http";
import fs from "node:fs";

// ---------------------------------------------------------------- arguments

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith("--")) continue;
    const eq = a.indexOf("=");
    if (eq !== -1) out[a.slice(2, eq)] = a.slice(eq + 1);
    else out[a.slice(2)] = argv[++i];
  }
  return out;
}

const args = parseArgs(process.argv.slice(2));
const opt = (name, env, dflt) =>
  args[name] !== undefined ? args[name] : process.env[env] !== undefined ? process.env[env] : dflt;

const TOOL = String(opt("tool", "NRC_MOCK_TOOL", "none"));
const TOOL_INPUT_RAW = opt("tool-input", "NRC_MOCK_TOOL_INPUT", null);
const FINAL_TEXT = String(opt("text", "NRC_MOCK_TEXT", "MOCK-DONE"));
const PORT = Number(opt("port", "NRC_MOCK_PORT", 0));
const LOG = opt("log", "NRC_MOCK_LOG", null);
const LOG_BODIES = opt("log-bodies", "NRC_MOCK_LOG_BODIES", null);
const READY_FILE = opt("ready-file", "NRC_MOCK_READY_FILE", null);

// Presets keyed by lowercase alias. `name` is the tool name as Claude Code
// registers it; sending anything else makes the CLI answer its own request with
// "No such tool available", which looks like a mock bug but is not one.
const PRESETS = {
  bash: {
    name: "Bash",
    input: {
      command: "echo HELLO-FROM-SUBPROCESS; uname -s",
      description: "mock probe",
    },
  },
  grep: {
    name: "Grep",
    // -n so the hit carries a line number, which is what makes a real match
    // distinguishable from the empty "No matches found" that the globally
    // flipped isStandaloneExecutable produces (findings.md section 11).
    // `scripts/ab-equivalence.sh --case grep` drives both answers out of the
    // same binary: hay/a.txt:1:NEEDLE-12345 on the shipped and scoped-shim
    // sides, "No matches found" on the globally flipped one.
    input: { pattern: "NEEDLE-12345", path: ".", output_mode: "content", "-n": true },
  },
  read: {
    name: "Read",
    input: { file_path: "/tmp/gradient-3000.png" },
  },
};

let toolCall = null;
if (TOOL && TOOL !== "none") {
  const preset = PRESETS[TOOL.toLowerCase()];
  toolCall = preset
    ? { name: preset.name, input: preset.input }
    : { name: TOOL, input: {} }; // unknown name: caller must supply --tool-input
  if (TOOL_INPUT_RAW) {
    try {
      toolCall.input = JSON.parse(TOOL_INPUT_RAW);
    } catch (e) {
      console.error(`mock: --tool-input is not JSON: ${e.message}`);
      process.exit(2);
    }
  }
}

function log(line) {
  if (LOG) fs.appendFileSync(LOG, line + "\n");
}

// Full bodies, opt-in: the system prompt and the tool schemas dominate them, so
// they run to tens of KB each and this is for "what is it actually asking for"
// debugging, not for routine runs.
//
// The schema counts below are exact - they are a property of the invocation -
// but the byte counts are deliberately approximate, because the size is not a
// constant even for one fixed invocation: the body carries the run's own paths.
// Measured 2026-08-23 by varying one thing at a time: +10 characters of cwd
// moved the Bash body 82,319 -> 82,339 (the cwd appears twice, once literally
// and once slug-encoded inside the memory path), and +1 character of
// CLAUDE_CONFIG_DIR moved it by exactly 1. That is why the three sides of
// scripts/ab-equivalence.sh disagree by a byte or two on the SAME case: their
// scratch homes are named home.<case>.asshipped / .shimmed / .global. At fixed
// paths a repeat run reproduces to the byte (measured twice).
//
// Measured on 2.1.222, the two turn POSTs of one `-p` run under the environment
// scripts/ab-equivalence.sh uses, from paths ~150 characters long under
// $TMPDIR: ~82.3 KB then ~82.6 KB carrying 24 tool schemas (Bash case; 82,319
// and 82,605 here), ~86.3 KB then ~86.6 KB carrying 26 (Grep case, which opts
// in to Grep and Glob; 86,300 and 86,561). Expect a few hundred bytes either
// way from shorter or longer paths - an exact-looking pair that used to stand
// here was ~300 bytes out for exactly that reason. Steadier is the step from
// the first turn to the second - the tool_result going back up: +286 bytes
// (Bash) on all three sides, and +261 (Grep) on the as-shipped and shimmed
// sides but +253 on the globally-flipped one, because that side's Grep is
// broken and returns the shorter "No matches found" instead of a real hit.
// Even this step is not a constant: it carries whatever the tool returned.
//
// The same Grep run WITHOUT CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1: a
// 2,248-byte session-title POST first (no tools, and no run-specific paths in
// it: it came out at exactly the 2,248 bytes handleMessages() documents below,
// measured from different paths than that figure was), then ~105.0 and ~105.3 KB
// carrying 29 - three more tools the CLI only offers once it is allowed to
// evaluate its feature gates. Note what that costs: run under ab-equivalence's
// egress poller, it opened sockets to 160.79.104.10:443 and 34.149.66.165:443,
// so it is not a loopback-only measurement and no case here uses it.
function logBody(method, url, bodyText) {
  if (!LOG_BODIES) return;
  fs.appendFileSync(LOG_BODIES, JSON.stringify({ method, url, body: bodyText }) + "\n");
}

// ------------------------------------------------------------ SSE plumbing

function sse(res, type, data) {
  // The `event:` line is what the real API sends, so it is sent here too - but
  // it is not what the client dispatches on. Measured by dropping it: a
  // data-only stream still drove the Bash turn to the same tool_result, i.e.
  // 2.1.222 reads data.type. Do not "simplify" this to data-only anyway; the
  // point of a mock is to look like the thing it stands in for.
  res.write(`event: ${type}\ndata: ${JSON.stringify(data)}\n\n`);
}

let messageSeq = 0;

function usage(outputTokens) {
  return {
    input_tokens: 1,
    output_tokens: outputTokens,
    cache_creation_input_tokens: 0,
    cache_read_input_tokens: 0,
  };
}

function messageStart(res, model) {
  messageSeq += 1;
  sse(res, "message_start", {
    type: "message_start",
    message: {
      id: `msg_mock_${messageSeq}`,
      type: "message",
      role: "assistant",
      model,
      content: [],
      stop_reason: null,
      stop_sequence: null,
      usage: usage(1),
    },
  });
}

function textBlock(res, index, text) {
  sse(res, "content_block_start", {
    type: "content_block_start",
    index,
    content_block: { type: "text", text: "" },
  });
  sse(res, "content_block_delta", {
    type: "content_block_delta",
    index,
    delta: { type: "text_delta", text },
  });
  sse(res, "content_block_stop", { type: "content_block_stop", index });
}

function toolUseBlock(res, index, id, name, input) {
  sse(res, "content_block_start", {
    type: "content_block_start",
    index,
    content_block: { type: "tool_use", id, name, input: {} },
  });
  // The tool input arrives as input_json_delta, not inline on
  // content_block_start. Measured by suppressing this delta: the turn then
  // reaches the tool with an empty input and comes back
  // "InputValidationError: ... required parameter `command` is missing", so
  // this is the path the CLI's partial-JSON accumulator really reads.
  sse(res, "content_block_delta", {
    type: "content_block_delta",
    index,
    delta: { type: "input_json_delta", partial_json: JSON.stringify(input) },
  });
  sse(res, "content_block_stop", { type: "content_block_stop", index });
}

function messageEnd(res, stopReason, outputTokens) {
  sse(res, "message_delta", {
    type: "message_delta",
    delta: { stop_reason: stopReason, stop_sequence: null },
    usage: usage(outputTokens),
  });
  sse(res, "message_stop", { type: "message_stop" });
  res.end();
}

// -------------------------------------------------------------- turn logic

// True once the transcript carries a tool_result, i.e. the CLI has run the tool
// we asked for and is coming back for the next assistant turn. Driving off the
// transcript rather than off "this is request number N" is deliberate, because
// N is not a property of the turn. Measured on 2.1.222, one `-p` run: a
// HEAD /api/hello, then THREE POSTs by default (a session-title request, the
// turn, the follow-up) but only TWO under
// CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1, which suppresses the title
// request - and that variable is exactly what scripts/ab-equivalence.sh sets to
// keep the run off the network. A failing request is re-sent on top of that. A
// counter would answer the wrong request in one configuration or the other and
// the loop would never reach its final text.
function transcriptHasToolResult(body) {
  const messages = Array.isArray(body?.messages) ? body.messages : [];
  for (const m of messages) {
    const content = m?.content;
    if (!Array.isArray(content)) continue;
    for (const block of content) {
      if (block && block.type === "tool_result") return true;
    }
  }
  return false;
}

function handleMessages(req, res, bodyText) {
  let body = {};
  try {
    body = JSON.parse(bodyText);
  } catch {
    /* fall through: a body we cannot parse still gets a valid, terminating turn
       rather than a hang, because a hang here costs the caller a full timeout */
  }
  const model = body.model || "claude-mock";
  // A turn is not the only thing the CLI asks for. Measured on 2.1.222 with
  // --log-bodies: with nonessential traffic left enabled, the first POST of a
  // `-p` run is a 2,248-byte session-title request carrying zero tools, a
  // "Write the title in the predominant language" instruction and the prompt
  // wrapped in <session>; answering *that* with a tool_use hands the title
  // generator a tool call it never offered. Tools present is the signal that
  // this request is the agentic loop, and it holds whether or not the title
  // request was suppressed.
  const offersTools = Array.isArray(body.tools) && body.tools.length > 0;
  const wantToolCall = toolCall !== null && offersTools && !transcriptHasToolResult(body);

  log(
    `REQ ${req.method} ${req.url} stream=${body.stream === true} ` +
      `msgs=${(body.messages || []).length} toolResult=${transcriptHasToolResult(body)} ` +
      `-> ${wantToolCall ? "tool_use:" + toolCall.name : "text"}`,
  );

  // Diagnostic only - we still send the tool_use. Measured on 2.1.222: the CLI
  // hides Grep and Glob from `tools` unless the invocation opts in (an
  // --allowedTools naming Grep or Glob sets its searchToolsOptIn flag) - 24
  // schemas offered without the opt-in, 26 with it - and a tool_use for a tool
  // it did not offer comes back as the tool_result "No such tool available:
  // Grep", i.e. a turn that completes with the wrong answer. This line is what
  // tells you that is what happened.
  if (wantToolCall) {
    const offered = body.tools.map((t) => t && t.name);
    if (!offered.includes(toolCall.name)) {
      log(`WARN client did not offer tool ${toolCall.name}; offered: ${offered.join(",")}`);
    }
  }

  if (body.stream !== true) {
    // Not hypothetical: when an early version of this mock answered the first
    // POST with 404, the CLI re-sent the identical body with `stream` absent.
    // A non-streaming request answered with an SSE body has no reason to parse.
    const content = wantToolCall
      ? [{ type: "tool_use", id: `toolu_mock_${++messageSeq}`, name: toolCall.name, input: toolCall.input }]
      : [{ type: "text", text: FINAL_TEXT }];
    const payload = {
      id: `msg_mock_${messageSeq}`,
      type: "message",
      role: "assistant",
      model,
      content,
      stop_reason: wantToolCall ? "tool_use" : "end_turn",
      stop_sequence: null,
      usage: usage(content.length),
    };
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify(payload));
    return;
  }

  res.writeHead(200, {
    "content-type": "text/event-stream",
    "cache-control": "no-cache",
    connection: "keep-alive",
  });

  messageStart(res, model);
  if (wantToolCall) {
    toolUseBlock(res, 0, `toolu_mock_${messageSeq}`, toolCall.name, toolCall.input);
    messageEnd(res, "tool_use", 8);
  } else {
    textBlock(res, 0, FINAL_TEXT);
    messageEnd(res, "end_turn", 4);
  }
}

// ------------------------------------------------------------------ server

const server = http.createServer((req, res) => {
  const chunks = [];
  req.on("data", (c) => chunks.push(c));
  req.on("end", () => {
    const bodyText = Buffer.concat(chunks).toString("utf8");
    const path = (req.url || "").split("?")[0];
    logBody(req.method, req.url, bodyText);

    // No run measured here reached this route. It is answered anyway because
    // the endpoint exists in the real API: whatever a 404 to it would do to a
    // turn would be a property of this harness, not of the artifact under test.
    if (path === "/v1/messages/count_tokens") {
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ input_tokens: 1 }));
      return;
    }

    if (path === "/v1/messages") {
      handleMessages(req, res, bodyText);
      return;
    }

    // 2.1.222 sends `HEAD /api/hello` before the first turn. Measured, by
    // making this route answer 404: the Bash turn still completed with the same
    // tool_result, so this is a connectivity probe the CLI does not gate on -
    // it is answered because leaving a 404 in a harness invites someone to
    // spend an afternoon on it, not because a turn needs it.
    if (path === "/api/hello") {
      res.writeHead(200, { "content-type": "application/json" });
      res.end("{}");
      return;
    }

    log(`UNHANDLED ${req.method} ${req.url} body=${bodyText.slice(0, 400)}`);
    res.writeHead(404, { "content-type": "application/json" });
    res.end(
      JSON.stringify({
        type: "error",
        error: { type: "not_found_error", message: `mock: no route for ${req.method} ${path}` },
      }),
    );
  });
});

server.listen(PORT, "127.0.0.1", () => {
  const port = server.address().port;
  process.stdout.write(`PORT ${port}\n`);
  process.stdout.write(`BASE_URL http://127.0.0.1:${port}\n`);
  if (READY_FILE) fs.writeFileSync(READY_FILE, String(port));
});

for (const sig of ["SIGINT", "SIGTERM"]) {
  process.on(sig, () => {
    server.close();
    process.exit(0);
  });
}
