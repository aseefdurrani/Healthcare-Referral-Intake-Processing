import { createRequire } from "node:module";
import { cpSync, mkdirSync } from "node:fs";

const require = createRequire(import.meta.url);
const worker = require.resolve("pdfjs-dist/build/pdf.worker.min.mjs");
mkdirSync("public", { recursive: true });
cpSync(worker, "public/pdf.worker.min.mjs");
