const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const standalone = path.join(root, ".next", "standalone");
const server = path.join(standalone, "server.js");

if (!fs.existsSync(server)) {
  console.error("Standalone build not found. Run npm run build first.");
  process.exit(1);
}

for (const [source, destination] of [
  [path.join(root, "public"), path.join(standalone, "public")],
  [path.join(root, ".next", "static"), path.join(standalone, ".next", "static")],
]) {
  if (fs.existsSync(source)) {
    fs.cpSync(source, destination, { recursive: true, force: true });
  }
}

process.chdir(standalone);
require(server);
