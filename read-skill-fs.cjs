const fs = require('fs');
const path = require('path');
const f = path.join('D:\\Program Files (x86)\\DeepSeek\\resources', 'app.asar', 'dsh', 'node_modules', '@deepseek-ai', 'dsh-skill-filesystem', 'lib', 'index.js');
console.log(fs.readFileSync(f, 'utf8'));
