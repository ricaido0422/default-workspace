Add-Type -AssemblyName System.Drawing
$bmp=New-Object System.Drawing.Bitmap 1440,900
$g=[System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode='AntiAlias'
$g.TextRenderingHint='AntiAliasGridFit'
function Fill($color,$x,$y,$w,$h){$b=New-Object System.Drawing.SolidBrush([System.Drawing.ColorTranslator]::FromHtml($color));$g.FillRectangle($b,$x,$y,$w,$h);$b.Dispose()}
function Txt($s,$x,$y,$size=16,$color='#DEE5EC'){$f=New-Object System.Drawing.Font('Microsoft YaHei UI',$size,[System.Drawing.FontStyle]::Regular,[System.Drawing.GraphicsUnit]::Pixel);$b=New-Object System.Drawing.SolidBrush([System.Drawing.ColorTranslator]::FromHtml($color));$g.DrawString($s,$f,$b,$x,$y);$f.Dispose();$b.Dispose()}
Fill '#101418' 0 0 1440 900
Fill '#181E24' 0 0 208 900
Txt 'PM / 跟单工作台' 22 28 20 '#B4F55B'
Txt 'COPY TRADING' 22 61 12 '#7E8B96'
Fill '#293725' 12 109 184 46
Txt '发现交易员' 30 121 17 '#B4F55B'
Txt '我的跟单' 30 186 17
Txt '交易台 · 审核' 30 246 17
Txt '持仓与结算' 30 306 17
Txt '收益与归因' 30 366 17
Txt '高级策略 / 设置' 30 426 17
Txt '实时成交 · 连接状态' 24 802 13 '#A0ADB8'
Txt '原型预览 / 数据仅为示意' 24 833 12 '#7E8B96'
Txt '发现交易员' 240 26 27
Txt '找到擅长目标板块的钱包，再配置独立的跟单规则。' 240 68 15 '#93A0AB'
Fill '#273A26' 1090 27 128 38
Txt 'PAPER 模拟盘' 1104 38 14 '#B4F55B'
Fill '#232A31' 1230 27 176 38
Txt '实盘接口 / 凭证检查' 1245 38 14
Fill '#1A2128' 240 114 360 87
Txt '虚拟可用资金' 260 129 14 '#93A0AB'
Txt '10,000.00 USDC' 260 155 25
Fill '#1A2128' 615 114 360 87
Txt '持仓浮动盈亏' 635 129 14 '#93A0AB'
Txt '— 以真实买一估值' 635 157 21
Fill '#1A2128' 990 114 416 87
Txt '运行状态' 1010 129 14 '#93A0AB'
Txt '半自动审核 · 止盈止损可配置' 1010 160 19
Fill '#1A2128' 240 222 780 642
Txt '排行榜 / 已观察钱包' 263 244 19
Fill '#11171C' 263 287 430 37
Txt '搜索用户名、备注或钱包地址' 276 297 14 '#83909B'
Txt '近 7 天  /  30 天  /  全部' 718 297 14
Fill '#293725' 263 342 64 31
Txt '全部' 280 349 14 '#B4F55B'
Txt '体育     政治     加密货币     经济     未分类' 348 349 14
Txt '交易员 / 擅长板块' 265 401 13 '#93A0AB'
Txt '官方区间盈亏' 620 401 13 '#93A0AB'
Txt '观测成交' 766 401 13 '#93A0AB'
Txt '操作' 933 401 13 '#93A0AB'
foreach($row in @(@('SportsWallet','体育 · 篮球 / 网球','—','—'),@('MacroWallet','政治 / 经济','—','—'),@('CryptoWallet','加密货币','—','—'))){$i=[array]::IndexOf(@('SportsWallet','MacroWallet','CryptoWallet'),$row[0]);$y=449+$i*107;Fill '#252E36' 263 ($y+77) 732 1;Txt $row[0] 267 $y 18;Txt $row[1] 267 ($y+29) 13 '#93A0AB';Txt $row[2] 654 ($y+14) 18;Txt $row[3] 788 ($y+14) 18;Fill '#B4F55B' 913 ($y+7) 77 34;Txt '配置跟单' 921 ($y+17) 13 '#172018'}
Txt '板块为人工标签；官方榜单与本工具观测收益分别展示。' 263 811 13 '#93A0AB'
Fill '#1A2128' 1040 222 366 642
Txt '快捷配置跟单' 1063 244 20
Txt 'SportsWallet  /  演示钱包' 1063 283 14 '#93A0AB'
Txt '金额方式' 1063 330 14
Fill '#293725' 1063 360 150 36
Txt '固定 USDC' 1086 370 14 '#B4F55B'
Fill '#252E36' 1223 360 156 36
Txt '按源交易比例' 1241 370 14
Txt '每笔金额 / 比例' 1063 419 14
Fill '#11171C' 1063 451 316 39
Txt '10 USDC' 1077 462 16
Txt '过滤范围' 1063 513 14
Txt '买入价格区间、源成交最小金额' 1063 547 14 '#93A0AB'
Txt '只买入 / 买卖均跟随' 1063 579 14 '#93A0AB'
Txt '模式绑定' 1063 630 14
Txt 'PAPER · 切实盘不沿用此规则' 1063 663 14 '#B4F55B'
Fill '#B4F55B' 1063 723 316 46
Txt '保存跟单规则' 1150 738 16 '#172018'
Txt '保存规则不改变全局自动 / 审核方式' 1063 794 13 '#93A0AB'
$bmp.Save((Join-Path (Get-Location) 'docs/design/copy-workbench-preview.png'),[System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose();$bmp.Dispose()
