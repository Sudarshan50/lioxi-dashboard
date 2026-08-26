import { X } from "lucide-react";
import { ReactNode } from "react";
import { createPortal } from "react-dom";

interface ModalProps {
  title: ReactNode;
  isOpen: boolean;
  onClose: () => void;
  children: ReactNode;
  widthClassName?: string;
}

export default function Modal({ title, isOpen, onClose, children, widthClassName = "max-w-lg" }: ModalProps) {
  if (!isOpen) return null;
  // Portal to <body>: ancestors with transform/backdrop-filter (glassy cards,
  // fade-up animation) would otherwise become the containing block for
  // position:fixed and trap the overlay inside the card.
  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className={`flex max-h-[90vh] w-full ${widthClassName} flex-col rounded-2xl border border-surface-border bg-surface-raised p-5 shadow-card transition-[max-width] duration-500 ease-out sm:p-6`}
      >
        <div className="mb-5 flex shrink-0 items-center justify-between">
          <h2 className="text-lg font-semibold text-gray-100">{title}</h2>
          <button onClick={onClose} className="rounded-md p-1 text-gray-400 hover:bg-surface-border hover:text-gray-100">
            <X size={18} />
          </button>
        </div>
        <div className="overflow-y-auto pr-1">{children}</div>
      </div>
    </div>,
    document.body
  );
}
