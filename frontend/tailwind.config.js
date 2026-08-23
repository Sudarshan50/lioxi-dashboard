/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      colors: {
        surface: {
          DEFAULT: "#07080d",
          raised: "#10131c",
          hover: "#181c29",
          border: "#232839",
        },
        accent: {
          DEFAULT: "#6366f1",
          hover: "#818cf8",
          soft: "#818cf8",
        },
      },
      boxShadow: {
        card: "0 1px 2px rgba(0,0,0,0.5), 0 12px 32px -12px rgba(0,0,0,0.6)",
        "card-hover": "0 1px 2px rgba(0,0,0,0.5), 0 16px 44px -12px rgba(0,0,0,0.7), 0 0 0 1px rgba(99,102,241,0.18)",
        glow: "0 0 0 1px rgba(99,102,241,0.25), 0 8px 24px -8px rgba(99,102,241,0.35)",
        "glow-sm": "0 0 16px -2px rgba(99,102,241,0.45)",
        "btn-glow": "0 4px 20px -4px rgba(99,102,241,0.55), inset 0 1px 0 rgba(255,255,255,0.12)",
      },
      backgroundImage: {
        "grid-fade": "radial-gradient(circle at top left, rgba(99,102,241,0.12), transparent 45%)",
        "accent-gradient": "linear-gradient(135deg, #6366f1 0%, #8b5cf6 55%, #a855f7 100%)",
        "card-sheen": "linear-gradient(180deg, rgba(255,255,255,0.045) 0%, rgba(255,255,255,0) 38%)",
      },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.45s cubic-bezier(0.22, 1, 0.36, 1) both",
      },
    },
  },
  plugins: [],
};
