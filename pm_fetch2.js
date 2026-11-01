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
        headers: {
          Accept: "text/html,application/json",
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        },
      }, (r) => {
        let d = "";
        r.on("data", (c) => (d += c));
        r.on("end", () => resolve({ status: r.statusCode, data: d, headers: r.headers }));
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
  // 方式1: 直接抓取用户主页 HTML
  console.log("=== 抓取 polymarket.com/@homerunhazard ===");
  const page = await fetchViaProxy("https://polymarket.com/@homerunhazard");
  console.log("Status:", page.status, "Length:", page.data.length);

  // 搜索 0x 开头的钱包地址
  const addrMatches = page.data.match(/0x[a-fA-F0-9]{40}/g);
  if (addrMatches) {
    const unique = [...new Set(addrMatches)];
    console.log("\n找到的钱包地址候选:", unique);
  }

  // 搜索 proxyWallet / proxy_wallet
  const proxyMatch = page.data.match(/proxy[_]?[wW]allet["\s:]+["']?(0x[a-fA-F0-9]{40})/);
  if (proxyMatch) console.log("proxyWallet:", proxyMatch[1]);

  // 搜索 __NEXT_DATA__ 或 window.__INITIAL_STATE__
  const nextDataMatch = page.data.match(/__NEXT_DATA__[^{]*({.*?})<\/script>/s);
  if (nextDataMatch) {
    console.log("\n__NEXT_DATA__ (前2000字符):", nextDataMatch[1].substring(0, 2000));
  }

  // 搜索 profile 相关 JSON
  const profileMatch = page.data.match(/"profile"\s*:\s*({[^}]+})/);
  if (profileMatch) console.log("\nProfile JSON:", profileMatch[1]);

  // 搜索 username / userAddress
  const userAddrMatch = page.data.match(/user[Aa]ddress["\s:]+["']?(0x[a-fA-F0-9]{40})/);
  if (userAddrMatch) console.log("userAddress:", userAddrMatch[1]);

  // 输出页面中包含 "homerun" 的上下文片段
  const homerunContexts = [];
  let idx = 0;
  while (true) {
    idx = page.data.indexOf("homerun", idx);
    if (idx === -1) break;
    homerunContexts.push(page.data.substring(Math.max(0, idx - 100), idx + 200));
    idx += 7;
    if (homerunContexts.length > 5) break;
  }
  if (homerunContexts.length > 0) {
    console.log("\n=== 'homerun' 上下文片段 ===");
    homerunContexts.forEach((c, i) => console.log(`[${i}]`, c.replace(/\n/g, " ").substring(0, 300)));
  }

  // 方式2: 试试 Gamma API profiles 端点
  console.log("\n=== 试试 Gamma /profiles?username=homerunhazard ===");
  try {
    const p2 = await fetchViaProxy("https://gamma-api.polymarket.com/profiles?username=homerunhazard");
    console.log("Status:", p2.status, "Data:", p2.data.substring(0, 2000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 方式3: 试试 /users 端点
  console.log("\n=== 试试 Gamma /users?username=homerunhazard ===");
  try {
    const p3 = await fetchViaProxy("https://gamma-api.polymarket.com/users?username=homerunhazard");
    console.log("Status:", p3.status, "Data:", p3.data.substring(0, 2000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 方式4: 试试 data-api user by username
  console.log("\n=== 试试 Data API /v2/user?username=homerunhazard ===");
  try {
    const p4 = await fetchViaProxy("https://data-api.polymarket.com/v2/user?username=homerunhazard");
    console.log("Status:", p4.status, "Data:", p4.data.substring(0, 2000));
  } catch (e) { console.log("FAILED:", e.message); }
}

main().catch((e) => console.error("Error:", e.message));
