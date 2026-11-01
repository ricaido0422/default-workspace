// Polymarket 跟单系统 - 多钱包管理 v2.0

const API_BASE = "/api";
let monitoring = false;
let refreshInterval = null;
let lastData = null;
let charts = {};
let currentView = "dashboard";
let config = {};

// DOM 元素
const statusDot = document.getElementById("statusDot");
const statusText = document.getElementById("statusText");
const monitorBtn = document.getElementById("monitorBtn");
const refreshBtn = document.getElementById("refreshBtn");
const currentTime = document.getElementById("currentTime");
const pageTitle = document.getElementById("pageTitle");
const activeWalletName = document.getElementById("activeWalletName");
const toastContainer = document.getElementById("toastContainer");

// 格式化函数
function formatNumber(num) {
  if (num >= 1000000) return (num / 1000000).toFixed(2) + "M";
  if (num >= 1000) return (num / 1000).toFixed(1) + "k";
  return num.toFixed(0);
}

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
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return minutes + " 分钟前";
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return hours + " 小时前";
  return Math.floor(hours / 24) + " 天前";
}

function truncateAddress(addr) {
  if (!addr || addr.length < 12) return addr;
  return addr.slice(0, 6) + "..." + addr.slice(-4);
}

// 更新状态
function updateStatus(connected) {
  statusDot.className = connected ? "connected" : "";
  statusText.textContent = connected ? "已连接" : "断开";
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

// 加载配置
async function loadConfig() {
  try {
    const res = await fetch(`${API_BASE}/config`);
    const data = await res.json();
    if (data.success) {
      config = data.config;
      updateUI();
    }
  } catch (e) {
    console.error("加载配置失败:", e);
  }
}

// 更新 UI
function updateUI() {
  // 更新操作钱包
  const tradingWallet = config.tradingWallet || "";
  document.getElementById("tradingAddress").textContent = truncateAddress(tradingWallet);
  
  // 更新跟踪钱包列表
  updateTrackedList();
  
  // 更新活动跟踪钱包
  if (config.activeTracked) {
    const tracked = config.trackedWallets.find(w => w.wallet === config.activeTracked);
    activeWalletName.textContent = tracked?.name || truncateAddress(config.activeTracked);
  }
}

// 更新跟踪钱包列表
function updateTrackedList() {
  const list = document.getElementById("trackedWalletsList");
  if (!config.trackedWallets || config.trackedWallets.length === 0) {
    list.innerHTML = '<div class="loading-state" style="padding: 20px;"><span>暂无跟踪钱包</span></div>';
    return;
  }

  list.innerHTML = config.trackedWallets.map(w => `
    <div class="tracked-wallet-item ${w.wallet === config.activeTracked ? 'active' : ''}" data-wallet="${w.wallet}">
      <div class="tracked-wallet-avatar" style="background: linear-gradient(135deg, ${w.wallet === config.activeTracked ? '#00d4ff' : '#7b2cbf'}, #a855f7);">
        <i class="fas ${w.wallet === config.activeTracked ? 'fa-check' : 'fa-eye'}"></i>
      </div>
      <div class="tracked-wallet-info">
        <div class="tracked-wallet-name">${w.name}</div>
        <div class="tracked-wallet-address">${truncateAddress(w.wallet)}</div>
      </div>
      <div class="tracked-wallet-actions">
        <button class="btn-icon-sm" title="设置" onclick="event.stopPropagation(); setActiveWallet('${w.wallet}')">
          <i class="fas ${w.wallet === config.activeTracked ? 'fa-check' : 'fa-cog'}"></i>
        </button>
        <button class="btn-icon-sm delete" title="删除" onclick="event.stopPropagation(); removeTrackedWallet('${w.wallet}')">
          <i class="fas fa-trash"></i>
        </button>
      </div>
    </div>
  `).join("");

  // 点击切换活动钱包
  list.querySelectorAll(".tracked-wallet-item").forEach(item => {
    item.addEventListener("click", () => {
      const wallet = item.dataset.wallet;
      setActiveWallet(wallet);
    });
  });
}

// 设置活动跟踪钱包
async function setActiveWallet(wallet) {
  try {
    const res = await fetch(`${API_BASE}/active-tracked`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ wallet })
    });
    const data = await res.json();
    if (data.success) {
      config.activeTracked = wallet;
      updateUI();
      fetchDashboard();
      showToast("已切换跟踪钱包", "success");
    }
  } catch (e) {
    showToast("切换失败: " + e.message, "error");
  }
}

// 删除跟踪钱包
async function removeTrackedWallet(wallet) {
  try {
    const res = await fetch(`${API_BASE}/tracked-wallets?wallet=${wallet}`, { method: "DELETE" });
    const data = await res.json();
    if (data.success) {
      config.trackedWallets = data.trackedWallets;
      updateUI();
      showToast("已删除跟踪钱包", "info");
    }
  } catch (e) {
    showToast("删除失败: " + e.message, "error");
  }
}

// 更新仪表盘数据
async function fetchDashboard() {
  try {
    const res = await fetch(`${API_BASE}/active-tracked`);
    const data = await res.json();

    if (data.success) {
      updateStatus(true);
      lastData = data;
      
      // 更新统计数据
      if (data.stats) {
        const pnl = data.stats.realized_pnl || data.stats.total_pnl || 0;
        document.getElementById("pnlValue").textContent = formatCurrency(pnl);
        document.getElementById("volumeValue").textContent = formatCurrency(data.stats.volume || 0);
        document.getElementById("tradesValue").textContent = formatNumber(data.stats.trades || 0);
      }

      if (data.value && data.value.length > 0) {
        document.getElementById("portfolioValue").textContent = formatCurrency(data.value[0].value || 0);
      }

      // 更新实时交易流
      updateTradeStream(data);
    } else {
      updateStatus(false);
    }
  } catch (e) {
    console.error("获取数据失败:", e);
    updateStatus(false);
  }
}

// 更新实时交易流
function updateTradeStream(data) {
  const container = document.getElementById("tradeStream");
  if (!data.trades || data.trades.length === 0) {
    container.innerHTML = '<div class="loading-state"><i class="fas fa-info-circle"></i><span>暂无交易数据</span></div>';
    return;
  }

  container.innerHTML = data.trades.slice(0, 20).map(trade => {
    const isBuy = trade.side === "BUY";
    const amount = trade.size || trade.amount || 0;
    const market = trade.market || trade.condition_id || "未知";

    return `
      <div class="trade-item ${isBuy ? 'buy' : 'sell'}">
        <div class="trade-item-info">
          <div class="trade-item-icon ${isBuy ? 'buy' : 'sell'}">
            <i class="fas fa-${isBuy ? 'arrow-up' : 'arrow-down'}"></i>
          </div>
          <div>
            <div class="trade-item-market">${market}</div>
            <div class="trade-item-detail">${isBuy ? '买入' : '卖出'} ${trade.side === 'YES' ? 'YES' : 'NO'}</div>
          </div>
        </div>
        <div class="trade-item-stats">
          <div class="trade-item-amount">
            <div class="trade-item-amount-value ${isBuy ? 'positive' : 'negative'}">${formatCurrency(amount)}</div>
          </div>
          <div class="trade-item-time">${formatRelativeTime(trade.timestamp || trade.created_at)}</div>
        </div>
      </div>
    `;
  }).join("");
}

// 更新持仓表格
function updatePositionsTable(positions) {
  const tbody = document.getElementById("positionsTable");
  if (!positions || positions.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" class="loading-state"><i class="fas fa-info-circle"></i><span>暂无活跃持仓</span></td></tr>';
    return;
  }

  tbody.innerHTML = positions.map(pos => {
    const pnl = pos.unrealized_pnl || 0;
    const pnlPercent = pos.unrealized_pnl_percent || 0;
    const isPositive = pnl >= 0;
    const market = pos.market || pos.condition_id || "未知";

    return `
      <tr>
        <td>${market}</td>
        <td><span class="badge ${isPositive ? 'badge-profit' : 'badge-loss'}">${pos.side === 'YES' ? '买入 YES' : '买入 NO'}</span></td>
        <td>${formatNumber(pos.size || 0)}</td>
        <td>${pos.entry_price?.toFixed(4) || '--'}</td>
        <td>${pos.current_price?.toFixed(4) || '--'}</td>
        <td class="${isPositive ? 'positive' : 'negative'}">${formatCurrency(pnl)}</td>
        <td class="${isPositive ? 'positive' : 'negative'}">${isPositive ? '+' : ''}${pnlPercent.toFixed(2)}%</td>
        <td><button class="btn-icon" title="跟单"><i class="fas fa-copy"></i></button></td>
      </tr>
    `;
  }).join("");
}

// 更新交易表格
function updateTradesTable(trades) {
  const tbody = document.getElementById("tradesTable");
  if (!trades || trades.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" class="loading-state"><i class="fas fa-info-circle"></i><span>暂无交易记录</span></td></tr>';
    return;
  }

  tbody.innerHTML = trades.slice(0, 50).map(trade => {
    const isBuy = trade.side === "BUY";
    const amount = trade.size || trade.amount || 0;
    const pnl = trade.pnl || 0;
    const isPositive = pnl >= 0;
    const market = trade.market || trade.condition_id || "未知";

    return `
      <tr>
        <td>${formatRelativeTime(trade.timestamp || trade.created_at)}</td>
        <td>${market}</td>
        <td><span class="badge ${isBuy ? 'badge-profit' : 'badge-loss'}">${isBuy ? '买入' : '卖出'}</span></td>
        <td>${formatCurrency(amount)}</td>
        <td>${trade.price?.toFixed(4) || '--'}</td>
        <td class="${isPositive ? 'positive' : 'negative'}">${isPositive ? '+' : ''}${formatCurrency(pnl)}</td>
        <td><span class="badge ${isPositive ? 'badge-profit' : 'badge-loss'}">${isPositive ? '盈利' : '亏损'}</span></td>
      </tr>
    `;
  }).join("");
}

// 获取排行榜
async function fetchLeaderboard() {
  try {
    const res = await fetch(`${API_BASE}/leaderboard?limit=50`);
    const data = await res.json();
    if (data.success) {
      updateLeaderboardTable(data.leaderboard);
      // 填充搜索下拉框
      const select = document.getElementById("leaderboardSelect");
      select.innerHTML = '<option value="">-- 选择用户 --</option>' + 
        data.leaderboard.slice(0, 20).map(u => 
          `<option value="${u.wallet || u.user}">${u.username || u.name} (${formatCurrency(u.pnl || u.realized_pnl)})</option>`
        ).join("");
    }
  } catch (e) {
    console.error("获取排行榜失败:", e);
  }
}

// 更新排行榜表格
function updateLeaderboardTable(leaderboard) {
  const tbody = document.getElementById("leaderboardTable");
  if (!leaderboard || leaderboard.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="loading-state"><i class="fas fa-info-circle"></i><span>暂无排行榜数据</span></td></tr>';
    return;
  }

  tbody.innerHTML = leaderboard.slice(0, 50).map((user, index) => {
    const pnl = user.pnl || user.realized_pnl || 0;
    const isPositive = pnl >= 0;
    const wallet = user.wallet || user.user;
    const isTracked = config.trackedWallets?.some(w => w.wallet === wallet);

    return `
      <tr>
        <td>
          <span class="rank-badge ${index < 3 ? 'gold' : ''}">${index + 1}</span>
        </td>
        <td>
          <div class="user-cell">
            <div class="user-name">${user.username || user.name || truncateAddress(wallet)}</div>
            <div class="user-wallet">${truncateAddress(wallet)}</div>
          </div>
        </td>
        <td class="${isPositive ? 'positive' : 'negative'}">${formatCurrency(pnl)}</td>
        <td>${formatCurrency(user.volume || 0)}</td>
        <td>${formatNumber(user.trades || 0)}</td>
        <td>
          <button class="btn-icon ${isTracked ? 'active' : ''}" onclick="trackFromLeaderboard('${wallet}', '${user.username || user.name}')" title="${isTracked ? '已跟踪' : '添加跟踪'}">
            <i class="fas ${isTracked ? 'fa-check' : 'fa-plus'}"></i>
          </button>
        </td>
      </tr>
    `;
  }).join("");
}

// 从排行榜添加跟踪
async function trackFromLeaderboard(wallet, name) {
  try {
    const res = await fetch(`${API_BASE}/tracked-wallets`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ wallet, name })
    });
    const data = await res.json();
    if (data.success) {
      config.trackedWallets = data.trackedWallets;
      updateUI();
      showToast(`已添加 ${name} 到跟踪列表`, "success");
    }
  } catch (e) {
    showToast("添加失败: " + e.message, "error");
  }
}

// 添加跟踪钱包
async function addTrackedWallet(wallet, name) {
  try {
    const res = await fetch(`${API_BASE}/tracked-wallets`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ wallet, name })
    });
    const data = await res.json();
    if (data.success) {
      config.trackedWallets = data.trackedWallets;
      updateUI();
      showToast("已添加跟踪钱包", "success");
      closeModal("addWalletModal");
    }
  } catch (e) {
    showToast("添加失败: " + e.message, "error");
  }
}

// 切换操作钱包
async function switchTradingWallet(wallet) {
  try {
    const res = await fetch(`${API_BASE}/trading-wallet`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ wallet })
    });
    const data = await res.json();
    if (data.success) {
      config.tradingWallet = wallet;
      updateUI();
      showToast("已切换操作钱包", "success");
      closeModal("switchWalletModal");
    }
  } catch (e) {
    showToast("切换失败: " + e.message, "error");
  }
}

// 保存设置
async function saveSettings() {
  try {
    const refreshRate = document.getElementById("refreshRate").value;
    const notifyBrowser = document.getElementById("notifyBrowser").checked;
    const notifySound = document.getElementById("notifySound").checked;

    config.refreshRate = parseInt(refreshRate);
    config.notifyBrowser = notifyBrowser;
    config.notifySound = notifySound;

    const res = await fetch(`${API_BASE}/config`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config)
    });
    const data = await res.json();
    if (data.success) {
      showToast("设置已保存", "success");
    }
  } catch (e) {
    showToast("保存失败: " + e.message, "error");
  }
}

// Modal 控制
function openModal(id) {
  document.getElementById(id).style.display = "flex";
}

function closeModal(id) {
  document.getElementById(id).style.display = "none";
}

// 开始监控
function startMonitoring() {
  if (monitoring) return;

  monitoring = true;
  monitorBtn.innerHTML = '<i class="fas fa-pause"></i><span>停止监控</span>';
  monitorBtn.classList.add("monitoring");

  const rate = parseInt(config.refreshRate || 30) * 1000;
  refreshInterval = setInterval(fetchDashboard, rate);

  fetchDashboard();
  showToast("监控已启动", "success");
}

// 停止监控
function stopMonitoring() {
  if (!monitoring) return;

  monitoring = false;
  monitorBtn.innerHTML = '<i class="fas fa-play"></i><span>开始监控</span>';
  monitorBtn.classList.remove("monitoring");

  if (refreshInterval) {
    clearInterval(refreshInterval);
    refreshInterval = null;
  }

  showToast("监控已停止", "info");
}

// 切换视图
function switchView(view) {
  currentView = view;

  document.querySelectorAll(".nav-item").forEach(item => {
    item.classList.toggle("active", item.dataset.view === view);
  });

  document.querySelectorAll(".view").forEach(v => {
    v.classList.toggle("active", v.id === `view-${view}`);
  });

  const titles = {
    dashboard: "仪表盘",
    positions: "持仓分析",
    trades: "实时交易",
    leaderboard: "排行榜",
    settings: "设置"
  };
  pageTitle.textContent = titles[view] || "仪表盘";

  // 切换到交易视图时加载数据
  if (view === "trades") {
    loadTrades();
  }

  // 切换到排行榜时加载数据
  if (view === "leaderboard") {
    fetchLeaderboard();
  }
}

// 加载交易数据
async function loadTrades() {
  try {
    const res = await fetch(`${API_BASE}/active-tracked/trades`);
    const data = await res.json();
    if (data.success) {
      updateTradesTable(data.trades);
    }
  } catch (e) {
    console.error("加载交易数据失败:", e);
  }
}

// 时间更新
function updateTime() {
  currentTime.textContent = formatTime(new Date());
}

// 事件监听
function initEventListeners() {
  // 导航点击
  document.querySelectorAll(".nav-item").forEach(item => {
    item.addEventListener("click", (e) => {
      e.preventDefault();
      switchView(item.dataset.view);
    });
  });

  // 监控按钮
  monitorBtn.addEventListener("click", () => {
    if (monitoring) {
      stopMonitoring();
    } else {
      startMonitoring();
    }
  });

  // 刷新按钮
  refreshBtn.addEventListener("click", () => {
    fetchDashboard();
    showToast("已刷新数据", "success");
  });

  // 刷新频率
  document.getElementById("refreshRate").addEventListener("change", () => {
    if (monitoring) {
      stopMonitoring();
      startMonitoring();
    }
  });

  // 添加跟踪钱包按钮
  document.getElementById("addTrackedWalletBtn").addEventListener("click", () => {
    openModal("addWalletModal");
  });

  document.getElementById("addTrackedWalletSettings").addEventListener("click", () => {
    openModal("addWalletModal");
  });

  // 关闭 Modal
  document.getElementById("closeModal").addEventListener("click", () => {
    closeModal("addWalletModal");
  });

  document.getElementById("closeSwitchModal").addEventListener("click", () => {
    closeModal("switchWalletModal");
  });

  // 确认添加钱包
  document.getElementById("confirmAddWallet").addEventListener("click", () => {
    const wallet = document.getElementById("walletAddress").value.trim();
    const name = document.getElementById("walletName").value.trim();
    const fromLeaderboard = document.getElementById("leaderboardSelect").value;

    if (fromLeaderboard) {
      addTrackedWallet(fromLeaderboard, name || "");
    } else if (wallet) {
      addTrackedWallet(wallet, name);
    } else {
      showToast("请输入钱包地址", "error");
    }
  });

  // 排行榜选择
  document.getElementById("leaderboardSelect").addEventListener("change", (e) => {
    if (e.target.value) {
      document.getElementById("walletAddress").value = e.target.value;
    }
  });

  // 切换操作钱包
  document.getElementById("changeTradingWallet").addEventListener("click", () => {
    openModal("switchWalletModal");
  });

  document.getElementById("confirmSwitchWallet").addEventListener("click", () => {
    const wallet = document.getElementById("switchWalletSelect").value;
    if (wallet) {
      switchTradingWallet(wallet);
    }
  });

  // 保存设置
  document.getElementById("saveSettings").addEventListener("click", saveSettings);

  // 过滤器
  document.querySelectorAll(".filter-group").forEach(group => {
    group.querySelectorAll(".filter-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        group.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
      });
    });
  });

  // 通知权限
  if ("Notification" in window && Notification.permission === "default") {
    Notification.requestPermission();
  }
}

// 初始化
async function init() {
  await loadConfig();
  initEventListeners();
  updateTime();
  setInterval(updateTime, 1000);

  // 初始加载
  fetchDashboard();
  fetchLeaderboard();
}

// 页面加载完成后初始化
document.addEventListener("DOMContentLoaded", init);
