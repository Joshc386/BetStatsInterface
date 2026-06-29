// A "Last N" number field that lets you type freely (including clearing it),
// rather than clamping to >=1 on every keystroke. Blank or invalid input falls
// back to 5; the committed value is always a clean integer in [1, max].

import { useState } from 'react'

const cls =
  'w-20 rounded-md border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm text-slate-100 outline-none focus:border-sky-600'

/** Parse the raw text into a usable window size. Blank / non-numeric / < 1 all
 * fall back to 5; anything above max is clamped to max. */
export function coerceN(text: string, max: number): number {
  const v = parseInt(text, 10)
  if (!Number.isFinite(v) || v < 1) return 5
  return Math.min(max, v)
}

export function LastNInput({
  n,
  setN,
  max,
}: {
  n: number
  setN: (v: number) => void
  max: number
}) {
  // Local text so the field can be empty / mid-edit; n stays a valid number.
  const [text, setText] = useState(String(n))
  return (
    <input
      type="number"
      min={1}
      max={max}
      value={text}
      className={cls}
      onChange={(e) => {
        setText(e.target.value)
        setN(coerceN(e.target.value, max))
      }}
      onBlur={() => setText(String(coerceN(text, max)))} // snap blank/invalid to 5 on exit
    />
  )
}
