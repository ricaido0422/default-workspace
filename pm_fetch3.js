const https = require("https");
const http = require("http");

const PROXY_HOST = "127.0.0.1";
const PROXY_PORT = 7897;

function fetchViaProxy(urlStr, extraHeaders = {}) {
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
          ...extraHeaders,
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
  const username = "homerunhazard";

  // 1. Try Gamma API - search for events/markets by this username in creator fields
  console.log("=== 1. Gamma /events?creator=username ===");
  try {
    const r = await fetchViaProxy(`https://gamma-api.polymarket.com/events?creator=${username}&limit=5`);
    console.log("Status:", r.status);
    console.log(r.data.substring(0, 2000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 2. Try Gamma /markets?creator=username
  console.log("\n=== 2. Gamma /markets?creator=username ===");
  try {
    const r = await fetchViaProxy(`https://gamma-api.polymarket.com/markets?creator=${username}&limit=5`);
    console.log("Status:", r.status);
    console.log(r.data.substring(0, 2000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 3. Try Data API v2 - search by username in activity
  console.log("\n=== 3. Data API /v2/activity?username=... ===");
  try {
    const r = await fetchViaProxy(`https://data-api.polymarket.com/v2/activity?username=${username}&limit=5`);
    console.log("Status:", r.status);
    console.log(r.data.substring(0, 2000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 4. Try Data API v2 - search by user_name in leaderboard
  console.log("\n=== 4. Data API /v2/leaderboard?user_name=... ===");
  try {
    const r = await fetchViaProxy(`https://data-api.polymarket.com/v2/leaderboard?time_period=ALL&limit=100`);
    console.log("Status:", r.status);
    const data = JSON.parse(r.data);
    const match = (data.data || []).find(u => (u.user_name || u.username || "").toLowerCase().includes(username.toLowerCase()));
    if (match) {
      console.log("FOUND in leaderboard:", JSON.stringify(match, null, 2));
    } else {
      console.log("Not found in top 100 leaderboard");
      // Show first few entries
      console.log("Sample entries:", JSON.stringify((data.data || []).slice(0, 3), null, 2));
    }
  } catch (e) { console.log("FAILED:", e.message); }

  // 5. Try the Polymarket internal API endpoint
  console.log("\n=== 5. Polymarket internal /api/user ===");
  try {
    const r = await fetchViaProxy(`https://polymarket.com/api/user?username=${username}`);
    console.log("Status:", r.status);
    console.log(r.data.substring(0, 2000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 6. Try /api/profile
  console.log("\n=== 6. Polymarket internal /api/profile ===");
  try {
    const r = await fetchViaProxy(`https://polymarket.com/api/profile?username=${username}`);
    console.log("Status:", r.status);
    console.log(r.data.substring(0, 2000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 7. Try /api/users
  console.log("\n=== 7. Polymarket internal /api/users ===");
  try {
    const r = await fetchViaProxy(`https://polymarket.com/api/users?username=${username}`);
    console.log("Status:", r.status);
    console.log(r.data.substring(0, 2000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 8. Try the Gamma /profile endpoint with username param
  console.log("\n=== 8. Gamma /profile?username=... ===");
  try {
    const r = await fetchViaProxy(`https://gamma-api.polymarket.com/profile?username=${username}`);
    console.log("Status:", r.status);
    console.log(r.data.substring(0, 2000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 9. Try Gamma /user endpoint
  console.log("\n=== 9. Gamma /user?username=... ===");
  try {
    const r = await fetchViaProxy(`https://gamma-api.polymarket.com/user?username=${username}`);
    console.log("Status:", r.status);
    console.log(r.data.substring(0, 2000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 10. Try to get the user page and extract __NEXT_DATA__ from the full response
  console.log("\n=== 10. Full page fetch with different approach ===");
  try {
    const r = await fetchViaProxy(`https://polymarket.com/@${username}`);
    console.log("Status:", r.status, "Length:", r.data.length);
    // Look for any JSON data in the page
    const jsonMatches = r.data.match(/\{[^{}]*"username"[^{}]*\}/g);
    if (jsonMatches) {
      console.log("Found JSON with username:");
      jsonMatches.forEach(m => console.log(m.substring(0, 500)));
    }
    // Look for proxyWallet near username
    const idx = r.data.indexOf(username);
    if (idx > 0) {
      console.log("\nUsername found at index", idx);
      const context = r.data.substring(Math.max(0, idx - 500), idx + 500);
      console.log("Context:", context.replace(/\n/g, " ").substring(0, 1000));
    }
  } catch (e) { console.log("FAILED:", e.message); }

  // 11. Try the Data API v2 /v2/user-stats with a different approach
  // First, let's try to find the user by looking at recent trades and filtering
  console.log("\n=== 11. Try Data API /v2/trades with username filter ===");
  try {
    const r = await fetchViaProxy(`https://data-api.polymarket.com/v2/trades?username=${username}&limit=5`);
    console.log("Status:", r.status);
    console.log(r.data.substring(0, 2000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 12. Try the Gamma /public-search with different params
  console.log("\n=== 12. Gamma /public-search with type=profile ===");
  try {
    const r = await fetchViaProxy(`https://gamma-api.polymarket.com/public-search?q=${username}&limit_per_type=5&types=profile`);
    console.log("Status:", r.status);
    console.log(r.data.substring(0, 2000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 13. Try the Data API v2 /v2/user endpoint
  console.log("\n=== 13. Data API /v2/user (no params) ===");
  try {
    const r = await fetchViaProxy(`https://data-api.polymarket.com/v2/user`);
    console.log("Status:", r.status);
    console.log(r.data.substring(0, 2000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 14. Try the Gamma /users endpoint
  console.log("\n=== 14. Gamma /users ===");
  try {
    const r = await fetchViaProxy(`https://gamma-api.polymarket.com/users`);
    console.log("Status:", r.status);
    console.log(r.data.substring(0, 2000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 15. Try to get the user's positions from the Data API by looking at all open positions
  // and filtering by username (if the API supports it)
  console.log("\n=== 15. Data API /v2/positions with username ===");
  try {
    const r = await fetchViaProxy(`https://data-api.polymarket.com/v2/positions?username=${username}&limit=5`);
    console.log("Status:", r.status);
    console.log(r.data.substring(0, 2000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 16. Try the Gamma /profile endpoint with the username in the path
  console.log("\n=== 16. Gamma /profiles/username ===");
  try {
    const r = await fetchViaProxy(`https://gamma-api.polymarket.com/profiles/${username}`);
    console.log("Status:", r.status);
    console.log(r.data.substring(0, 2000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 17. Try the Data API v2 /v2/user with the username in the path
  console.log("\n=== 17. Data API /v2/user/username ===");
  try {
    const r = await fetchViaProxy(`https://data-api.polymarket.com/v2/user/${username}`);
    console.log("Status:", r.status);
    console.log(r.data.substring(0, 2000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 18. Try the Gamma /user endpoint with the username in the path
  console.log("\n=== 18. Gamma /user/username ===");
  try {
    const r = await fetchViaProxy(`https://gamma-api.polymarket.com/user/${username}`);
    console.log("Status:", r.status);
    console.log(r.data.substring(0, 2000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 19. Try the Polymarket internal /api/user/username
  console.log("\n=== 19. Polymarket /api/user/username ===");
  try {
    const r = await fetchViaProxy(`https://polymarket.com/api/user/${username}`);
    console.log("Status:", r.status);
    console.log(r.data.substring(0, 2000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 20. Try the Polymarket internal /api/profile/username
  console.log("\n=== 20. Polymarket /api/profile/username ===");
  try {
    const r = await fetchViaProxy(`https://polymarket.com/api/profile/${username}`);
    console.log("Status:", r.status);
    console.log(r.data.substring(0, 2000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 21. Try the Gamma /search endpoint
  console.log("\n=== 21. Gamma /search?q=... ===");
  try {
    const r = await fetchViaProxy(`https://gamma-api.polymarket.com/search?q=${username}`);
    console.log("Status:", r.status);
    console.log(r.data.substring(0, 2000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 22. Try the Data API v2 /v2/search
  console.log("\n=== 22. Data API /v2/search?q=... ===");
  try {
    const r = await fetchViaProxy(`https://data-api.polymarket.com/v2/search?q=${username}`);
    console.log("Status:", r.status);
    console.log(r.data.substring(0, 2000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 23. Try the Gamma /profiles endpoint
  console.log("\n=== 23. Gamma /profiles ===");
  try {
    const r = await fetchViaProxy(`https://gamma-api.polymarket.com/profiles`);
    console.log("Status:", r.status);
    console.log(r.data.substring(0, 2000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 24. Try the Data API v2 /v2/profiles
  console.log("\n=== 24. Data API /v2/profiles ===");
  try {
    const r = await fetchViaProxy(`https://data-api.polymarket.com/v2/profiles`);
    console.log("Status:", r.status);
    console.log(r.data.substring(0, 2000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 25. Try the Gamma /users endpoint with username param
  console.log("\n=== 25. Gamma /users?username=... ===");
  try {
    const r = await fetchViaProxy(`https://gamma-api.polymarket.com/users?username=${username}`);
    console.log("Status:", r.status);
    console.log(r.data.substring(0, 2000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 26. Try the Data API v2 /v2/users
  console.log("\n=== 26. Data API /v2/users ===");
  try {
    const r = await fetchViaProxy(`https://data-api.polymarket.com/v2/users`);
    console.log("Status:", r.status);
    console.log(r.data.substring(0, 2000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 27. Try the Gamma /user endpoint with the username in the path
  console.log("\n=== 27. Gamma /users/username ===");
  try {
    const r = await fetchViaProxy(`https://gamma-api.polymarket.com/users/${username}`);
    console.log("Status:", r.status);
    console.log(r.data.substring(0, 2000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 28. Try the Data API v2 /v2/users/username
  console.log("\n=== 28. Data API /v2/users/username ===");
  try {
    const r = await fetchViaProxy(`https://data-api.polymarket.com/v2/users/${username}`);
    console.log("Status:", r.status);
    console.log(r.data.substring(0, 2000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 29. Try the Polymarket internal /api/users/username
  console.log("\n=== 29. Polymarket /api/users/username ===");
  try {
    const r = await fetchViaProxy(`https://polymarket.com/api/users/${username}`);
    console.log("Status:", r.status);
    console.log(r.data.substring(0, 2000));
  } catch (e) { console.log("FAILED:", e.message); }

  // 30. Try the Polymarket internal /api/profiles/username
  console.log("\n=== 30. Polymarket /api/profiles/username ===");
  try {
    const r = await fetchViaProxy(`https://polymarket.com/api/profiles/${username}`);
    console.log("Status:", r.status);
    console.log(r.data.substring(0, 2000));
  } catch (e) { console.log("FAILED:", e.message); }
}

main().catch((e) => console.error("Error:", e.message));
