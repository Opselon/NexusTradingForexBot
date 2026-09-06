/** @type {import('tailwindcss').Config} */
module.exports = {
  // tv_widget.html is now inlined into index.html, but keep the standalone file
  // for direct /tv_widget.html serving and for safety when rebuilding.
  content: ["./Web/index.html", "./Web/tv_widget.html", "./Web/*.js"],
  safelist: [
    // White-card TradingView widget (light-on-light) — these were absent before,
    // so the purge build stripped bg-white / text-black / gray borders and left
    // invisible text on white. Keep them alive.
    "bg-white",
    "bg-gray-50",
    "bg-gray-50/60",
    "border-gray-100",
    "border-gray-200",
    "divide-gray-100",
    "text-black",
    "text-gray-500",
  ],
  theme: {
    extend: {
      colors: {
        darkBg: '#090d16',
        panelBg: '#121826',
        borderClr: '#1e293b',
        accentCyan: '#06b6d4',
        accentRose: '#f43f5e',
        accentGold: '#eab308',
        textMuted: '#94a3b8',
      }
    }
  },
  plugins: [],
}
