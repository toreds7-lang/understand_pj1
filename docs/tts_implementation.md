# TTS Implementation: Web Speech API

## Why Chrome's Web Speech API over Qwen3-TTS

### The decision

This project is a **single-user, local FastAPI app** (`serve.py` on `127.0.0.1`) with a browser frontend. The TTS need is simple: read selected text or AI answers aloud during a paper reading session. Given that context:

| Criterion | Web Speech API | Qwen3-TTS |
|---|---|---|
| Setup cost | Zero — already in Chrome | GPU + model weights (~several GB) or DashScope API key |
| Cost at runtime | Free | DashScope API charges per character, or electricity for local GPU |
| Latency to first word | ~80 ms | 1–3 s round-trip (API) or inference time (local) |
| Works with streaming | Yes — speak sentence-by-sentence as tokens arrive | No — full text needed before synthesis |
| Backend changes needed | None | New `/api/tts` endpoint, audio byte streaming, frontend audio queue |
| Voice quality | Serviceable (OS voices) | Natural, expressive, multilingual |
| Offline | Yes | No (API) or requires local GPU (self-hosted) |

Qwen3-TTS produces genuinely better audio. The trade-off is substantial engineering overhead and ongoing cost for a use case (private study tool) where "good enough" voice quality is fully acceptable. The Web Speech API delivers the feature in ~150 lines of frontend-only JavaScript with no server changes, no API key, and no latency.

The upgrade path is preserved: the entire TTS engine is isolated in one `TTS` module in `viewer.html`. Replacing `speakNext()` with a call to `/api/tts` is a localized change if better voice quality becomes a priority.

---

## Implementation

All code lives in `viewer.html`. No backend changes were made.

### Module structure

```js
const TTS = (function () {
  const synth = window.speechSynthesis;
  // ...
  return { toggle, speakSelection, stop, setBtnState, supported };
})();
```

An IIFE keeps all state private. The public surface is minimal:

| Export | Purpose |
|---|---|
| `toggle(markdown, btn)` | Play/stop tied to a specific button element |
| `speakSelection(markdown)` | Play/stop with no button (used by Ctrl+S) |
| `stop()` | Hard stop, clears queue |
| `setBtnState(btn, playing)` | Update a button's label and CSS class |
| `supported` | `false` if the browser has no speech synthesis |

---

### Step 1 — Strip markdown to plain text

Before any text is spoken, markdown syntax is removed so the synthesizer doesn't read `**`, `#`, backticks, LaTeX, etc. aloud.

```js
function stripMarkdown(md) {
  let t = md || "";
  t = t.replace(/```[\s\S]*?```/g, ". ");          // fenced code → pause
  t = t.replace(/~~~[\s\S]*?~~~/g, ". ");
  t = t.replace(/`([^`]+)`/g, "$1");                // inline code → bare text
  t = t.replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1");   // images → alt text
  t = t.replace(/\[([^\]]+)\]\([^)]*\)/g, "$1");    // links → link text
  t = t.replace(/^\s{0,3}#{1,6}\s+/gm, "");         // headings
  t = t.replace(/^\s{0,3}>\s?/gm, "");              // blockquotes
  t = t.replace(/^\s{0,3}([-*+]|\d+[.)])\s+/gm, "");// list markers
  t = t.replace(/^\s*\|?[\s:|-]*\|[\s:|-]*\|?\s*$/gm, ""); // table dividers
  t = t.replace(/\|/g, " ");                        // table cell pipes
  t = t.replace(/^\s*([-*_]\s*){3,}$/gm, ". ");      // horizontal rules
  t = t.replace(/(\*\*|__)(.*?)\1/g, "$2");          // bold → text
  t = t.replace(/(\*|_)(.*?)\1/g, "$2");             // italic → text
  t = t.replace(/~~(.*?)~~/g, "$2");                 // strikethrough
  t = t.replace(/\$\$[\s\S]*?\$\$/g, " equation. ");  // block math
  t = t.replace(/\$[^$\n]+\$/g, " expression ");      // inline math
  t = t.replace(/\n{2,}/g, ". ");                    // paragraph breaks → audible pause
  t = t.replace(/\s+/g, " ").trim();
  return t;
}
```

---

### Step 2 — Split into sentence-sized chunks

Chrome's `speechSynthesis` **silently truncates utterances** that exceed roughly 15 seconds of audio. A single long AI answer would cut off mid-way without chunking. The solution is to split on sentence boundaries and speak each chunk as a separate `SpeechSynthesisUtterance`.

Chunks are also hard-wrapped at 220 characters to guard against unusually long sentences.

```js
function splitSentences(text) {
  const MAX = 220;
  const sentences = text.match(/[^.!?。！？]+[.!?。！？]+|\S[^.!?。！？]*$/g) || [];
  const chunks = [];
  sentences.forEach(function (s) {
    s = s.trim();
    while (s.length > MAX) {
      let cut = s.lastIndexOf(" ", MAX);
      if (cut < 40) cut = MAX;
      chunks.push(s.slice(0, cut).trim());
      s = s.slice(cut).trim();
    }
    if (s) chunks.push(s);
  });
  return chunks;
}
```

---

### Step 3 — Engine warm-up (Chrome cold-start bug)

Chrome drops the **first utterance ever spoken after page load**. On a cold engine, `synth.speak()` is a no-op for the very first call. This caused the first press of 🔊 Play to start mid-paragraph (the first sentence was silently discarded).

Fix: push one **silent utterance** (a space at `volume = 0`) the first time `play()` is called. This absorbs the dropped-first-utterance behavior, and all subsequent real chunks play in full.

```js
let primed = false;

function prime() {
  if (primed) return;
  primed = true;
  const u = new SpeechSynthesisUtterance(" ");
  u.volume = 0;
  synth.speak(u);
}
```

Voices are also pre-fetched eagerly on module init (Chrome loads them asynchronously):

```js
synth.getVoices();
synth.addEventListener("voiceschanged", () => synth.getVoices());
```

---

### Step 4 — Safe cancel + deferred first speak

A second Chrome quirk: calling `synth.cancel()` then `synth.speak()` **in the same tick** causes the first `speak()` to be ignored, making playback start mid-paragraph on subsequent presses.

Two guards:
1. Only call `cancel()` when something is actually playing (`synth.speaking || synth.pending`). Cancelling an idle engine triggers the bug with no upside.
2. Defer the first real `speakNext()` by 80 ms via `setTimeout`. This gives the engine one event-loop tick to process the cancel before receiving new utterances.

```js
function play(markdown, btn) {
  const chunks = splitSentences(stripMarkdown(markdown));
  if (!chunks.length) return;
  setBtnState(activeBtn, false);
  if (synth.speaking || synth.pending) synth.cancel();
  prime();
  queue = chunks;
  speaking = true;
  activeBtn = btn;
  setBtnState(btn, true);
  setTimeout(speakNext, 80);   // defer: avoid same-tick cancel→speak drop
}
```

---

### Step 5 — Language detection

The viewer supports mixed Korean/English content (paper panel, chat). Chunks are checked for Hangul characters and routed to the appropriate voice:

```js
u.lang = /[ㄱ-힝]/.test(text) ? "ko-KR" : "en-US";
```

---

### Playback chain

```js
function speakNext() {
  if (!queue.length) { finish(); return; }
  const text = queue.shift();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = /[ㄱ-힝]/.test(text) ? "ko-KR" : "en-US";
  u.rate = 1.0;
  u.onend = speakNext;    // chain to next chunk on completion
  u.onerror = speakNext;  // skip silently on error, don't halt the queue
  synth.speak(u);
}
```

Each utterance fires `speakNext` on `onend`, creating a self-chaining queue. Errors skip the failing chunk rather than halting the whole answer.

---

### Integration points in viewer.html

#### Chat + figure explanation answers — 🔊 Play button

Added inside `addMsgActions()`, which runs after every completed assistant message:

```js
const ttsBtn = document.createElement('button');
ttsBtn.className = 'msg-action-btn tts-btn';
TTS.setBtnState(ttsBtn, false);                    // "🔊 Play"
ttsBtn.addEventListener('click', () => TTS.toggle(rawMarkdown, ttsBtn));
bar.appendChild(ttsBtn);
```

The raw markdown string (before HTML rendering) is passed so `stripMarkdown` has clean input.

#### Define / explain popup — 🔊 Play button

After the popup stream finishes:

```js
const popTts = document.getElementById("pop-tts");
TTS.setBtnState(popTts, false);
popTts.style.display = "";
popTts.onclick = () => TTS.toggle(acc, popTts);
```

The button is hidden while the answer is streaming and shown only when complete. It resets and stops speech each time a new popup opens.

#### Any selected text — Ctrl+S

```js
if (e.key === "s" || e.key === "S") {
  e.preventDefault();                     // suppress browser Save dialog
  const sel = window.getSelection().toString().trim();
  const text = sel || lastSelection.text; // fall back to last mouse selection
  if (!text) { TTS.stop(); return; }
  TTS.speakSelection(text);
}
```

Works on any text in the viewer: PDF text layer, extracted markdown panel, chat log, popup body. Press Ctrl+S again to stop.

---

### Known Chrome quirks worked around

| Bug | Symptom | Fix applied |
|---|---|---|
| First utterance dropped on cold engine | First press plays from 2nd sentence | Silent warm-up utterance (`prime()`) |
| `cancel()` + `speak()` in same tick | Subsequent presses play from 2nd sentence | 80 ms `setTimeout` defer before first `speakNext()` |
| Long utterances silently truncated (~15 s) | Long answers cut off mid-way | Sentence-level chunking (max 220 chars) |
| Cancelling idle engine drops next speak | Stop then Play loses first sentence | Guard: only `cancel()` when `synth.speaking || synth.pending` |
