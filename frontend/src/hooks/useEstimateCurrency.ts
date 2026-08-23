import { useState } from "react";

import { EstimateCurrency } from "@/lib/format";

const STORAGE_KEY = "estimate-currency";

function readStored(): EstimateCurrency {
  try {
    return localStorage.getItem(STORAGE_KEY) === "INR" ? "INR" : "USD";
  } catch {
    return "USD";
  }
}

export function useEstimateCurrency() {
  const [currency, setCurrencyState] = useState<EstimateCurrency>(readStored);

  function setCurrency(next: EstimateCurrency) {
    setCurrencyState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* ignore quota / private mode */
    }
  }

  return { currency, setCurrency };
}
