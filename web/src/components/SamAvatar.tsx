/** The supplied Sam artwork, shared by navigation, messages, and progress. */
export function SamAvatar({ size = 40, busy = false }: { size?: number; busy?: boolean }) {
  return <img className={`sam-avatar${busy ? " busy" : ""}`} src={`${import.meta.env.BASE_URL}sam-avatar.png`}
    width={size} height={size} alt="Sam the Minuteman" decoding="async" />;
}
