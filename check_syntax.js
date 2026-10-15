const fs = require('fs');
const html = fs.readFileSync('ui/index.html', 'utf-8');
const scriptMatch = html.match(/<script>([\s\S]*?)<\/script>/);
if (scriptMatch) {
  const code = scriptMatch[1];
  const vm = require('vm');
  try {
    new vm.Script(code);
    console.log("No syntax errors!");
  } catch (e) {
    console.log(e.message);
    console.log(e.stack);
  }
} else {
  console.log("No script tag found");
}
