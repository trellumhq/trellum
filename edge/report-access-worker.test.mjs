// Exercise the actual edge fetch handler with valid grants and a fake bucket.
import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import { readFile } from "node:fs/promises";

if (!globalThis.crypto) globalThis.crypto = webcrypto;
const source = await readFile(new URL("./report-access-worker.js", import.meta.url), "utf8");
const { default: worker } = await import("data:text/javascript;base64," + Buffer.from(source).toString("base64"));
const keys = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
const scope = "/content/demo/casino/sales/builds/b1/";
const payload = Buffer.from(JSON.stringify({ scope, exp: Math.floor(Date.now() / 1000) + 300 }));
const signature = await crypto.subtle.sign("Ed25519", keys.privateKey, payload);
const grant = payload.toString("base64url") + "." + Buffer.from(signature).toString("base64url");
const gets = [];
const env = {
  SIGNING_PUBLIC_KEY: Buffer.from(await crypto.subtle.exportKey("raw", keys.publicKey)).toString("base64"),
  REPORTS: { async get(key) {
    gets.push(key);
    return { body: "public", httpEtag: "test", writeHttpMetadata(headers) { headers.set("content-type", "text/plain"); } };
  } },
};
for (const method of ["GET", "HEAD"]) {
  for (const name of ["_live_queries.json", "_live_queries.json.gz", "_LIVE_QUERIES.JSON", "_live_queries.json.", "_live_queries.json%20", "%5flive_queries.json", "%255flive_queries.json", "sub%2f_live_queries.json", "sub%5c_live_queries.json", "_live_queries.json::$DATA", "_live_queries.json%3A%3A%24DATA", "_live_queries.json.gz::$DATA", "_live_queries.json.gz%3A%3A%24DATA", "%ff", "%broken", "%2e%2e%2f%2e%2e%2f_private/demo/casino/sales/builds/b1/_live_queries.json"]) {
    const before = gets.length;
    const response = await worker.fetch(new Request("https://reports.example.com" + scope + name, { method, headers: { cookie: "trellum_grant=" + grant } }), env);
    assert.equal(response.status, 404, name);
    assert.equal(gets.length, before, name + " must not reach REPORTS.get");
  }
  for (const name of ["index.html", "data.json", "rawdata.csv", "_meta.json"]) {
    const response = await worker.fetch(new Request("https://reports.example.com" + scope + name, { method, headers: { cookie: "trellum_grant=" + grant } }), env);
    assert.equal(response.status, 200);
    assert.equal(gets.at(-1), "demo/casino/sales/builds/b1/" + name);
    if (method === "HEAD") assert.equal(await response.text(), "");
  }
}
const response = await worker.fetch(new Request("https://reports.example.com/content/_private/demo/casino/sales/builds/b1/_live_queries.json", { headers: { cookie: "trellum_grant=" + grant } }), env);
assert.equal(response.status, 404);
console.log("edge private-artifact checks passed");
