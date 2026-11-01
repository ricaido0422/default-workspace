# DeepSeek 助手（桌面版）

把 DeepSeek 网页问答助手（https://chat.deepseek.com/）封装成独立桌面应用，
不用每次打开浏览器，双击即用，登录状态独立保存，可常驻系统托盘、关闭即最小化。

## 功能特性

- **独立窗口**：干净的应用窗口，无浏览器地址栏/标签页，专注问答。
- **登录独立**：登录态保存在应用专属目录，与浏览器互不影响，重新登录请用菜单「账户 → 清除登录信息」。
- **关闭即隐藏**：点关闭按钮不会退出，而是最小化到系统托盘，随时唤回。
- **系统托盘**：托盘图标右键可显示窗口 / 退出。
- **站外链接拦截**：只有 DeepSeek 相关域名在应用内打开，其它链接交给系统默认浏览器。
- **导航与缩放**：菜单提供刷新、后退/前进、缩放、全屏、开发者工具等。

## 运行（开发模式）

**方式一（最简单，双击即可）**：双击项目目录下的 `启动.bat`。

**方式二（命令行）**：

```bash
npm install
npm start
```

> 注意：`npm install` 会触发 Electron 二进制的自动下载（约 100MB）。
> 若网络受限，可先设置镜像后再安装：
> ```bash
> # Windows PowerShell
> $env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
> npm install
> ```

## 打包成安装包 / 便携版

```bash
# 生成 Windows 安装程序（NSIS）与免安装便携版（portable）
npm run dist:win

# 生成 macOS 安装包（需在 macOS 上执行）
npm run dist:mac
```

打包产物输出到 `dist/` 目录：

- `dist/DeepSeek 助手 Setup x.x.x.exe` —— 安装程序
- `dist/DeepSeek 助手 x.x.x.exe` —— 免安装便携版（双击直接运行）

## 项目结构

```
deepseek-desktop/
├── main.js           # 主进程：窗口、菜单、托盘、链接拦截
├── preload.js        # 预加载脚本（保持隔离）
├── package.json      # 依赖与打包配置
├── make_icon.py      # 图标生成脚本（可选）
└── assets/           # 应用图标 icon.png / icon.ico
```

## 常见问题

**双击后窗口没出现 / 一闪而过？** 若当前环境设置了 `ELECTRON_RUN_AS_NODE=1`，Electron 会退化成纯 Node 而不显示窗口。`启动.bat` 已自动清除该变量；命令行运行时请先执行 `set ELECTRON_RUN_AS_NODE=`。

**登录状态无法保存？** 应用使用独立数据目录，与浏览器隔离；若需要切换账号，使用菜单「账户 → 清除登录信息（重新登录）」。

**想开机自启？** 将打包后的「DeepSeek 助手」快捷方式放入 Windows 启动文件夹即可：
`Win + R` → 输入 `shell:startup` → 放入快捷方式。

## 说明

本项目仅为官方网页版的本地封装，不包含任何第三方数据收集，所有对话数据仍由 DeepSeek 官方处理。
