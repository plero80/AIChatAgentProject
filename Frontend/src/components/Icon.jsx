const paths = {
  leaf: <><path d="M19.5 4.5c-8-2-15 1.5-15 8a6 6 0 0 0 6 6c6.5 0 10-7 9-14Z" /><path d="M4 21 15 10M10 15v-5m0 5h5" /></>,
  bowl: <><path d="M3 12h18a9 9 0 0 1-18 0ZM8 21h8M8 8c-2-2 2-3 0-5m5 5c-2-2 2-3 0-5m5 5c-2-2 2-3 0-5" /></>,
  sparkles: <><path d="m12 3 2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4L12 3ZM20 2v4m-2-2h4" /></>,
  arrow: <path d="M19 12H5m6-6-6 6 6 6" />,
  send: <path d="m21 3-7 18-4-7-7-4 18-7ZM10 14 21 3" />,
}

export default function Icon({ name, className = '' }) {
  return <svg className={`icon ${className}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] || paths.leaf}</svg>
}
