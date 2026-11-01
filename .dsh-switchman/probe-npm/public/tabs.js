/* 标签页切换 + 分析中心各按钮的初始化。
   单独成一个文件，是为了让 app.js（主面板）和 leaderboard.js（分析中心）互不依赖。 */
(function () {
  // ---- 右侧主面板的「跟单记录 / 系统日志」两个页签 ----
  document.querySelectorAll('.tab[data-tab="events"], .tab[data-tab="logs"]').forEach((t) => {
    t.onclick = () => {
      document.querySelectorAll('.tab[data-tab="events"], .tab[data-tab="logs"]').forEach((x) => x.classList.remove('active'));
      t.classList.add('active');
      const name = t.dataset.tab;
      const a = document.getElementById('tab-events');
      const b = document.getElementById('tab-logs');
      if (a) a.classList.toggle('hidden', name !== 'events');
      if (b) b.classList.toggle('hidden', name !== 'logs');
    };
  });

  // ---- 分析中心的四个页签 ----
  const TABS = ['lb', 'audit', 'rec', 'scope', 'wl'];
  const panes = Object.fromEntries(TABS.map((k) => [k, document.getElementById('pane-' + k)]));
  document.querySelectorAll(TABS.map((t) => `.tab[data-tab="${t}"]`).join(','))
    .forEach((t) => {
      t.onclick = () => {
        document.querySelectorAll(TABS.map((t2) => `.tab[data-tab="${t2}"]`).join(','))
          .forEach((x) => x.classList.remove('active'));
        t.classList.add('active');
        for (const [k, el] of Object.entries(panes)) {
          if (el) el.classList.toggle('hidden', k !== t.dataset.tab);
        }
      };
    });

  // ---- 按钮绑定 ----
  const lb = window.leaderboard;
  if (!lb) return;

  const on = (id, fn) => { const el = document.getElementById(id); if (el) el.onclick = fn; };

  on('btnLbRefresh', () => lb.loadLeaderboard());
  on('btnAudit', () => lb.loadAudit());
  on('btnRec', () => lb.loadRecommend());
  on('btnWlAdd', () => lb.addToWatchlist());
  on('btnScopeScan', () => lb.loadScopeScan());
  on('btnScopeSim', () => lb.loadScopeSim());

  // 打开页面时先把名单拉下来填好
  lb.loadWatchlist();
})();
