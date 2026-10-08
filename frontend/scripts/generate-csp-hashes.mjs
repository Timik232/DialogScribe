import { createHash } from "node:crypto";
import { readdirSync, readFileSync, writeFileSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const buildDir = join(dirname(fileURLToPath(import.meta.url)), "..", "build");

const JS_SCRIPT_TYPES = new Set([
	"",
	"module",
	"text/javascript",
	"application/javascript",
	"text/ecmascript",
	"application/ecmascript",
]);

function walkHtmlFiles(dir) {
	const entries = readdirSync(dir, { withFileTypes: true });
	const files = [];
	for (const entry of entries) {
		const path = join(dir, entry.name);
		if (entry.isDirectory()) files.push(...walkHtmlFiles(path));
		else if (entry.isFile() && entry.name.endsWith(".html")) files.push(path);
	}
	return files;
}

const SCRIPT_RE = /<script\b([^>]*)>([\s\S]*?)<\/script\s*>/gi;

function inlineScriptBodies(html) {
	const bodies = [];
	for (const match of html.matchAll(SCRIPT_RE)) {
		const attrs = match[1];
		if (/\bsrc\s*=/i.test(attrs)) continue;
		const typeMatch = attrs.match(/\btype\s*=\s*["']?([^"'\s>]+)/i);
		const type = typeMatch ? typeMatch[1].toLowerCase() : "";
		if (!JS_SCRIPT_TYPES.has(type)) continue;
		bodies.push(match[2]);
	}
	return bodies;
}

function sha256Csp(body) {
	return `sha256-${createHash("sha256").update(body, "utf8").digest("base64")}`;
}

const htmlFiles = statSync(buildDir, { throwIfNoEntry: false }) ? walkHtmlFiles(buildDir) : [];
if (htmlFiles.length === 0) {
	console.error(`generate-csp-hashes: no .html files under ${buildDir}; nothing to hash`);
	process.exit(1);
}

const hashes = new Set();
for (const file of htmlFiles) {
	for (const body of inlineScriptBodies(readFileSync(file, "utf8"))) {
		hashes.add(sha256Csp(body));
	}
}

const outputPath = join(buildDir, "csp-hashes.json");
writeFileSync(outputPath, `${JSON.stringify([...hashes].sort(), null, "\t")}\n`);
console.log(`generate-csp-hashes: wrote ${hashes.size} sha256 hash(es) from ${htmlFiles.length} page(s) to ${outputPath}`);
