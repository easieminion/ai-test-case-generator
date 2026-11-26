// Custom node:test reporter. Writes one JSON file (QA_RESULTS_PATH) with
// per-test outcomes and V8 line/branch/function coverage.
import { writeFileSync } from 'node:fs';

export default async function* reporter(source) {
  const out = { runner: `node ${process.version} (node:test)`, tests: [], coverage: null };
  for await (const ev of source) {
    if (ev.type === 'test:pass' || ev.type === 'test:fail') {
      const d = ev.data;
      if (d.nesting !== 0) continue;           // only top-level tests
      const err = d.details && d.details.error;
      const code = err && err.cause && err.cause.code;
      let status = 'passed';
      let message = '';
      if (ev.type === 'test:fail') {
        status = code === 'ERR_ASSERTION' ? 'failed' : 'error';
        const cause = err && err.cause;
        message = String((cause && cause.message) || (err && err.message) || 'test failed');
        if (cause && cause.stack) {
          const m = String(cause.stack).match(/\(([^()]*solution[^()]*:\d+:\d+)\)/);
          if (m) message += `  [${m[1].split('/').pop()}]`;
        }
      }
      yield `${status === 'passed' ? 'PASS' : status.toUpperCase()}  ${d.name} (${(d.details.duration_ms || 0).toFixed(2)} ms)\n`;
      if (message) yield `      ${message.split('\n').filter(Boolean).join('\n      ')}\n`;
      out.tests.push({
        name: d.name,
        status,
        duration_ms: Math.round((d.details.duration_ms || 0) * 1000) / 1000,
        message: message.slice(0, 1500),
      });
    } else if (ev.type === 'test:coverage') {
      out.coverage = ev.data.summary;
      const t = ev.data.summary.totals;
      yield `coverage: ${t.coveredLineCount}/${t.totalLineCount} lines (raw V8 count incl. blank/comment lines)\n`;
    }
  }
  writeFileSync(process.env.QA_RESULTS_PATH, JSON.stringify(out));
}
