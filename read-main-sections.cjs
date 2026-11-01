const fs = require('fs');
const p = 'D:\\Program Files (x86)\\DeepSeek\\resources\\app.asar\\lib\\main.js';
const src = fs.readFileSync(p, 'utf8');
const lines = src.split('\n');
function show(from, to) {
  console.log(`\n===== lines ${from}-${to} =====`);
  for (let i = from; i <= to; i++) console.log(String(i).padStart(5) + ': ' + lines[i - 1]);
}
show(3720, 3940);
show(11800, 11860);
show(12740, 12790);
