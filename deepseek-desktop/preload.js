// 预加载脚本：默认保持隔离，不注入任何特权 API。
// 如后续需要向页面暴露受控能力（例如系统通知、快捷键），可在此扩展。
const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('deepseekDesktop', {
  version: '1.0.0',
});
