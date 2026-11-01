// 模拟操盘逻辑

const API_BASE = "/api";
let simInterval = null;
let balance = 100000;
let copyRatio = 0.1;
let tradeCount = 0;
let winCount = 0;
let lossCount = 0;
let totalPnl = 0;
let pnlHistory = [0];
let charts = {};

// DOM 元素
const tradeStream = document.getElementById("tradeStream");
const copyHistoryTable = document.getElementById("copyHistoryTable");
const balanceValue = document.getElementById("balanceValue");
const totalTrades = document.getElementById("totalTrades");
const winTrades = document.getElementById("winTrades");
const lossTrades = document.getElementById("lossTrades");
const totalPnlEl = document.getElementById("totalPnl");
const toastContainer = document.getElementById("toastContainer");

// 格式化函数
function formatCurrency(num) {
  if (num >= 1000000) return "$" + (num / 1000000).toFixed(2) + "M";
  if (num >= 1000) return "$" + (num / 1000).toFixed(1) + "k";
  return "$" + num.toFixed(0);
}

function formatTime(dateStr) {
  const date = new Date(dateStr);
  return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatRelativeTime(dateStr) {
  const date = new Date(dateStr);
  const now = new Date();
  const diff = now - date;
  const seconds = Math.floor(diff / 1000);
  if (seconds < 5) return "刚刚";
  if (seconds < 60) return seconds + " 秒前";
  return Math.floor(seconds / 60) + " 分钟前";
}

function truncateAddress(addr) {
  if (!addr || addr.length < 12) return addr;
  return addr.slice(0, 6) + "..." + addr.slice(-4);
}

// 显示 Toast
function showToast(message, type = "info") {
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  const icon = type === 'success' ? 'check-circle' : type === 'error' ? 'exclamation-circle' : 'info-circle';
  toast.innerHTML = `<i class="fas fa-${icon}"></i> ${message}`;
  toastContainer.appendChild(toast);
  setTimeout(() => toast.remove(), 3000);
}

// 生成模拟交易
function generateMockTrade() {
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
  
  const market = markets[Math.floor(Math.random() * markets.length)];
  const side = sides[Math.floor(Math.random() * sides.length)];
  const amount = amounts[Math.floor(Math.random() * amounts.length)];
  const price = 0.3 + Math.random() * 0.4;
  const pnl = (Math.random() - 0.3) * amount * 0.5;
  
  return {
    id: `trade_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`,
    timestamp: new Date().toISOString(),
    market,
    side,
    amount,
    price,
    pnl: Math.round(pnl),
    type: Math.random() > 0.3 ? "BUY" : "SELL"
  };
}

// 执行跟单
async function executeCopyTrade(trade) {
  const copyAmount = Math.floor(trade.amount * copyRatio);
  const estimatedPnl = Math.floor(trade.pnl * copyRatio);
  
  // 更新余额
  balance -= copyAmount;
  balance += copyAmount + estimatedPnl;
  
  // 更新统计
  tradeCount++;
  if (estimatedPnl > 0) {
    winCount++;
  } else {
    lossCount++;
  }
  totalPnl += estimatedPnl;
  
  // 更新图表数据
  pnlHistory.push(totalPnl);
  if (pnlHistory.length > 50) pnlHistory.shift();
  
  // 更新图表
  updatePnlChart();
  
  // 创建跟单记录
  const copyRecord = {
    id: `copy_${Date.now()}`,
    timestamp: trade.timestamp,
    market: trade.market,
    side: trade.side,
    originalAmount: trade.amount,
    copyAmount,
    copyRatio,
    estimatedPnl,
    status: "EXECUTED",
    txHash: `0x${Math.random().toString(16).slice(2, 10)}...${Math.random().toString(16).slice(2, 10)}`
  };
  
  // 添加到交易流
  addTradeToStream(trade, copyRecord);
  
  // 添加到历史表格
  addToHistoryTable(copyRecord);
  
  // 更新显示
  updateDisplay();
  
  // 发送请求到后端
  try {
    await fetch(`${API_BASE}/simulate-copy`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ trade, copyRatio })
    });
  } catch (e) {
    console.error("发送跟单请求失败:", e);
  }
  
  return copyRecord;
}

// 添加交易到流
function addTradeToStream(trade, copyRecord) {
  const isWin = trade.pnl > 0;
  const isBuy = trade.type === "BUY";
  
  const item = document.createElement("div");
  item.className = `trade-item ${isBuy ? 'buy' : 'sell'}`;
  item.innerHTML = `
    <div class="trade-item-info">
      <div class="trade-item-icon ${isBuy ? 'buy' : 'sell'}">
        <i class="fas fa-${isBuy ? 'arrow-up' : 'arrow-down'}"></i>
      </div>
      <div>
        <div class="trade-item-market">${trade.market}</div>
        <div class="trade-item-detail">
          ${isBuy ? '买入' : '卖出'} ${trade.side} | 
          原金额 ${formatCurrency(trade.amount)} → 
          跟单 ${formatCurrency(copyRecord.copyAmount)} (${copyRatio * 100}%)
        </div>
      </div>
    </div>
    <div class="trade-item-stats">
      <div class="trade-item-amount">
        <div class="trade-item-amount-value ${isWin ? 'positive' : 'negative'}">
          ${isWin ? '+' : ''}${formatCurrency(trade.pnl)}
        </div>
      </div>
      <div class="trade-item-time">${formatRelativeTime(trade.timestamp)}</div>
    </div>
  `;
  
  tradeStream.insertBefore(item, tradeStream.firstChild);
  
  // 限制显示数量
  while (tradeStream.children.length > 20) {
    tradeStream.removeChild(tradeStream.lastChild);
  }
}

// 添加到历史表格
function addToHistoryTable(record) {
  const isWin = record.estimatedPnl > 0;
  
  const row = document.createElement("tr");
  row.innerHTML = `
    <td>${formatRelativeTime(record.timestamp)}</td>
    <td>${record.market}</td>
    <td><span class="badge ${isWin ? 'badge-profit' : 'badge-loss'}">${record.side}</span></td>
    <td>${formatCurrency(record.originalAmount)}</td>
    <td>${formatCurrency(record.copyAmount)}</td>
    <td>${record.copyRatio * 100}%</td>
    <td class="${isWin ? 'positive' : 'negative'}">${isWin ? '+' : ''}${formatCurrency(record.estimatedPnl)}</td>
    <td><span class="badge ${isWin ? 'badge-profit' : 'badge-loss'}">${isWin ? '盈利' : '亏损'}</span></td>
    <td style="font-family: monospace; font-size: 12px;">${record.txHash}</td>
  `;
  
  copyHistoryTable.insertBefore(row, copyHistoryTable.firstChild);
  
  // 限制显示数量
  while (copyHistoryTable.children.length > 50) {
    copyHistoryTable.removeChild(copyHistoryTable.lastChild);
  }
}

// 更新显示
function updateDisplay() {
  balanceValue.textContent = formatCurrency(balance);
  totalTrades.textContent = tradeCount;
  winTrades.textContent = winCount;
  lossTrades.textContent = lossCount;
  totalPnlEl.textContent = formatCurrency(totalPnl);
  
  // 更新余额颜色
  if (balance >= 100000) {
    balanceValue.className = "balance-value";
  } else if (balance >= 50000) {
    balanceValue.className = "balance-value warning";
  } else {
    balanceValue.className = "balance-value danger";
  }
}

// 初始化盈亏图表
function initPnlChart() {
  const ctx = document.getElementById("pnlChart").getContext("2d");
  charts.pnlChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: [],
      datasets: [{
        label: "盈亏",
        data: [],
        borderColor: "#00d4ff",
        backgroundColor: "rgba(0, 212, 255, 0.1)",
        fill: true,
        tension: 0.4,
        pointRadius: 0
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => formatCurrency(ctx.parsed.y)
          }
        }
      },
      scales: {
        x: {
          grid: { color: "rgba(255,255,255,0.05)" },
          ticks: { display: false }
        },
        y: {
          grid: { color: "rgba(255,255,255,0.05)" },
          ticks: { 
            color: "#8888a0",
            callback: v => formatCurrency(v)
          }
        }
      }
    }
  });
}

// 更新盈亏图表
function updatePnlChart() {
  if (!charts.pnlChart) return;
  
  const labels = pnlHistory.map((_, i) => i);
  charts.pnlChart.data.labels = labels;
  charts.pnlChart.data.datasets[0].data = pnlHistory;
  charts.pnlChart.update();
}

// 开始模拟
function startSimulation() {
  if (simInterval) return;
  
  const frequency = parseInt(document.getElementById("tradeFrequency").value);
  
  simInterval = setInterval(async () => {
    const trade = generateMockTrade();
    await executeCopyTrade(trade);
  }, frequency);
  
  document.getElementById("startSimBtn").innerHTML = '<i class="fas fa-pause"></i> 运行中...';
  document.getElementById("startSimBtn").classList.add("monitoring");
  showToast("模拟已开始", "success");
}

// 暂停模拟
function pauseSimulation() {
  if (!simInterval) return;
  
  clearInterval(simInterval);
  simInterval = null;
  
  document.getElementById("startSimBtn").innerHTML = '<i class="fas fa-play"></i> 继续模拟';
  document.getElementById("startSimBtn").classList.remove("monitoring");
  showToast("模拟已暂停", "info");
}

// 停止模拟
function stopSimulation() {
  if (simInterval) {
    clearInterval(simInterval);
    simInterval = null;
  }
  
  document.getElementById("startSimBtn").innerHTML = '<i class="fas fa-play"></i> 开始模拟';
  document.getElementById("startSimBtn").classList.remove("monitoring");
  showToast("模拟已停止", "info");
}

// 单步执行
function singleStep() {
  const trade = generateMockTrade();
  executeCopyTrade(trade);
}

// 重置资金
function resetBalance() {
  balance = 100000;
  tradeCount = 0;
  winCount = 0;
  lossCount = 0;
  totalPnl = 0;
  pnlHistory = [0];
  
  tradeStream.innerHTML = '<div class="loading-state"><i class="fas fa-info-circle"></i><span>点击「开始模拟」开始操盘</span></div>';
  copyHistoryTable.innerHTML = '<tr><td colspan="9" class="loading-state"><i class="fas fa-info-circle"></i><span>暂无跟单记录</span></td></tr>';
  
  updateDisplay();
  updatePnlChart();
  showToast("资金已重置", "info");
}

// 清空历史
function clearHistory() {
  copyHistoryTable.innerHTML = '<tr><td colspan="9" class="loading-state"><i class="fas fa-info-circle"></i><span>暂无跟单记录</span></td></tr>';
  showToast("历史已清空", "info");
}

// 时间更新
function updateTime() {
  document.getElementById("currentTime").textContent = formatTime(new Date());
}

// 事件监听
function initEventListeners() {
  // 跟单比例按钮
  document.querySelectorAll("#copyRatioButtons .ratio-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#copyRatioButtons .ratio-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      copyRatio = parseFloat(btn.dataset.ratio);
      showToast(`跟单比例已设置为 ${copyRatio * 100}%`, "info");
    });
  });
  
  // 开始按钮
  document.getElementById("startSimBtn").addEventListener("click", () => {
    if (simInterval) {
      pauseSimulation();
    } else {
      startSimulation();
    }
  });
  
  // 暂停按钮
  document.getElementById("pauseSimBtn").addEventListener("click", pauseSimulation);
  
  // 停止按钮
  document.getElementById("stopSimBtn").addEventListener("click", stopSimulation);
  
  // 单步按钮
  document.getElementById("singleTradeBtn").addEventListener("click", singleStep);
  
  // 重置资金
  document.getElementById("resetBalance").addEventListener("click", resetBalance);
  
  // 清空历史
  document.getElementById("clearHistoryBtn").addEventListener("click", clearHistory);
  
  // 导航
  document.querySelectorAll(".nav-item").forEach(item => {
    item.addEventListener("click", (e) => {
      e.preventDefault();
      if (item.dataset.view === "dashboard") {
        window.location.href = "/";
      }
    });
  });
}

// 初始化
function init() {
  initPnlChart();
  initEventListeners();
  updateTime();
  setInterval(updateTime, 1000);
  updateDisplay();
}

document.addEventListener("DOMContentLoaded", init);
