import { toast, Id, ToastOptions } from "react-toastify";

const base: ToastOptions = {
  position: "top-right",
  hideProgressBar: false,
  closeOnClick: true,
  pauseOnHover: true,
  draggable: false,
};

export function toastSuccess(message: string, toastId?: Id) {
  toast.success(message, { ...base, autoClose: 4000, toastId });
}

export function toastError(message: string, opts?: { persist?: boolean; toastId?: Id }) {
  toast.error(message, {
    ...base,
    autoClose: opts?.persist ? false : 8000,
    toastId: opts?.toastId,
  });
}

export function toastDismiss(toastId?: Id) {
  if (toastId == null) toast.dismiss();
  else toast.dismiss(toastId);
}
