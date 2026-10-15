const fs = require('fs');
const vm = require('vm');
// usage: node check_syntax.js [path]   (default: ui/app.html, the wizard)
const file = process.argv[2] || 'ui/app.html';
const html = fs.readFileSync(file, 'utf-8');
const re = /<script>([\s\S]*?)<\/script>/g;
let m, n = 0, bad = 0;
while ((m = re.exec(html)) !== null) {
  n++;
  try {
    new vm.Script(m[1]);
  } catch (e) {
    bad++;
    console.log(`script #${n}: ${e.message}`);
    console.log(e.stack);
  }
}
if (!n) console.log("No script tag found");
else if (!bad) console.log(`No syntax errors! (${file}, ${n} script block${n > 1 ? 's' : ''})`);
process.exit(bad ? 1 : 0);
