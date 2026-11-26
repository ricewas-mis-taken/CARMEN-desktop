// A tiny JSON-lines bridge from the Python live tests to the CarmenLinked test harness (client/test/harness.mjs).
// It creates the throwaway users (SQL-created `ct-<run>-<tag>@carmenlinked.test`, never GoTrue signup), plays the GUIDE
// through the JS adapter (the same code a guide's browser would run), and removes every account it made.
//
//   node ct_bridge.mjs <CARMENLINKED_DIR>      # then one JSON object per line on stdin, one JSON answer per line on stdout
//   {"op":"init"}                              -> {student:{id,email,password}, guide:{id,email}}  (password goes to the test, not to a log)
//   {"op":"guide","method":"POST","path":"/students/<id>/rules","body":{...}} -> {status, body}
//   {"op":"sql","q":"select ..."}              -> rows (SELECT only: reads of rows this run created)
//   {"op":"counts"} / {"op":"cleanup"} / {"op":"quit"}
// Cleanup also runs from the harness's own exit/signal hooks, so a killed test still removes its accounts.
import { createInterface } from 'node:readline';
import { pathToFileURL } from 'node:url';
import path from 'node:path';

const root = process.argv[2];
if (!root) { console.error('usage: ct_bridge.mjs <CARMENLINKED_DIR>'); process.exit(2); }
const H = await import(pathToFileURL(path.join(root, 'client', 'test', 'harness.mjs')).href);
H.ensureEnvProxy();
H.installExitCleanup();

let target, guide, student;
const out = (o) => process.stdout.write(JSON.stringify(o) + '\n');

async function handle(m) {
  switch (m.op) {
    case 'init': {
      target = await H.makeTarget('supabase');
      guide = await target.newUser('CT Guide', { age: 40 });
      const [kid] = await H.createUsers([{ tag: 'kid', name: 'CT Kid', age: 15 }]);
      student = kid;
      return { run: H.RUN_ID, student: { id: kid.id, email: kid.email, password: kid.password }, guide: { id: guide.id, email: guide.email } };
    }
    case 'guide': {
      const r = await guide.api[m.method.toLowerCase() === 'delete' ? 'del' : m.method.toLowerCase()](m.path, m.body);
      return { status: r.status, body: r.body };
    }
    case 'sql': {
      if (!/^\s*select\b/i.test(m.q) || /;\s*\S/.test(m.q)) throw new Error('only a single SELECT is allowed');
      return await H.sql(m.q);
    }
    case 'counts': return await H.projectCounts();
    case 'cleanup': return { left: await H.cleanupTestUsers() };
    default: throw new Error('unknown op ' + m.op);
  }
}

const rl = createInterface({ input: process.stdin });
let chain = Promise.resolve();
rl.on('line', (line) => {
  chain = chain.then(async () => {
    let m;
    try { m = JSON.parse(line); } catch { return out({ error: 'bad json' }); }
    if (m.op === 'quit') { try { await H.cleanupTestUsers(); } catch {} process.exit(0); }
    try { out({ ok: await handle(m) }); } catch (e) { out({ error: String(e && e.message || e) }); }
  });
});
rl.on('close', () => chain.then(async () => { try { await H.cleanupTestUsers(); } catch {} process.exit(0); }));
