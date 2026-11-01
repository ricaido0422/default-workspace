const fs = require('fs');
const path = require('path');
const root = 'D:\\Program Files (x86)\\DeepSeek\\resources\\app.asar\\dsh\\node_modules\\@deepseek-ai';

function read(p) {
  try { return fs.readFileSync(path.join(root, p), 'utf8'); }
  catch (e) { return 'ERR: ' + e.message; }
}

console.log('===== dsh-desktop-host/lib/cli.js =====');
console.log(read(path.join('dsh-desktop-host', 'lib', 'cli.js')));
console.log('\n===== dsh-desktop-host/lib/index.js =====');
console.log(read(path.join('dsh-desktop-host', 'lib', 'index.js')));
console.log('\n===== dsh-desktop-host/package.json =====');
console.log(read(path.join('dsh-desktop-host', 'package.json')));
console.log('\n===== dsh-experimental-plugin-market-bundle/cordis.patch.yml =====');
console.log(read(path.join('dsh-experimental-plugin-market-bundle', 'cordis.patch.yml')));
console.log('\n===== dsh-client-ui-plugin-market/package.json =====');
console.log(read(path.join('dsh-client-ui-plugin-market', 'package.json')));
