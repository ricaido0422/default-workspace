const https = require("https");
const http = require("http");

const PROXY_HOST = "127.0.0.1";
const PROXY_PORT = 7897;

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
        headers: { Accept: "application/json", "User-Agent": "pm-fetch/1.0" },
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

async function main() {
  // 1. 搜索用户 homerunhazard
  console.log("=== 搜索 homerunhazard ===");
  const search = await fetchViaProxy(
    "https://gamma-api.polymarket.com/public-search?q=homerunhazard&limit_per_type=10"
  );
  console.log("Search status:", search.status);
  const searchData = JSON.parse(search.data);

  // 提取 profile 中的钱包地址
  let wallet = null;
  let profileInfo = null;
  const profiles = searchData.profiles || [];
  if (profiles.length > 0) {
    profileInfo = profiles[0];
    wallet = profileInfo.proxyWallet || profileInfo.address || profileInfo.id || profileInfo.user;
    console.log("Profile:", JSON.stringify(profileInfo, null, 2).substring(0, 2000));
  } else {
    console.log("No profiles found. Full search result keys:", Object.keys(searchData));
    console.log("Full result:", JSON.stringify(searchData).substring(0, 3000));
  }

  if (!wallet) {
    console.log("\n无法从搜索结果中提取钱包地址，退出。");
    return;
  }

  console.log(`\n钱包地址: ${wallet}`);

  // 2. user_stats
  console.log("\n=== User Stats ===");
  const stats = await fetchViaProxy(
    `https://data-api.polymarket.com/v2/user-stats?user=${wallet}`
  );
  console.log("Status:", stats.status);
  console.log(stats.data.substring(0, 3000));

  // 3. positions (open)
  console.log("\n=== Positions (OPEN) ===");
  const positions = await fetchViaProxy(
    `https://data-api.polymarket.com/v2/positions?user=${wallet}&status=OPEN&limit=50&sort_by=CURRENT_VALUE&sort_direction=DESC`
  );
  console.log("Status:", positions.status);
  console.log(positions.data.substring(0, 8000));

  // 4. activity (trades)
  console.log("\n=== Activity (TRADE) ===");
  const activity = await fetchViaProxy(
    `https://data-api.polymarket.com/v2/activity?user=${wallet}&type=TRADE&limit=30`
  );
  console.log("Status:", activity.status);
  console.log(activity.data.substring(0, 8000));

  // 5. portfolio value
  console.log("\n=== Portfolio Value ===");
  const value = await fetchViaProxy(
    `https://data-api.polymarket.com/v2/value?user=${wallet}`
  );
  console.log("Status:", value.status);
  console.log(value.data.substring(0, 1000));
}

main().catch((e) => console.error("Error:", e.message));
