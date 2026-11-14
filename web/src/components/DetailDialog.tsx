import { useEffect, useRef, type ReactNode } from "react";

export function DetailDialog({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    ref.current?.showModal();
    return () => { previous?.focus(); };
  }, []);
  return <dialog ref={ref} className="detail-dialog" aria-label={title}
    onCancel={(event) => { event.preventDefault(); onClose(); }}
    onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="detail-dialog-top"><span>Kitchen plan <span aria-hidden> / </span><b>{title}</b></span><button className="btn" autoFocus onClick={onClose} aria-label={`Close ${title}`}>Close <span aria-hidden>×</span></button></div>
    <div className="detail-dialog-body">
      {children}
    </div>
  </dialog>;
}
