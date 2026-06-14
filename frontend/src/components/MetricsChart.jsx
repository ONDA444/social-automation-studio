import { BarChart, Bar, LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts'

const axis = { stroke: 'var(--text-muted)', fontSize: 11 }
const tooltipStyle = {
  contentStyle: { background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 8, color: 'var(--text-primary)' },
  labelStyle: { color: 'var(--text-muted)' },
}

export function BarMetrics({ data, x, y, color = 'var(--accent)', height = 240 }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
        <XAxis dataKey={x} {...axis} />
        <YAxis {...axis} />
        <Tooltip {...tooltipStyle} cursor={{ fill: 'var(--bg-elevated)' }} />
        <Bar dataKey={y} fill={color} radius={[6, 6, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  )
}

export function LineMetrics({ data, x, y, color = 'var(--success)', height = 240 }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
        <XAxis dataKey={x} {...axis} />
        <YAxis {...axis} />
        <Tooltip {...tooltipStyle} />
        <Line type="monotone" dataKey={y} stroke={color} strokeWidth={2} dot={false} />
      </LineChart>
    </ResponsiveContainer>
  )
}
