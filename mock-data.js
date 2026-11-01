// 模拟跟单模块
const fs = require("fs");

// 模拟交易数据
const mockTrades = [
  { market: "South Carolina vs Tennessee", side: "YES", amount: 12000, price: 0.52, pnl: 43 },
  { market: "LSU vs Auburn", side: "YES", amount: 10000, price: 0.48, pnl: 22 },
  { market: "Pelicans vs Jazz", side: "YES", amount: 14000, price: 0.55, pnl: -1569 },
  { market: "Trail Blazers vs Kings", side: "YES", amount: 11000, price: 0.45, pnl: 346 },
  { market: "Ravens vs Bengals", side: "YES", amount: 9000, price: 0.58, pnl: 323 },
  { market: "Rays vs Brewers", side: "YES", amount: 8500, price: 0.51, pnl: 353 },
];

// 模拟跟单执行
function simulateCopyTrade(tradingWallet, trackedWallet, trade, copyRatio) {
  const copyAmount = Math.floor(trade.amount * copyRatio);
  const timestamp = new Date().toISOString();
  
  return {
    id: `sim_${Date.now()}`,
    type: "COPY_TRADE",
    timestamp,
    tradingWallet,
    trackedWallet,
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
}

// 生成模拟交易流
function generateTradeStream() {
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

module.exports = {
  mockTrades,
  mockPositions,
  mockLeaderboard,
  mockStats,
  mockValue,
  simulateCopyTrade,
  generateTradeStream
};
