import {copyFileSync, readFileSync} from 'node:fs';
copyFileSync('node_modules/htmx.org/dist/htmx.min.js', 'app/web/static/htmx.min.js');
copyFileSync('node_modules/htmx.org/LICENSE', 'app/web/static/HTMX-LICENSE.txt');
copyFileSync('app/web/static/src/app.js', 'app/web/static/app.js');
for (const name of ['app.css', 'htmx.min.js', 'app.js']) {
  if (!readFileSync(`app/web/static/${name}`).length) throw new Error(`Empty asset: ${name}`);
}
