// Quick syntax check for edited JS/HTML-related files
const fs = require('fs');
for (const f of ['Web/app.js', 'Web/api_client.js', 'Web/index.html']) {
    try {
        if (f.endsWith('.js')) {
            new Function(fs.readFileSync(f, 'utf8'));
            console.log(f, 'OK');
        } else {
            const s = fs.readFileSync(f, 'utf8');
            console.log(f, 'bytes:', s.length);
        }
    } catch (e) {
        console.log(f, 'SYNTAX ERROR:', e.message);
    }
}