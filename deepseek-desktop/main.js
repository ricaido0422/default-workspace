const {
  app,
  BrowserWindow,
  Menu,
  shell,
  session,
  nativeImage,
  Tray,
  dialog,
} = require('electron');
const path = require('path');
const fs = require('fs');

const HOME_URL = 'https://chat.deepseek.com/';
const DEEPSEEK_HOSTS = new Set([
  'chat.deepseek.com',
  'www.deepseek.com',
  'deepseek.com',
  'cdn.deepseek.com',
  'api.deepseek.com',
]);

let mainWindow = null;
let tray = null;
let isQuitting = false;

const isDev = process.argv.includes('--dev');

function isDeepSeekUrl(url) {
  try {
    const u = new URL(url);
    if (u.protocol !== 'https:' && u.protocol !== 'http:') return false;
    return DEEPSEEK_HOSTS.has(u.hostname) || u.hostname.endsWith('.deepseek.com');
  } catch {
    return false;
  }
}

function createWindow() {
  const iconPath = path.join(__dirname, 'assets', 'icon.png');
  const hasIcon = fs.existsSync(iconPath);

  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 800,
    minHeight: 600,
    title: 'DeepSeek 助手',
    ...(hasIcon ? { icon: iconPath } : {}),
    autoHideMenuBar: true,
    backgroundColor: '#ffffff',
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  // 去掉 Electron 标识，避免个别站点行为异常
  const ua = mainWindow.webContents.getUserAgent()
    .replace(/\sElectron\/[\d.]+/, '')
    .replace(/deepseek-desktop\/[\d.]+/i, '');
  mainWindow.webContents.setUserAgent(ua);

  // 站内跳转用本窗口，站外链接交给系统浏览器
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (isDeepSeekUrl(url)) {
      return { action: 'allow' };
    }
    shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (!isDeepSeekUrl(url)) {
      event.preventDefault();
      shell.openExternal(url);
    }
  });

  mainWindow.on('close', (event) => {
    if (!isQuitting) {
      event.preventDefault();
      mainWindow.hide();
    }
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  mainWindow.loadURL(HOME_URL);
}

function buildMenu() {
  const template = [
    {
      label: '文件',
      submenu: [
        { role: 'quit', label: '退出' },
      ],
    },
    {
      label: '编辑',
      submenu: [
        { role: 'undo', label: '撤销' },
        { role: 'redo', label: '重做' },
        { type: 'separator' },
        { role: 'cut', label: '剪切' },
        { role: 'copy', label: '复制' },
        { role: 'paste', label: '粘贴' },
        { role: 'selectAll', label: '全选' },
      ],
    },
    {
      label: '视图',
      submenu: [
        { role: 'reload', label: '刷新' },
        { role: 'forceReload', label: '强制刷新' },
        { role: 'toggleDevTools', label: '开发者工具' },
        { type: 'separator' },
        { role: 'resetZoom', label: '重置缩放' },
        { role: 'zoomIn', label: '放大' },
        { role: 'zoomOut', label: '缩小' },
        { type: 'separator' },
        { role: 'togglefullscreen', label: '全屏' },
      ],
    },
    {
      label: '导航',
      submenu: [
        {
          label: '后退',
          accelerator: 'Alt+Left',
          click: () => mainWindow && mainWindow.webContents.navigateBack(),
        },
        {
          label: '前进',
          accelerator: 'Alt+Right',
          click: () => mainWindow && mainWindow.webContents.navigateForward(),
        },
        { type: 'separator' },
        {
          label: '回到首页',
          click: () => mainWindow && mainWindow.loadURL(HOME_URL),
        },
      ],
    },
    {
      label: '账户',
      submenu: [
        {
          label: '清除登录信息（重新登录）',
          click: async () => {
            if (!mainWindow) return;
            const ses = mainWindow.webContents.session;
            await ses.clearStorageData();
            await ses.clearCache();
            mainWindow.loadURL(HOME_URL);
          },
        },
      ],
    },
    {
      label: '帮助',
      submenu: [
        {
          label: '打开 DeepSeek 官网',
          click: () => shell.openExternal('https://www.deepseek.com/'),
        },
        {
          label: '关于',
          click: () => {
            dialog.showMessageBox(mainWindow, {
              type: 'info',
              title: '关于',
              message: 'DeepSeek 助手',
              detail: '基于 Electron 的 DeepSeek 网页问答助手桌面版。',
            });
          },
        },
      ],
    },
  ];

  if (isDev) {
    template.push({
      label: '调试',
      submenu: [
        { role: 'toggleDevTools', label: '开发者工具' },
      ],
    });
  }

  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function createTray() {
  const iconPath = path.join(__dirname, 'assets', 'icon.png');
  if (!fs.existsSync(iconPath)) return;

  let img = nativeImage.createFromPath(iconPath);
  img = img.resize({ width: 16, height: 16 });

  tray = new Tray(img);
  tray.setToolTip('DeepSeek 助手');
  const contextMenu = Menu.buildFromTemplate([
    {
      label: '显示窗口',
      click: () => showWindow(),
    },
    { type: 'separator' },
    {
      label: '退出',
      click: () => {
        isQuitting = true;
        app.quit();
      },
    },
  ]);
  tray.setContextMenu(contextMenu);
  tray.on('click', () => showWindow());
}

function showWindow() {
  if (!mainWindow) {
    createWindow();
  } else {
    mainWindow.show();
    mainWindow.focus();
  }
}

app.whenReady().then(() => {
  buildMenu();
  createWindow();
  createTray();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    } else {
      showWindow();
    }
  });
});

app.on('before-quit', () => {
  isQuitting = true;
});

app.on('window-all-closed', () => {
  // macOS 上通常保留应用，但这里做成点关闭即隐藏到托盘，因此不退出
  if (process.platform !== 'darwin') {
    // 不退出，由托盘控制
  }
});
