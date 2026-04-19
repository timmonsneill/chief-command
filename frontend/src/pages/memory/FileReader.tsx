import { useState, useEffect, useMemo, useRef } from 'react'
import { ArrowLeft, Save, Eye, Pencil, Check } from 'lucide-react'

interface FileReaderProps {
  title: string
  subtitle?: string
  filename: string
  initialContent: string
  updatedAt?: string | null
  onBack: () => void
  onSave: (filename: string, content: string) => Promise<void>
}

function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    return d.toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    })
  } catch {
    return ''
  }
}

/**
 * Lightweight markdown-ish renderer. react-markdown isn't installed and Chief
 * said not to add deps in this pass, so this handles the bits that matter for
 * memory files (headings, bullet lists, code fences, inline emphasis) and
 * falls back to whitespace-preserving paragraphs for everything else. Good
 * enough to make reading feel like reading, not debugging.
 */
function RenderedMarkdown({ text }: { text: string }) {
  const blocks = useMemo(() => parseBlocks(text), [text])

  return (
    <div className="prose-chief text-sm text-white/75 leading-relaxed space-y-3 font-sans">
      {blocks.map((block, i) => {
        if (block.kind === 'heading') {
          const sizeClass =
            block.level === 1
              ? 'text-lg font-semibold text-white'
              : block.level === 2
              ? 'text-base font-semibold text-white'
              : 'text-sm font-semibold text-white/90'
          return (
            <h3 key={i} className={`${sizeClass} mt-4 first:mt-0`}>
              {renderInline(block.text)}
            </h3>
          )
        }
        if (block.kind === 'code') {
          return (
            <pre
              key={i}
              className="bg-surface border border-surface-border rounded-lg p-3 text-xs text-white/70 font-mono leading-relaxed overflow-x-auto"
            >
              <code>{block.text}</code>
            </pre>
          )
        }
        if (block.kind === 'list') {
          return (
            <ul key={i} className="space-y-1.5 pl-1">
              {block.items.map((item, j) => (
                <li key={j} className="flex gap-2 text-sm text-white/75 leading-relaxed">
                  <span className="text-chief-light shrink-0 select-none">•</span>
                  <span className="flex-1">{renderInline(item)}</span>
                </li>
              ))}
            </ul>
          )
        }
        if (block.kind === 'hr') {
          return <hr key={i} className="border-surface-border my-4" />
        }
        if (block.kind === 'frontmatter') {
          return (
            <div
              key={i}
              className="text-[11px] text-white/40 font-mono bg-surface-overlay/50 border border-surface-border rounded-lg px-3 py-2 whitespace-pre-wrap"
            >
              {block.text}
            </div>
          )
        }
        // paragraph
        return (
          <p key={i} className="text-sm text-white/75 leading-relaxed whitespace-pre-wrap">
            {renderInline(block.text)}
          </p>
        )
      })}
    </div>
  )
}

type Block =
  | { kind: 'heading'; level: 1 | 2 | 3; text: string }
  | { kind: 'paragraph'; text: string }
  | { kind: 'code'; text: string }
  | { kind: 'list'; items: string[] }
  | { kind: 'hr' }
  | { kind: 'frontmatter'; text: string }

function parseBlocks(src: string): Block[] {
  const blocks: Block[] = []
  const lines = src.replace(/\r\n/g, '\n').split('\n')
  let i = 0

  // frontmatter block at top of file
  if (lines[0]?.trim() === '---') {
    const end = lines.findIndex((l, idx) => idx > 0 && l.trim() === '---')
    if (end > 0) {
      blocks.push({ kind: 'frontmatter', text: lines.slice(1, end).join('\n') })
      i = end + 1
    }
  }

  while (i < lines.length) {
    const line = lines[i]

    if (line.trim() === '') {
      i++
      continue
    }

    if (line.trim() === '---') {
      blocks.push({ kind: 'hr' })
      i++
      continue
    }

    // code fence
    if (line.trim().startsWith('```')) {
      const codeLines: string[] = []
      i++
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        codeLines.push(lines[i])
        i++
      }
      i++ // consume closing fence
      blocks.push({ kind: 'code', text: codeLines.join('\n') })
      continue
    }

    // heading
    const h = /^(#{1,6})\s+(.*)$/.exec(line)
    if (h) {
      const level = Math.min(3, h[1].length) as 1 | 2 | 3
      blocks.push({ kind: 'heading', level, text: h[2] })
      i++
      continue
    }

    // list
    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ''))
        i++
      }
      blocks.push({ kind: 'list', items })
      continue
    }

    // paragraph (gather consecutive non-empty non-special lines)
    const paraLines: string[] = [line]
    i++
    while (
      i < lines.length &&
      lines[i].trim() !== '' &&
      !/^(#{1,6})\s+/.test(lines[i]) &&
      !/^\s*[-*]\s+/.test(lines[i]) &&
      !lines[i].trim().startsWith('```') &&
      lines[i].trim() !== '---'
    ) {
      paraLines.push(lines[i])
      i++
    }
    blocks.push({ kind: 'paragraph', text: paraLines.join('\n') })
  }

  return blocks
}

function renderInline(text: string) {
  // Handle **bold**, `code`, and [link](url) lightly. Keep it simple.
  const parts: (string | JSX.Element)[] = []
  const regex = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g
  let lastIdx = 0
  let match: RegExpExecArray | null
  let key = 0
  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIdx) {
      parts.push(text.slice(lastIdx, match.index))
    }
    const token = match[0]
    if (token.startsWith('**')) {
      parts.push(
        <strong key={key++} className="font-semibold text-white">
          {token.slice(2, -2)}
        </strong>
      )
    } else if (token.startsWith('`')) {
      parts.push(
        <code
          key={key++}
          className="font-mono text-[0.85em] bg-surface-overlay text-chief-light px-1 py-0.5 rounded"
        >
          {token.slice(1, -1)}
        </code>
      )
    } else if (token.startsWith('[')) {
      const linkMatch = /\[([^\]]+)\]\(([^)]+)\)/.exec(token)
      if (linkMatch) {
        parts.push(
          <span key={key++} className="text-chief-light underline decoration-dotted">
            {linkMatch[1]}
          </span>
        )
      } else {
        parts.push(token)
      }
    }
    lastIdx = match.index + token.length
  }
  if (lastIdx < text.length) parts.push(text.slice(lastIdx))
  return parts
}

export default function FileReader({
  title,
  subtitle,
  filename,
  initialContent,
  updatedAt,
  onBack,
  onSave,
}: FileReaderProps) {
  const [mode, setMode] = useState<'read' | 'edit'>('read')
  const [content, setContent] = useState(initialContent)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [saveSuccess, setSaveSuccess] = useState(false)

  // Re-sync local editor state when the drilled-into file changes (or when a
  // save refreshes parent's initialContent). Without this, React reuses the
  // FileReader instance across back-then-open-different-file and the editor
  // keeps the previous file's content.
  useEffect(() => {
    setContent(initialContent)
    setSaveError('')
    setSaveSuccess(false)
  }, [filename, initialContent])

  const successTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => {
    if (successTimerRef.current) clearTimeout(successTimerRef.current)
  }, [])

  const dirty = content !== initialContent

  async function handleSave() {
    setSaving(true)
    setSaveError('')
    setSaveSuccess(false)
    try {
      await onSave(filename, content)
      setSaveSuccess(true)
      if (successTimerRef.current) clearTimeout(successTimerRef.current)
      successTimerRef.current = setTimeout(() => setSaveSuccess(false), 1800)
    } catch {
      setSaveError('Save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="h-full flex flex-col">
      {/* Sub-header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-surface-border bg-surface/80 backdrop-blur-sm shrink-0">
        <button
          onClick={onBack}
          className="w-8 h-8 flex items-center justify-center rounded-lg text-white/50 active:text-white active:bg-surface-overlay transition-colors"
          aria-label="Back"
        >
          <ArrowLeft size={16} />
        </button>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-white truncate">{title}</p>
          {subtitle && (
            <p className="text-[11px] text-white/40 truncate">{subtitle}</p>
          )}
        </div>
        <div className="flex items-center gap-1 bg-surface-raised border border-surface-border rounded-lg p-0.5 shrink-0">
          <button
            onClick={() => setMode('read')}
            className={`flex items-center gap-1 px-2 py-1 rounded text-[11px] font-medium transition-colors ${
              mode === 'read'
                ? 'bg-chief text-white'
                : 'text-white/50 active:text-white'
            }`}
          >
            <Eye size={12} />
            Read
          </button>
          <button
            onClick={() => setMode('edit')}
            className={`flex items-center gap-1 px-2 py-1 rounded text-[11px] font-medium transition-colors ${
              mode === 'edit'
                ? 'bg-chief text-white'
                : 'text-white/50 active:text-white'
            }`}
          >
            <Pencil size={12} />
            Edit
          </button>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto">
        {mode === 'read' ? (
          <div className="px-4 py-4 pb-24">
            {content.trim() === '' ? (
              <p className="text-white/30 text-sm italic">Empty file.</p>
            ) : (
              <RenderedMarkdown text={content} />
            )}
            {updatedAt && (
              <p className="text-[11px] text-white/25 mt-6 pt-3 border-t border-surface-border">
                Last updated {formatTimestamp(updatedAt)}
              </p>
            )}
          </div>
        ) : (
          <div className="h-full flex flex-col px-4 py-3 gap-2">
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              className="flex-1 min-h-[320px] w-full bg-surface border border-surface-border rounded-lg p-3 text-sm text-white/80 font-mono leading-relaxed resize-none focus:outline-none focus:border-chief/50 placeholder-white/20"
              spellCheck={false}
              placeholder="Empty"
            />
            {saveError && <p className="text-xs text-status-offline">{saveError}</p>}
          </div>
        )}
      </div>

      {/* Sticky save bar (edit mode only) */}
      {mode === 'edit' && (
        <div className="shrink-0 border-t border-surface-border bg-surface/90 backdrop-blur-sm px-4 py-3 flex items-center gap-3">
          <p className="text-[11px] text-white/40 flex-1 truncate">
            {dirty ? 'Unsaved changes' : 'Up to date'}
          </p>
          <button
            onClick={handleSave}
            disabled={saving || !dirty}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-chief text-white text-xs font-medium transition-opacity disabled:opacity-30 active:opacity-80"
          >
            {saveSuccess ? <Check size={13} /> : <Save size={13} />}
            {saving ? 'Saving...' : saveSuccess ? 'Saved' : 'Save'}
          </button>
        </div>
      )}
    </div>
  )
}
