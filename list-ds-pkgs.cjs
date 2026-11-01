const fs = require('fs');
const path = require('path');
const root = path.join('D:\\Program Files (x86)\\DeepSeek\\resources', 'app.asar', 'dsh', 'node_modules', '@deepseek-ai');
const names = fs.readdirSync(root);
console.log('=== all @deepseek-ai packages ===');
for (const n of names) console.log(n);
console.log('\n=== packages with "skill" in name ===');
for (const n of names) if (/skill/i.test(n)) console.log(n);
