// Serve the pdf.js worker from this app (same origin, so the CSP stays 'self').
import { copyFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const source = require.resolve("pdfjs-dist/build/pdf.worker.min.mjs");
const target = join(dirname(fileURLToPath(import.meta.url)), "..", "public", "pdf.worker.min.mjs");
mkdirSync(dirname(target), { recursive: true });
copyFileSync(source, target);
console.log("pdf.js worker copied to public/");
