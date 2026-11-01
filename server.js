const http = require("http");
const fs = require("fs");
const path = require("path");

const PORT = 3000;
const PROXY_HOST = "127.0.0.1";
const PROXY_PORT = 7897;

// 钱包配置
let config = {
  tradingWallet: "0x5268527977f700f9bf9b6d5cd843859e4e70135d",
  trackedWallets: [
    { wallet: "0x5268527977f700f9bf9b6d5cd843859e4e70135d", name: "HomeRunHazard" },
  ],
  activeTracked: "0x5268527977f700f9bf9b6d5cd843859e4e70135d"
};

// 模拟交易数据
let simulatedTrades = [];
let copyHistory = [];

// 保存配置
function saveConfig() {
  fs.writeFileSync("wallets.json", JSON.stringify(config, null, 2));
}

// 加载配置
function loadConfig() {
  try {
    if (fs.existsSync("wallets.json")) {
      config = JSON.parse(fs.readFileSync("wallets.json", "utf8"));
    }
  } catch (e) {
    console.error("加载配置失败:", e.message);
  }
}

loadConfig();

// Proxy fetch helper
function fetchViaProxy(urlStr) {
  return new Promise((resolve, reject) => {
    const url = new URL(urlStr);
    const proxyReq = http.request({
      host: PROXY_HOST,
      port: PROXY_PORT,
      method: "CONNECT",
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

const https = require("https");

// 获取钱包信息
async function getWalletInfo(wallet) {
  try {
    const [statsRes, positionsRes, valueRes] = await Promise.all([
      fetchViaProxy(`https://data-api.polymarket.com/v2/user-stats?user=${wallet}`),
      fetchViaProxy(
        `https://data-api.polymarket.com/v2/positions?user=${wallet}&status=OPEN&limit=20&sort_by=CURRENT_VALUE&sort_direction=DESC`
      ),
      fetchViaProxy(`https://data-api.polymarket.com/v2/value?user=${wallet}`),
    ]);

    const stats = JSON.parse(statsRes.data);
    const positions = JSON.parse(positionsRes.data);
    const value = JSON.parse(valueRes.data);

    return { stats, positions: positions.data || [], value };
  } catch (e) {
    throw new Error(e.message);
  }
}

// 获取交易历史
async function getTradeHistory(wallet, limit = 20) {
  try {
    const r = await fetchViaProxy(
      `https://data-api.polymarket.com/v2/activity?user=${wallet}&type=TRADE&limit=${limit}`
    );
    return JSON.parse(r.data);
  } catch (e) {
    throw new Error(e.message);
  }
}

// 获取排行榜
async function getLeaderboard(limit = 50) {
  try {
    const r = await fetchViaProxy(`https://data-api.polymarket.com/v2/leaderboard?time_period=ALL&limit=${limit}`);
    return JSON.parse(r.data);
  } catch (e) {
    throw new Error(e.message);
  }
}

// 模拟跟单
function simulateCopyTrade(trade, copyRatio) {
  const copyAmount = Math.floor(trade.amount * copyRatio);
  const timestamp = new Date().toISOString();
  
  const copyTrade = {
    id: `copy_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`,
    timestamp,
    tradingWallet: config.tradingWallet,
    trackedWallet: config.activeTracked,
    originalMarket: trade.market,
    originalSide: trade.side,
    originalAmount: trade.amount,
    originalPrice: trade.price,
    copyAmount,
    copyRatio,
    estimatedPnl: Math.floor(trade.pnl * copyRatio),
    status: "EXECUTED",
    txHash: `0x${Math.random().toString(16).slice(2, 10)}...${Math.random().toString(16).slice(2, 10)}`
  };
  
  copyHistory.unshift(copyTrade);
  fs.writeFileSync("copy-history.json", JSON.stringify(copyHistory, null, 2));
  
  return copyTrade;
}

// 生成模拟交易流
function generateMockTradeStream() {
  const markets = [
    "South Carolina vs Tennessee",
    "LSU vs Auburn", 
    "Pelicans vs Jazz",
    "Trail Blazers vs Kings",
    "Ravens vs Bengals",
    "Rays vs Brewers",
    "Celtics vs Lakers",
    "Warriors vs Suns",
    "Bucks vs Heat",
    "Nets vs 76ers"
  ];
  
  const sides = ["YES", "NO"];
  const amounts = [8000, 10000, 12000, 14000, 15000, 18000, 20000];
  
  const trades = [];
  const now = Date.now();
  
  for (let i = 0; i < 20; i++) {
    const market = markets[Math.floor(Math.random() * markets.length)];
    const side = sides[Math.floor(Math.random() * sides.length)];
    const amount = amounts[Math.floor(Math.random() * amounts.length)];
    const price = 0.3 + Math.random() * 0.4;
    const pnl = (Math.random() - 0.3) * amount * 0.5;
    
    trades.push({
      id: `trade_${now - i * 30000}`,
      timestamp: new Date(now - i * 30000).toISOString(),
      market,
      side,
      amount,
      price,
      pnl: Math.round(pnl),
      type: Math.random() > 0.3 ? "BUY" : "SELL"
    });
  }
  
  return trades;
}

// 模拟持仓
const mockPositions = [
  { market: "South Carolina vs Tennessee", side: "YES", size: 12000, entryPrice: 0.52, currentPrice: 0.55, unrealizedPnl: 43, pnlPercent: 0.31 },
  { market: "LSU vs Auburn", side: "YES", size: 10000, entryPrice: 0.48, currentPrice: 0.50, unrealizedPnl: 22, pnlPercent: 0.20 },
  { market: "Pelicans vs Jazz", side: "YES", size: 14000, entryPrice: 0.55, currentPrice: 0.42, unrealizedPnl: -1569, pnlPercent: -12.90 },
  { market: "Trail Blazers vs Kings", side: "YES", size: 11000, entryPrice: 0.45, currentPrice: 0.48, unrealizedPnl: 346, pnlPercent: 3.41 },
  { market: "Ravens vs Bengals", side: "YES", size: 9000, entryPrice: 0.58, currentPrice: 0.62, unrealizedPnl: 323, pnlPercent: 3.62 },
  { market: "Rays vs Brewers", side: "YES", size: 8500, entryPrice: 0.51, currentPrice: 0.55, unrealizedPnl: 353, pnlPercent: 4.17 },
];

// 模拟排行榜
const mockLeaderboard = [
  { rank: 1, username: "CryptoKing", wallet: "0x1234...abcd", pnl: 5230000, volume: 1250000000, trades: 1250000 },
  { rank: 2, username: "DeFiMaster", wallet: "0x5678...efgh", pnl: 4890000, volume: 1100000000, trades: 1100000 },
  { rank: 3, username: "YieldFarmer", wallet: "0x9abc...ijkl", pnl: 4560000, volume: 980000000, trades: 980000 },
  { rank: 4, username: "LiquidityPro", wallet: "0xdef0...mnop", pnl: 4120000, volume: 870000000, trades: 870000 },
  { rank: 5, username: "SwapKing", wallet: "0x1234...qrst", pnl: 3890000, volume: 760000000, trades: 760000 },
  { rank: 55, username: "HomeRunHazard", wallet: "0x5268...0135d", pnl: 2820922, volume: 445660000, trades: 750872 },
];

// 模拟用户统计
const mockStats = {
  realized_pnl: 2820922,
  total_pnl: 2820922,
  volume: 445660000,
  trades: 750872,
  max_win: 322618,
  max_loss: -45231,
  win_rate: 0.685,
  avg_win: 423,
  avg_loss: -289,
  profit_factor: 1.46
};

// 模拟组合价值
const mockValue = [
  { value: 396128.78, timestamp: new Date().toISOString() }
];

// MIME types
const MIME_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
};

// API handlers
async function handleApi(req, res) {
  const url = new URL(req.url, `http://localhost:${PORT}`);
  const pathname = url.pathname;

  try {
    // 健康检查
    if (pathname === "/api/health") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "ok", timestamp: new Date().toISOString() }));
      return;
    }

    // 获取配置
    if (pathname === "/api/config") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ success: true, config }));
      return;
    }

    // 更新配置
    if (pathname === "/api/config" && req.method === "POST") {
      let body = "";
      req.on("data", (chunk) => body += chunk);
      req.on("end", () => {
        const newConfig = JSON.parse(body);
        config = newConfig;
        saveConfig();
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ success: true, config }));
      });
      return;
    }

    // 获取操作钱包信息
    if (pathname === "/api/trading-wallet") {
      const info = await getWalletInfo(config.tradingWallet).catch(() => ({
        stats: mockStats,
        positions: mockPositions,
        value: mockValue
      }));
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ success: true, wallet: config.tradingWallet, ...info }));
      return;
    }

    // 更新操作钱包
    if (pathname === "/api/trading-wallet" && req.method === "POST") {
      let body = "";
      req.on("data", (chunk) => body += chunk);
      req.on("end", () => {
        const { wallet } = JSON.parse(body);
        config.tradingWallet = wallet;
        saveConfig();
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ success: true, wallet }));
      });
      return;
    }

    // 获取跟踪钱包列表
    if (pathname === "/api/tracked-wallets") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ success: true, trackedWallets: config.trackedWallets, activeTracked: config.activeTracked }));
      return;
    }

    // 添加跟踪钱包
    if (pathname === "/api/tracked-wallets" && req.method === "POST") {
      let body = "";
      req.on("data", (chunk) => body += chunk);
      req.on("end", () => {
        const { wallet, name } = JSON.parse(body);
        const exists = config.trackedWallets.find(w => w.wallet === wallet);
        if (!exists) {
          config.trackedWallets.push({ wallet, name: name || wallet.slice(0, 8) + "..." + wallet.slice(-4) });
          saveConfig();
        }
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ success: true, trackedWallets: config.trackedWallets }));
      });
      return;
    }

    // 删除跟踪钱包
    if (pathname === "/api/tracked-wallets" && req.method === "DELETE") {
      const urlObj = new URL(req.url, `http://localhost:${PORT}`);
      const wallet = urlObj.searchParams.get("wallet");
      config.trackedWallets = config.trackedWallets.filter(w => w.wallet !== wallet);
      if (config.activeTracked === wallet) {
        config.activeTracked = config.trackedWallets[0]?.wallet || "";
      }
      saveConfig();
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ success: true, trackedWallets: config.trackedWallets }));
      return;
    }

    // 切换活动跟踪钱包
    if (pathname === "/api/active-tracked" && req.method === "POST") {
      let body = "";
      req.on("data", (chunk) => body += chunk);
      req.on("end", () => {
        const { wallet } = JSON.parse(body);
        config.activeTracked = wallet;
        saveConfig();
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ success: true, activeTracked: wallet }));
      });
      return;
    }

    // 获取活动跟踪钱包信息
    if (pathname === "/api/active-tracked") {
      if (!config.activeTracked) {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ success: false, error: "未选择跟踪钱包" }));
        return;
      }
      const info = await getWalletInfo(config.activeTracked).catch(() => ({
        stats: mockStats,
        positions: mockPositions,
        value: mockValue
      }));
      const tracked = config.trackedWallets.find(w => w.wallet === config.activeTracked);
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ 
        success: true, 
        wallet: config.activeTracked,
        name: tracked?.name,
        ...info 
      }));
      return;
    }

    // 获取活动跟踪钱包的交易历史
    if (pathname === "/api/active-tracked/trades") {
      if (!config.activeTracked) {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ success: false, error: "未选择跟踪钱包" }));
        return;
      }
      const trades = await getTradeHistory(config.activeTracked, 50).catch(() => ({
        data: generateMockTradeStream()
      }));
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ success: true, trades: trades.data || [] }));
      return;
    }

    // 获取排行榜
    if (pathname === "/api/leaderboard") {
      const limit = parseInt(url.searchParams.get("limit") || "50");
      const leaderboard = await getLeaderboard(limit).catch(() => ({
        data: mockLeaderboard
      }));
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ success: true, leaderboard: leaderboard.data || [] }));
      return;
    }

    // 搜索用户
    if (pathname === "/api/search") {
      const query = url.searchParams.get("q");
      if (!query) {
        res.writeHead(400, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ success: false, error: "缺少查询参数" }));
        return;
      }
      const results = await searchUser(query).catch(() => ({
        data: []
      }));
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ success: true, results }));
      return;
    }

    // 模拟跟单
    if (pathname === "/api/simulate-copy" && req.method === "POST") {
      let body = "";
      req.on("data", (chunk) => body += chunk);
      req.on("end", () => {
        const { trade, copyRatio } = JSON.parse(body);
        const result = simulateCopyTrade(trade, copyRatio || 0.1);
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ success: true, result }));
      });
      return;
    }

    // 获取跟单历史
    if (pathname === "/api/copy-history") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ success: true, history: copyHistory }));
      return;
    }

    // 生成模拟交易
    if (pathname === "/api/generate-trades" && req.method === "POST") {
      const trades = generateMockTradeStream();
      simulatedTrades = [...simulatedTrades, ...trades];
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ success: true, trades }));
      return;
    }

    // 获取模拟交易流
    if (pathname === "/api/simulated-trades") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ success: true, trades: simulatedTrades }));
      return;
    }

    // 404
    res.writeHead(404, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ success: false, error: "Not found" }));
  } catch (e) {
    console.error("API error:", e.message);
    res.writeHead(500, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ success: false, error: e.message }));
  }
}

// 搜索用户
async function searchUser(query) {
  try {
    const r = await fetchViaProxy(`https://gamma-api.polymarket.com/public-search?query=${encodeURIComponent(query)}`);
    return JSON.parse(r.data);
  } catch (e) {
    throw new Error(e.message);
  }
}

// 主请求处理器
function handleRequest(req, res) {
  const url = new URL(req.url, `http://localhost:${PORT}`);

  // API routes
  if (url.pathname.startsWith("/api")) {
    handleApi(req, res);
    return;
  }

  // Serve static files
  let filePath = path.join(__dirname, "public", url.pathname === "/" ? "index.html" : url.pathname);

  // 模拟操盘页面
  if (url.pathname === "/simulate") {
    filePath = path.join(__dirname, "public", "simulate.html");
  }

  // Security: prevent directory traversal
  if (!filePath.startsWith(path.join(__dirname, "public"))) {
    res.writeHead(403);
    res.end("403 - Forbidden");
    return;
  }

  const ext = path.extname(filePath).toLowerCase();
  const contentType = MIME_TYPES[ext] || "application/octet-stream";

  fs.readFile(filePath, (err, data) => {
    if (err) {
      res.writeHead(500);
      res.end("500 - Internal Server Error");
      return;
    }
    res.writeHead(200, { "Content-Type": contentType });
    res.end(data);
  });
}

// Create server
const server = http.createServer(handleRequest);

server.listen(PORT, () => {
  console.log(`\n🚀 Polymarket 跟单系统已启动`);
  console.log(`📊 操作钱包: ${config.tradingWallet.slice(0, 8)}...${config.tradingWallet.slice(-4)}`);
  console.log(`📊 跟踪钱包: ${config.trackedWallets.length} 个`);
  console.log(`🌐 仪表盘: http://localhost:${PORT}`);
  console.log(`🎮 模拟操盘: http://localhost:${PORT}/simulate`);
  console.log(`\n🔧 模拟跟单功能已启用`);
  console.log(`💡 访问模拟操盘页面开始模拟交易\n`);
});
