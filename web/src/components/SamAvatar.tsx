/** Sam the Minuteman, drawn for this app (an original tricorn-hat sketch in UMass maroon, not the official mark). */
export function SamAvatar({ size = 40, busy = false }: { size?: number; busy?: boolean }) {
  return (
    <svg className={`sam-avatar${busy ? " busy" : ""}`} width={size} height={size} viewBox="0 0 64 64" role="img" aria-label="Sam the Minuteman">
      <g>
        <rect width="64" height="64" fill="var(--maroon-soft)" />
        {/* coat and cravat */}
        <path d="M10 64 Q32 46 54 64Z" fill="var(--maroon)" />
        <path d="M27 52 L32 61 L37 52Z" fill="#fff" />
        {/* powdered hair at the sides */}
        <circle cx="19" cy="38" r="5" fill="#fff" stroke="#d8d2c8" strokeWidth="1" />
        <circle cx="45" cy="38" r="5" fill="#fff" stroke="#d8d2c8" strokeWidth="1" />
        {/* face */}
        <circle cx="32" cy="37" r="13" fill="#f2cfae" />
        <circle cx="27" cy="36" r="1.6" fill="#2a2420" />
        <circle cx="37" cy="36" r="1.6" fill="#2a2420" />
        <path d="M27.5 42 Q32 45.5 36.5 42" stroke="#2a2420" strokeWidth="1.6" fill="none" strokeLinecap="round" />
        {/* tricorn hat */}
        <path d="M19 26 Q32 6 45 26Z" fill="#6e1515" />
        <path d="M6 27 Q32 9 58 27 Q46 22.5 32 24.5 Q18 22.5 6 27Z" fill="var(--maroon)" />
        <path d="M6 27 Q18 22.5 32 24.5 Q46 22.5 58 27" stroke="#d9a441" strokeWidth="1.6" fill="none" strokeLinecap="round" />
        <circle cx="32" cy="22" r="2" fill="#d9a441" />
      </g>
    </svg>
  );
}
