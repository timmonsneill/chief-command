/**
 * ThinkingDots — three pulsing dots, shown while Chief is processing the
 * deep reply (after the bridge phrase, before the first token streams).
 *
 * Why this exists:
 *   The Pro tier round-trip is 12–14s. Without an indicator the silent gap
 *   between the bridge phrase ("Let me think…") and the streamed answer
 *   feels broken — owner reported "we need a thinking icon like Claude has
 *   so I know that it's thinking."
 *
 * Visual contract:
 *   - 3 dots, staggered pulse (0ms / 200ms / 400ms delay)
 *   - Steel-blue base (primary), amber accent on the middle dot
 *   - Lives inside a soft rounded surface that mirrors the assistant
 *     bubble — same left-aligned position, slightly smaller padding so
 *     it reads as "pre-bubble" rather than a real reply
 *   - Continuous loop; no idle/end state — the parent unmounts it
 *
 * Decoupled from voiceState:
 *   The previous implementation tied dots to `voiceState === 'thinking'`,
 *   which was cleared the moment `tts_start` fired for the bridge phrase.
 *   That left a 12s window with NO indicator while the deep brain was
 *   still processing. Now driven by an independent `thinkingState` so the
 *   dots can persist *under* the bridge-phrase TTS and disappear only
 *   when the first real `token` lands.
 */
export function ThinkingDots() {
  return (
    <div
      className="flex justify-start"
      role="status"
      aria-label="Chief is thinking"
    >
      <div className="bg-surface-raised border border-surface-border rounded-2xl rounded-bl-md px-4 py-3 shadow-card">
        <div className="flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full bg-primary/70 animate-thinking-dot [animation-delay:0ms]" />
          <span className="w-2 h-2 rounded-full bg-accent animate-thinking-dot [animation-delay:200ms]" />
          <span className="w-2 h-2 rounded-full bg-primary/70 animate-thinking-dot [animation-delay:400ms]" />
        </div>
      </div>
    </div>
  )
}

export default ThinkingDots
