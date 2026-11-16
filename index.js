/** PDF layout-preserving translation tools (preset-scoped).
 *
 * The preset `pdf-translate` mounts ONLY these tools plus a read-only fs tool
 * and ask_user.  The agent cannot run shell commands, write files, or read
 * images: prepare -> view -> submit -> finish is the only path to a PDF.
 */
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { readFile, writeFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const execFileP = promisify(execFile)
const here = dirname(fileURLToPath(import.meta.url))
const pyDir = join(here, 'python')

export const name = 'pdf-translate-tools'
export const inject = ['tools']

function out(value) {
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2)
}

async function runPy(python, script, argv, timeoutMs) {
  try {
    const { stdout } = await execFileP(python, [join(pyDir, script), ...argv], {
      maxBuffer: 64 * 1024 * 1024,
      timeout: timeoutMs,
      windowsHide: true,
    })
    const lines = String(stdout).trim().split(/\r?\n/).filter(Boolean)
    try {
      return JSON.parse(lines[lines.length - 1] || '{}')
    } catch {
      return { ok: true, raw: String(stdout).slice(-4000) }
    }
  } catch (err) {
    const stdout = String(err.stdout || '')
    const stderr = String(err.stderr || '')
    const lines = stdout.trim().split(/\r?\n/).filter(Boolean)
    let parsed = null
    try {
      parsed = JSON.parse(lines[lines.length - 1] || '')
    } catch { /* not json */ }
    return parsed || {
      ok: false,
      errors: [String(err.message || err), stderr.slice(-1500)],
    }
  }
}

async function loadJson(path) {
  return JSON.parse(await readFile(path, 'utf8'))
}

async function saveJson(path, value) {
  await writeFile(path, JSON.stringify(value, null, 1), 'utf8')
}

function str(v) { return typeof v === 'string' ? v.trim() : '' }

export function apply(ctx, config = {}) {
  const python = str(config.python) || 'python'
  const timeoutMs = Number(config.timeoutMs) > 0 ? Number(config.timeoutMs) : 600000
  const outText = (value) => [{ type: 'text', text: out(value) }]

  ctx.tools.register({
    name: 'pdf_prepare',
    description:
      'Step 1 of 4 for layout-preserving PDF translation. Analyze the source PDF, ' +
      'detect the layout profile, and write translations_template.json / blocks.json / ' +
      'plan.json under the work directory. Returns counts and paths. Call this before ' +
      'anything else; this mode has no shell or file-write tool, so the template can ' +
      'only be produced here.',
    parameters: {
      type: 'object',
      additionalProperties: false,
      required: ['src'],
      properties: {
        src: { type: 'string', description: 'Absolute path of the source PDF.' },
        workdir: { type: 'string', description: 'Output directory (default: <pdf-stem>_zh_work beside the PDF).' },
        profile: {
          type: 'string',
          enum: ['auto', 'single-col-word', 'two-col-latex'],
          description: 'Layout profile; auto detects it.',
        },
      },
    },
    output: { schema: { type: 'string' }, render: (_a, v) => outText(v) },
    timeoutMs,
    async execute(args) {
      const src = str(args.src)
      if (!src) return out({ ok: false, errors: ['src is required'] })
      const argv = [src]
      if (str(args.workdir)) argv.push(str(args.workdir))
      if (str(args.profile) && str(args.profile) !== 'auto') argv.push('--profile', str(args.profile))
      return out(await runPy(python, 'prepare.py', argv, timeoutMs))
    },
  })

  ctx.tools.register({
    name: 'pdf_view',
    description:
      'Step 2 of 4. Return a batch of translation-template entries (id, page, kind, ' +
      'keep, size, en, zh). `en` is the source text to translate; `zh` is empty until ' +
      'submitted. Use only=all to inspect kept blocks, todo to see untranslated work.',
    parameters: {
      type: 'object',
      additionalProperties: false,
      required: ['workdir'],
      properties: {
        workdir: { type: 'string' },
        offset: { type: 'number', description: 'Start index (default 0).' },
        limit: { type: 'number', description: 'Batch size 1-60 (default 25).' },
        only: { type: 'string', enum: ['todo', 'all', 'done'], description: 'Filter (default todo).' },
        maxChars: { type: 'number', description: 'Cap on returned en characters (default 12000).' },
      },
    },
    output: { schema: { type: 'string' }, render: (_a, v) => outText(v) },
    timeoutMs: 120000,
    async execute(args) {
      const wd = resolve(str(args.workdir))
      if (!wd) return out({ ok: false, errors: ['workdir is required'] })
      let tpl
      try {
        tpl = await loadJson(join(wd, 'translations_template.json'))
      } catch (err) {
        return out({ ok: false, errors: ['translations_template.json not found; call pdf_prepare first'] })
      }
      const entries = Array.isArray(tpl.entries) ? tpl.entries : []
      const filter = str(args.only) || 'todo'
      const todo = entries.filter((e) => !e.keep)
      const done = todo.filter((e) => str(e.zh))
      const pool = filter === 'all' ? entries : filter === 'done' ? done : todo.filter((e) => !str(e.zh))
      const offset = Math.max(0, Math.floor(Number(args.offset) || 0))
      const limit = Math.min(60, Math.max(1, Math.floor(Number(args.limit) || 25)))
      const maxChars = Math.min(40000, Math.max(2000, Math.floor(Number(args.maxChars) || 12000)))
      const slice = []
      let used = 0
      for (const e of pool.slice(offset, offset + limit)) {
        const en = String(e.en || '')
        const cut = Math.min(en.length, Math.max(200, maxChars - used))
        slice.push({ id: e.id, page: e.page + 1, kind: e.kind, keep: Boolean(e.keep),
          size: e.size, en: en.slice(0, cut), truncated: cut < en.length,
          enLength: en.length, zh: e.zh || '', label: e.label || '' })
        used += cut
        if (used >= maxChars) break
      }
      return out({
        ok: true, workdir: wd, profile: tpl.meta?.profile,
        total: entries.length, translatable: todo.length,
        translated: done.length, remaining: todo.length - done.length,
        filter, offset, count: slice.length, entries: slice,
      })
    },
  })

  ctx.tools.register({
    name: 'pdf_submit',
    description:
      'Step 3 of 4. Merge translations into translations_template.json in batches. ' +
      'Each item is {id, zh?, keep?, label?, bbox_override?}. Unknown ids are rejected. ' +
      'For kind=formula_mixed, keep stays true unless you set keep=false and supply a ' +
      'faithful translation that reproduces the formula. This tool never renders a PDF.',
    parameters: {
      type: 'object',
      additionalProperties: false,
      required: ['workdir', 'entries'],
      properties: {
        workdir: { type: 'string' },
        entries: {
          type: 'array',
          items: {
            type: 'object',
            additionalProperties: false,
            required: ['id'],
            properties: {
              id: { type: 'string' },
              zh: { type: 'string' },
              keep: { type: 'boolean' },
              label: { type: 'string' },
              bbox_override: {
                type: 'array',
                items: { type: 'number' },
                description: 'Optional [x0,y0,x1,y1] drawing box.',
              },
            },
          },
        },
      },
    },
    output: { schema: { type: 'string' }, render: (_a, v) => outText(v) },
    timeoutMs: 120000,
    async execute(args) {
      const wd = resolve(str(args.workdir))
      if (!wd) return out({ ok: false, errors: ['workdir is required'] })
      const items = Array.isArray(args.entries) ? args.entries : []
      if (!items.length) return out({ ok: false, errors: ['entries is empty'] })
      let tpl
      try {
        tpl = await loadJson(join(wd, 'translations_template.json'))
      } catch {
        return out({ ok: false, errors: ['translations_template.json not found; call pdf_prepare first'] })
      }
      const byId = new Map(tpl.entries.map((e) => [e.id, e]))
      const errors = []
      let applied = 0
      for (const it of items) {
        const e = byId.get(str(it.id))
        if (!e) { errors.push('unknown id: ' + str(it.id)); continue }
        if (typeof it.zh === 'string') e.zh = it.zh
        if (typeof it.keep === 'boolean') e.keep = it.keep
        if (typeof it.label === 'string') e.label = it.label
        if (Array.isArray(it.bbox_override) && it.bbox_override.length === 4
          && it.bbox_override.every((v) => typeof v === 'number')) {
          e.bbox_override = it.bbox_override.map((v) => Math.round(v * 10) / 10)
        }
        applied += 1
      }
      if (errors.length) return out({ ok: false, applied, errors })
      await saveJson(join(wd, 'translations_template.json'), tpl)
      const todo = tpl.entries.filter((e) => !e.keep)
      const done = todo.filter((e) => str(e.zh))
      return out({ ok: true, applied, translatable: todo.length,
        translated: done.length, remaining: todo.length - done.length })
    },
  })

  ctx.tools.register({
    name: 'pdf_finish',
    description:
      'Step 4 of 4. Validate the filled template and render the Chinese PDF. ' +
      'Validation failures (missing zh, bad size/bbox, covering a display equation, ' +
      'PUA or missing glyphs, odd length ratio) return ok=false with errors and write ' +
      'NO PDF. This is the only way to obtain the output file.',
    parameters: {
      type: 'object',
      additionalProperties: false,
      required: ['workdir'],
      properties: {
        workdir: { type: 'string' },
        out: { type: 'string', description: 'Output PDF path (default <src>_中文全文翻译.pdf).' },
        allow_partial: { type: 'boolean', description: 'Render even if some translatable blocks are empty.' },
      },
    },
    output: { schema: { type: 'string' }, render: (_a, v) => outText(v) },
    timeoutMs,
    async execute(args) {
      const wd = resolve(str(args.workdir))
      if (!wd) return out({ ok: false, errors: ['workdir is required'] })
      const argv = [wd]
      if (str(args.out)) argv.push('--out', str(args.out))
      if (args.allow_partial === true) argv.push('--allow-partial')
      return out(await runPy(python, 'finish.py', argv, timeoutMs))
    },
  })

  ctx.tools.register({
    name: 'pdf_profile_propose',
    description:
      'Optional after pdf_finish. Read run_record.json and suggest profile adjustments ' +
      '(kept formula_mixed ratio, QC failures, page count). Suggestions are advisory; ' +
      'this tool never edits the pipeline or the profiles.',
    parameters: {
      type: 'object',
      additionalProperties: false,
      required: ['workdir'],
      properties: { workdir: { type: 'string' } },
    },
    output: { schema: { type: 'string' }, render: (_a, v) => outText(v) },
    timeoutMs: 120000,
    async execute(args) {
      const wd = resolve(str(args.workdir))
      if (!wd) return out({ ok: false, errors: ['workdir is required'] })
      return out(await runPy(python, 'profile_propose.py', [wd], timeoutMs))
    },
  })

  ctx.tools.register({
    name: 'pdf_evolve',
    description:
      'Optional after a successful pdf_finish. Replays every stored case (cases/*/src.pdf) ' +
      'against candidate numeric parameters and updates profiles/<id>.json only when the ' +
      'candidate raises automatic coverage on all cases and keeps the display-equation ' +
      'count stable. It never writes code. Run it once per finished translation; then ' +
      'report what changed or why nothing changed.',
    parameters: {
      type: 'object',
      additionalProperties: false,
      properties: {
        workdir: { type: 'string', description: 'Optional; used only to locate the profile.' },
        dry_run: { type: 'boolean', description: 'Report candidates without writing the profile.' },
      },
    },
    output: { schema: { type: 'string' }, render: (_a, v) => outText(v) },
    timeoutMs: 900000,
    async execute(args) {
      const argv = []
      if (str(args.workdir)) argv.push(str(args.workdir))
      if (args.dry_run === true) argv.push('--dry-run')
      return out(await runPy(python, 'evolve.py', argv, 900000))
    },
  })
}
