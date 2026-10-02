import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from 'react'
import type { Arrow, GameState } from './types'

const CELL = 24
const PAD = 26

function point(cell: [number, number]) {
  return [cell[1] * CELL + PAD, cell[0] * CELL + PAD]
}

export function arrowPath(arrow: Arrow) {
  const points = arrow.path.map(point)
  let d = `M ${points[0][0]} ${points[0][1]}`
  for (let i = 1; i < points.length - 1; i++) {
    const [x, y] = points[i],
      [px, py] = points[i - 1],
      [nx, ny] = points[i + 1]
    const radius = 2.5
    const before = [x + Math.sign(px - x) * radius, y + Math.sign(py - y) * radius]
    const after = [x + Math.sign(nx - x) * radius, y + Math.sign(ny - y) * radius]
    d += ` L ${before[0]} ${before[1]} Q ${x} ${y} ${after[0]} ${after[1]}`
  }
  return `${d} L ${points.at(-1)!.join(' ')}`
}

function geometry(arrow: Arrow) {
  const head = point(arrow.path.at(-1)!),
    previous = point(arrow.path.at(-2)!)
  const dx = Math.sign(head[0] - previous[0]),
    dy = Math.sign(head[1] - previous[1])
  const name = dx === 1 ? 'right' : dx === -1 ? 'left' : dy === 1 ? 'down' : 'up'
  const triangle = `${head[0] + dx * 3},${head[1] + dy * 3} ${head[0] - dx * 5 - dy * 4},${head[1] - dy * 5 + dx * 4} ${head[0] - dx * 5 + dy * 4},${head[1] - dy * 5 - dx * 4}`
  return { head, dx, dy, name, triangle }
}

interface Props {
  state: GameState
  color: string
  zoom: number
  dots: boolean
  motion: boolean
  hintId: number | null
  blockedId: number | null
  busy: boolean
  onTap: (id: number) => void
}

export default function Board({
  state,
  color,
  zoom,
  dots,
  motion,
  hintId,
  blockedId,
  busy,
  onTap,
}: Props) {
  const [hover, setHover] = useState<number | null>(null)
  const [departing, setDeparting] = useState<Arrow[]>([])
  const last = useRef(state)
  const viewport = useRef<HTMLDivElement>(null)
  const drag = useRef<{ x: number; y: number; left: number; top: number } | null>(null)
  const [panning, setPanning] = useState(false)

  useLayoutEffect(() => {
    if (state.session_id !== last.current.session_id) {
      setDeparting([])
      setHover(null)
      if (viewport.current) {
        viewport.current.scrollLeft = 0
        viewport.current.scrollTop = 0
      }
    } else if (motion) {
      const ids = new Set(state.arrows.map((a) => a.id))
      // A rejected prediction may restore an arrow before its exit animation ends.
      setDeparting((previous) => previous.filter((a) => !ids.has(a.id)))
      const gone = last.current.arrows.filter((a) => !ids.has(a.id))
      if (gone.length) setDeparting((previous) => [...previous, ...gone])
    }
    last.current = state
  }, [state, motion])

  useEffect(() => {
    if (!departing.length) return
    const timeout = window.setTimeout(() => setDeparting([]), 650)
    return () => window.clearTimeout(timeout)
  }, [departing])

  const width = (state.level.columns - 1) * CELL + PAD * 2
  const height = (state.level.rows - 1) * CELL + PAD * 2
  const selected = state.arrows.find((a) => a.id === (hintId ?? hover))
  const travel = Math.max(width, height) + 100

  return (
    <div
      className={`board-viewport ${panning ? 'panning' : ''}`}
      ref={viewport}
      onPointerDown={(event) => {
        if (zoom === 1 || (event.target as Element).closest('[data-arrow]')) return
        const el = viewport.current!
        drag.current = {
          x: event.clientX,
          y: event.clientY,
          left: el.scrollLeft,
          top: el.scrollTop,
        }
        setPanning(true)
        event.currentTarget.setPointerCapture(event.pointerId)
      }}
      onPointerMove={(event) => {
        if (!drag.current || !viewport.current) return
        viewport.current.scrollLeft = drag.current.left - event.clientX + drag.current.x
        viewport.current.scrollTop = drag.current.top - event.clientY + drag.current.y
      }}
      onPointerUp={() => {
        drag.current = null
        setPanning(false)
      }}
      onPointerCancel={() => {
        drag.current = null
        setPanning(false)
      }}
    >
      <div className="board-scene" style={{ width: `${zoom * 100}%`, height: `${zoom * 100}%` }}>
        <svg
          className="puzzle-svg"
          viewBox={`0 0 ${width} ${height}`}
          role="group"
          aria-label={`Level ${state.level.number} puzzle. ${state.remaining_count} arrows remaining. Tap an arrow whose forward path is clear.`}
        >
          <defs>
            <pattern
              id="grid-dots"
              x={PAD}
              y={PAD}
              width={CELL}
              height={CELL}
              patternUnits="userSpaceOnUse"
            >
              <circle cx="0" cy="0" r="0.65" fill="#deded1" />
            </pattern>
          </defs>
          {dots && (
            <rect x="12" y="12" width={width - 24} height={height - 24} fill="url(#grid-dots)" />
          )}
          {selected &&
            (() => {
              const { head, dx, dy } = geometry(selected)
              return (
                <path
                  d={`M ${head.join(' ')} l ${dx * travel} ${dy * travel}`}
                  stroke={color}
                  strokeWidth="1"
                  strokeDasharray="3 5"
                  opacity="0.35"
                />
              )
            })()}
          {state.arrows.map((arrow) => {
            const { triangle, name } = geometry(arrow)
            const highlighted = arrow.id === hintId
            return (
              <g
                key={arrow.id}
                data-arrow={arrow.id}
                className={`game-arrow ${blockedId === arrow.id ? 'blocked' : ''} ${highlighted ? 'hinted' : ''}`}
                role="button"
                tabIndex={busy || state.status !== 'active' ? -1 : 0}
                aria-label={`Arrow ${arrow.id}, points ${name}`}
                aria-disabled={busy || state.status !== 'active'}
                onClick={() => !busy && state.status === 'active' && onTap(arrow.id)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    if (!busy && state.status === 'active') onTap(arrow.id)
                  }
                }}
                onMouseEnter={() => setHover(arrow.id)}
                onMouseLeave={() => setHover(null)}
                onFocus={() => setHover(arrow.id)}
                onBlur={() => setHover(null)}
                style={{ '--arrow-color': color } as CSSProperties}
              >
                <title>{`Arrow ${arrow.id} · ${name}`}</title>
                <path
                  className="arrow-hitbox"
                  d={arrowPath(arrow)}
                  fill="none"
                  stroke="transparent"
                  strokeWidth="15"
                  strokeLinecap="round"
                />
                {highlighted && (
                  <path
                    d={arrowPath(arrow)}
                    stroke="#d2ad51"
                    strokeWidth="9"
                    fill="none"
                    opacity="0.2"
                    strokeLinecap="round"
                  />
                )}
                <path
                  className="arrow-line"
                  d={arrowPath(arrow)}
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.3"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                <polygon className="arrow-head" points={triangle} fill="currentColor" />
              </g>
            )
          })}
          {departing.map((arrow) => {
            const { head, dx, dy, triangle } = geometry(arrow)
            const length = (arrow.path.length - 1) * CELL
            return (
              <g
                key={`exit-${arrow.id}`}
                className="departing-arrow"
                aria-hidden="true"
                style={
                  {
                    color,
                    '--travel': -travel,
                    '--exit-x': `${dx * travel}px`,
                    '--exit-y': `${dy * travel}px`,
                  } as CSSProperties
                }
              >
                <path
                  className="departing-line"
                  d={`${arrowPath(arrow)} L ${head[0] + dx * travel} ${head[1] + dy * travel}`}
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.3"
                  strokeLinecap="round"
                  strokeDasharray={`${length} ${length + travel * 2}`}
                />
                <polygon className="departing-head" points={triangle} fill="currentColor" />
              </g>
            )
          })}
        </svg>
      </div>
    </div>
  )
}
