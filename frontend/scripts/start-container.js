const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const server = path.join(root, "server.js");
const runtimePublic = path.join(root, "public");

if (!fs.existsSync(server)) {
  console.error("Standalone server not found. Build the frontend image first.");
  process.exit(1);
}

fs.mkdirSync(runtimePublic, { recursive: true });
const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
fs.writeFileSync(
  path.join(runtimePublic, "runtime-config.js"),
  `window.__FINSCOPE_CONFIG__ = ${JSON.stringify({ apiBase })};\n`,
  "utf8",
);

process.chdir(root);
require(server);
