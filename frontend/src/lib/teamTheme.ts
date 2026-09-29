import type { CSSProperties } from 'react'

/** Team theme — each club's page wears its colours.
 *
 * The colours are the club's IDENTITY colours taken from its home kit, not the
 * literal shirt: a white-shirted club (Spurs, Leeds, Fulham…) would otherwise
 * theme a white page white, so its trim carries the theme instead. Hand-picked,
 * keyed by our canonical team id, for the English league clubs only — every
 * other team (foreign clubs, nations, non-league cup opponents) wears
 * DEFAULT_KIT, the neutral pitch theme index.css already paints.
 *
 * Readability is derived, never hand-tuned per club: whatever the kit, a fill
 * gets whichever of white/ink text reads on it, and text on the page uses the
 * kit colour that reads best there — darkened just enough if none does (Norwich
 * yellow/green, Coventry sky). teamTheme.test.ts holds every club to WCAG AA.
 */

export type Pattern = 'stripes' | 'hoops' | 'halves'

export interface Kit {
  shirt: string
  trim: string // collar + outline, and the colour of the stripes/hoops/half
  pattern?: Pattern
}

export interface Theme {
  kit: Kit
  accent: string // fills: hero band, selected toggle
  onAccent: string // text on an accent fill
  accentInk: string // accent as text/lines on the page
}

// The surfaces text is checked against. Must match index.css.
export const PAGE = '#f5f6f1'
export const INK = '#16202b'
export const WHITE = '#ffffff'
export const AA = 4.5

const W = WHITE
const B = '#111111'
const kit = (shirt: string, trim: string, pattern?: Pattern): Kit => ({ shirt, trim, pattern })

export const DEFAULT_KIT = kit(W, '#2d6a45')

/** What a club changes into when its colours clash with its opponent's and
 * its own other colour is white or clashes too: the plain dark away kit, else
 * (against a dark home side) the classic bright yellow one. */
const CHANGE_KITS = ['#2b2f36', '#F2C230']

export const KITS: Record<number, Kit> = {
  4: kit('#B6121B', W), // Accrington
  3: kit('#1B3F8B', '#F5D130'), // AFC Wimbledon
  5: kit('#EF0107', W), // Arsenal
  6: kit('#670E36', '#95BFE5'), // Aston Villa
  7: kit('#F7A823', B), // Barnet
  8: kit('#D71920', W), // Barnsley
  9: kit('#1E4FA3', W), // Barrow
  10: kit('#123C8C', W), // Birmingham
  11: kit('#009EE0', W, 'halves'), // Blackburn
  12: kit('#F68712', W), // Blackpool
  13: kit(W, '#263C7E'), // Bolton
  14: kit('#DA291C', B, 'stripes'), // Bournemouth
  15: kit('#7A1631', '#F9B21B', 'stripes'), // Bradford
  16: kit('#E30613', W, 'stripes'), // Brentford
  17: kit('#0057B8', W, 'stripes'), // Brighton
  18: kit('#E21A23', W), // Bristol City
  19: kit('#1D4F91', W), // Bristol Rvs
  20: kit(W, B), // Bromley
  21: kit('#6C1D45', '#99D6EA'), // Burnley
  22: kit('#FDE500', B), // Burton
  23: kit('#F7A500', B), // Cambridge
  24: kit('#0070B5', W), // Cardiff
  25: kit('#1C4E9C', W), // Carlisle
  26: kit('#D4021D', W), // Charlton
  27: kit('#034694', W), // Chelsea
  28: kit('#D5121E', W), // Cheltenham
  29: kit('#0A4595', W), // Chesterfield
  30: kit('#0B4EA2', W, 'stripes'), // Colchester
  31: kit('#59B6E6', W), // Coventry
  32: kit('#C8102E', W), // Crawley Town
  33: kit('#C8102E', W), // Crewe
  34: kit('#1B458F', '#C4122E', 'stripes'), // Crystal Palace
  35: kit(W, B), // Derby
  36: kit('#DA291C', W, 'hoops'), // Doncaster
  37: kit('#003399', W), // Everton
  38: kit('#D71920', W, 'stripes'), // Exeter
  39: kit('#E0001B', W), // Fleetwood Town
  40: kit('#6ABD45', B), // Forest Green
  41: kit(W, B), // Fulham
  42: kit('#1E3D8F', W), // Gillingham
  43: kit(B, W, 'stripes'), // Grimsby
  44: kit('#FFD700', B), // Harrogate
  103: kit('#003F87', W, 'stripes'), // Hartlepool
  45: kit('#0E63AD', W, 'stripes'), // Huddersfield
  46: kit('#F5A12D', B), // Hull
  47: kit('#0044A9', W), // Ipswich
  48: kit(W, '#1D428A'), // Leeds
  49: kit('#003090', W), // Leicester
  50: kit('#D6001C', W), // Leyton Orient
  51: kit('#E21E26', W, 'stripes'), // Lincoln
  52: kit('#C8102E', W), // Liverpool
  53: kit('#F78F1E', '#002D62'), // Luton
  54: kit('#6CABDD', '#1C2C5B'), // Man City
  55: kit('#DA291C', W), // Man United
  56: kit('#F2A900', '#1E4191'), // Mansfield
  57: kit('#E11B22', W), // Middlesbrough
  58: kit('#001D5E', W), // Millwall
  59: kit(W, B), // Milton Keynes Dons
  60: kit('#D71921', W), // Morecambe
  61: kit('#241F20', W, 'stripes'), // Newcastle
  62: kit('#F7941D', B), // Newport County
  63: kit('#8A1538', W), // Northampton
  64: kit('#FFF200', '#00A650'), // Norwich
  65: kit('#DD0000', W), // Nott'm Forest
  66: kit(B, W, 'stripes'), // Notts County
  67: kit('#1C4BA0', W), // Oldham
  68: kit('#FFE100', '#002147'), // Oxford
  69: kit('#0054A4', W), // Peterboro
  70: kit('#00553E', W), // Plymouth
  71: kit(W, B), // Port Vale
  72: kit('#001489', W), // Portsmouth
  73: kit(W, '#1D2C5E'), // Preston
  74: kit('#1D5BA4', W, 'hoops'), // QPR
  75: kit('#004494', W, 'hoops'), // Reading
  100: kit('#1450A0', B), // Rochdale
  76: kit('#E2001A', W), // Rotherham
  77: kit('#E4002B', W), // Salford
  101: kit('#7C1E3F', '#8DC8E8'), // Scunthorpe
  78: kit('#EE2737', W, 'stripes'), // Sheffield United
  79: kit('#0E4BA1', W, 'stripes'), // Sheffield Weds
  80: kit('#0D3B8C', '#F4B223'), // Shrewsbury
  81: kit('#D71920', W, 'stripes'), // Southampton
  102: kit('#1D3F91', W), // Southend
  82: kit('#E4002B', W), // Stevenage
  83: kit('#005DAA', W), // Stockport
  84: kit('#E03A3E', W, 'stripes'), // Stoke
  85: kit('#EB172B', W, 'stripes'), // Sunderland
  86: kit('#F9B000', '#4A2C1A'), // Sutton
  87: kit(W, B), // Swansea
  88: kit('#DC161B', W), // Swindon
  89: kit(W, '#132257'), // Tottenham
  90: kit(W, '#1C3F94'), // Tranmere
  91: kit('#D71920', W), // Walsall
  92: kit('#FBEE23', B), // Watford
  93: kit('#122F67', W, 'stripes'), // West Brom
  94: kit('#7A263A', '#1BB1E7'), // West Ham
  95: kit('#1D59AF', W, 'stripes'), // Wigan
  96: kit('#FDB913', '#231F20'), // Wolves
  97: kit('#D71920', W), // Wrexham
  98: kit('#88C4E8', '#0A2240'), // Wycombe
  402: kit('#CF142B', W), // York
}

// ---- colour maths -----------------------------------------------------------

const rgb = (hex: string): [number, number, number] => {
  const n = parseInt(hex.slice(1), 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

const toHex = ([r, g, b]: number[]) =>
  '#' + [r, g, b].map((v) => Math.round(v).toString(16).padStart(2, '0')).join('')

/** WCAG relative luminance. */
export function luminance(hex: string): number {
  const [r, g, b] = rgb(hex).map((v) => {
    const c = v / 255
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

/** WCAG contrast ratio, 1–21. */
export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

/** Composite `hex` at `alpha` over `bg` — what a /10 tint actually paints. */
export function over(hex: string, alpha: number, bg: string): string {
  const [f, g] = [rgb(hex), rgb(bg)]
  return toHex(f.map((v, i) => v * alpha + g[i] * (1 - alpha)))
}

/** Step towards black until `ok` holds. Every colour passes eventually. */
function darkenUntil(hex: string, ok: (c: string) => boolean): string {
  let c = hex
  for (let t = 0.05; !ok(c) && t <= 1; t += 0.05) c = toHex(rgb(hex).map((v) => v * (1 - t)))
  return c
}

const isWhite = (hex: string) => Math.min(...rgb(hex)) >= 0xe0

/** Perceptual-ish RGB distance ("redmean"); ~0 identical, ~765 black vs white. */
export function colourDistance(a: string, b: string): number {
  const [x, y] = [rgb(a), rgb(b)]
  const rm = (x[0] + y[0]) / 2
  const [dr, dg, db] = [x[0] - y[0], x[1] - y[1], x[2] - y[2]]
  return Math.sqrt((2 + rm / 256) * dr * dr + 4 * dg * dg + (2 + (255 - rm) / 256) * db * db)
}

// ---- themes -----------------------------------------------------------------

/** The theme a kit wears when `identity` is the colour that carries it. */
function themeWith(k: Kit, identity: string): Theme {
  const accent = darkenUntil(identity, (c) => Math.max(contrast(c, WHITE), contrast(c, INK)) >= AA)
  const onAccent = contrast(accent, WHITE) >= contrast(accent, INK) ? WHITE : INK
  // Text on the page: the identity colour if it reads there, else whichever
  // kit colour reads best, darkened until it does.
  const best = [identity, k.shirt, k.trim].sort((a, b) => contrast(b, PAGE) - contrast(a, PAGE))[0]
  const readable = (c: string) => contrast(c, PAGE) >= AA && contrast(c, over(accent, 0.1, WHITE)) >= AA
  const accentInk = readable(identity) ? identity : darkenUntil(best, readable)
  return { kit: k, accent, onAccent, accentInk }
}

const identityOf = (k: Kit) => (isWhite(k.shirt) ? k.trim : k.shirt)

export function themeOf(k: Kit): Theme {
  return themeWith(k, identityOf(k))
}

/** A team's kit; any id we hold no colours for (or none at all) gets the pitch. */
export const kitOf = (teamId: number | null | undefined): Kit =>
  (teamId != null && KITS[teamId]) || DEFAULT_KIT

export const teamTheme = (teamId: number | null | undefined): Theme => themeOf(kitOf(teamId))

/** Two accents this close read as one side on the Fixture view. */
export const CLASH = 130

/** The away side's theme against a given home theme — the change-kit rule. On a
 * clash (Chelsea v Everton: both blue) it changes into the first of its other
 * kit colour, then the CHANGE_KITS, that does not clash too. */
export function awayTheme(home: Theme, awayId: number | null | undefined): Theme {
  const t = teamTheme(awayId)
  if (colourDistance(home.accent, t.accent) >= CLASH) return t
  const other = identityOf(t.kit) === t.kit.shirt ? t.kit.trim : t.kit.shirt
  const changes = [other, ...CHANGE_KITS].filter((c) => !isWhite(c))
  const alt = changes.find((c) => colourDistance(home.accent, c) >= CLASH) ?? CHANGE_KITS[0]
  return themeWith(t.kit, alt)
}

/** Inline style that re-points the palette's accent trio at a theme; every
 * bg-accent / text-accent-ink inside the element picks the club up. */
export const themeStyle = (t: Theme) =>
  ({
    '--color-accent': t.accent,
    '--color-on-accent': t.onAccent,
    '--color-accent-ink': t.accentInk,
  }) as CSSProperties
