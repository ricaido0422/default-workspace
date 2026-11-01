const https = require("https");
const http = require("http");

const PROXY_HOST = "127.0.0.1";
const PROXY_PORT = 7897;
const WALLET = "0x5268527977f700f9bf9b6d5cd843859e4e70135d";

function fetchViaProxy(urlStr) {
  return new Promise((resolve, reject) => {
    const url = new URL(urlStr);
    const proxyReq = http.request({
      host: PROXY_HOST, port: PROXY_PORT, method: "CONNECT",
      path: `${url.hostname}:443`,
    });
    proxyReq.on("connect", (res, socket) => {
      const req = https.request({
        hostname: url.hostname,
        path: url.pathname + url.search,
        method: "GET",
        socket: socket,
        rejectUnauthorized: false,
        headers: {
          Accept: "application/json",
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        },
      }, (r) => {
        let d = "";
        r.on("data", (c) => (d += c));
        r.on("end", () => resolve({ status: r.statusCode, data: d }));
      });
      req.on("error", reject);
      req.setTimeout(15000, () => req.destroy(new Error("timeout")));
      req.end();
    });
    proxyReq.on("error", reject);
    proxyReq.setTimeout(10000, () => proxyReq.destroy(new Error("proxy timeout")));
    proxyReq.end();
  });
}

function prettyPrintJSON(str, maxLen = 3000) {
  try {
    const obj = JSON.parse(str);
    return JSON.stringify(obj, null, 2).substring(0, maxLen);
  } catch (e) {
    return str.substring(0, maxLen);
  }
}

async function main() {
  console.log(`=== 查询用户: ${WALLET} ===\n`);

  // 1. user_stats
  console.log("=== 1. User Stats ===");
  try {
    const r = await fetchViaProxy(`https://data-api.polymarket.com/v2/user-stats?user=${WALLET}`);
    console.log("Status:", r.status);
    console.log(prettyPrintJSON(r.data, 5000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 2. positions (open)
  console.log("\n=== 2. Positions (OPEN) ===");
  try {
    const r = await fetchViaProxy(`https://data-api.polymarket.com/v2/positions?user=${WALLET}&status=OPEN&limit=20&sort_by=CURRENT_VALUE&sort_direction=DESC`);
    console.log("Status:", r.status);
    console.log(prettyPrintJSON(r.data, 10000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 3. activity (trades)
  console.log("\n=== 3. Activity (TRADE) ===");
  try {
    const r = await fetchViaProxy(`https://data-api.polymarket.com/v2/activity?user=${WALLET}&type=TRADE&limit=20`);
    console.log("Status:", r.status);
    console.log(prettyPrintJSON(r.data, 10000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 4. portfolio value
  console.log("\n=== 4. Portfolio Value ===");
  try {
    const r = await fetchViaProxy(`https://data-api.polymarket.com/v2/value?user=${WALLET}`);
    console.log("Status:", r.status);
    console.log(prettyPrintJSON(r.data, 2000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 5. approvals
  console.log("\n=== 5. Approvals ===");
  try {
    const r = await fetchViaProxy(`https://data-api.polymarket.com/v2/approvals?user=${WALLET}`);
    console.log("Status:", r.status);
    console.log(prettyPrintJSON(r.data, 3000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 6. holders (markets where this user is a top holder)
  console.log("\n=== 6. Recent Trades (for holder analysis) ===");
  try {
    const r = await fetchViaProxy(`https://data-api.polymarket.com/v2/trades?user=${WALLET}&limit=10`);
    console.log("Status:", r.status);
    console.log(prettyPrintJSON(r.data, 10000));
  } catch (e) { console.log("FAILED:", e.message); }
}

main().catch((e) => console.error("Error:", e.message));
