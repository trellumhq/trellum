/**
 * Cloudflare Worker: the edge half of the portal's `edge-signed` read path.
 *
 * The portal (Django) decides who may see a report and, on success, sets a
 * short-lived `trellum_grant` cookie: an Ed25519 signature over `{scope, exp}`,
 * scoped to one report's content prefix. This Worker sits in front of the R2
 * bucket and does the ENFORCING: it verifies that grant on every request and
 * serves the object, or refuses. It can only ever *verify* — it holds the
 * public key, never the private one, so a leak of this Worker's config cannot
 * be used to forge a grant.
 *
 * Deploy (wrangler.toml sketch):
 *
 *   name = "report-access"
 *   main = "report-access-worker.js"
 *   compatibility_date = "2025-01-01"
 *   [[r2_buckets]]
 *   binding = "REPORTS"
 *   bucket_name = "trellum-reports"
 *   [vars]
 *   SIGNING_PUBLIC_KEY = "<base64 of the 32-byte Ed25519 public key>"
 *
 * Get SIGNING_PUBLIC_KEY from `python manage.py doctor` (the "report access"
 * line prints it) or `cdn.public_key_b64()`. Route this Worker at
 * `https://<content-host>/content/*`. The R2 bucket must have NO public access
 * — this Worker is the only reader.
 *
 * Requests to `/content/{org}/{studio}/{slug}/builds/{build}/...` map 1:1 onto
 * bucket keys after `/content/` is stripped. Build URLs are immutable, so the
 * successful response is cached long; nothing else is.
 */

const PREFIX = "/content/";

function deny(status, msg) {
  return new Response(msg + "\n", {
    status,
    headers: { "content-type": "text/plain", "cache-control": "no-store" },
  });
}

// Keep in step with trellum.artifacts.is_private_artifact at HTTP/upload boundaries.
function isPrivateArtifact(path) {
  while (path.includes("%")) {
    try {
      const decoded = decodeURIComponent(path);
      if (decoded === path) break;
      path = decoded;
    } catch {
      return true;
    }
  }
  return path.replace(/\\/g, "/").split("/").some((part) => {
    let name = part.split(":", 1)[0].replace(/[ .]+$/, "").toLowerCase();
    if (name.endsWith(".gz")) name = name.slice(0, -3).replace(/[ .]+$/, "");
    return name === "_live_queries.json";
  });
}

function b64urlToBytes(s) {
  s = s.replace(/-/g, "+").replace(/_/g, "/");
  while (s.length % 4) s += "=";
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function b64ToBytes(s) {
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function readCookie(header, name) {
  if (!header) return null;
  for (const part of header.split(";")) {
    const [k, ...v] = part.trim().split("=");
    if (k === name) return v.join("=");
  }
  return null;
}

let PUBLIC_KEY = null;
async function publicKey(env) {
  if (PUBLIC_KEY) return PUBLIC_KEY;
  PUBLIC_KEY = await crypto.subtle.importKey(
    "raw",
    b64ToBytes(env.SIGNING_PUBLIC_KEY),
    { name: "Ed25519" },
    false,
    ["verify"],
  );
  return PUBLIC_KEY;
}

/** Verify the grant and return its scope, or null. */
async function grantScope(env, token, pathname) {
  if (!token || token.indexOf(".") < 0) return null;
  const [payloadB64, sigB64] = token.split(".");
  let payloadBytes, sig;
  try {
    payloadBytes = b64urlToBytes(payloadB64);
    sig = b64urlToBytes(sigB64);
  } catch {
    return null;
  }
  const ok = await crypto.subtle.verify(
    { name: "Ed25519" },
    await publicKey(env),
    sig,
    payloadBytes,
  );
  if (!ok) return null;

  let payload;
  try {
    payload = JSON.parse(new TextDecoder().decode(payloadBytes));
  } catch {
    return null;
  }
  if (!payload.scope || typeof payload.exp !== "number") return null;
  if (payload.exp < Math.floor(Date.now() / 1000)) return null;
  // The scope is a path prefix WITH a trailing slash, so `casino/` can never
  // match `casino-secret/...`. The request must fall inside it.
  if (!pathname.startsWith(payload.scope)) return null;
  return payload.scope;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (!url.pathname.startsWith(PREFIX)) return deny(404, "not found");
    if (request.method !== "GET" && request.method !== "HEAD") {
      return deny(405, "method not allowed");
    }

    let key;
    try {
      key = decodeURIComponent(url.pathname.slice(PREFIX.length));
    } catch {
      return deny(404, "not found");
    }
    if (!key || key.endsWith("/") || key.includes("..") || key.includes("\\") ||
        key.startsWith("_private/") || isPrivateArtifact(key)) {
      return deny(404, "not found");
    }

    const token = readCookie(request.headers.get("cookie"), "trellum_grant");
    const scope = await grantScope(env, token, url.pathname);
    if (!scope) return deny(403, "forbidden");

    // Decoding must not move the key outside the scope that was verified.
    if (!(PREFIX + key).startsWith(scope)) {
      return deny(404, "not found");
    }

    const object = await env.REPORTS.get(key);
    if (object === null) return deny(404, "not found");

    const headers = new Headers();
    object.writeHttpMetadata(headers);
    headers.set("etag", object.httpEtag);
    // Immutable build content (the build id is in the key) may cache long;
    // the portal never routes the mutable `_current` pointer through here.
    if (!headers.has("cache-control")) {
      headers.set("cache-control", "private, max-age=3600");
    }
    return new Response(request.method === "HEAD" ? null : object.body, { headers });
  },
};
