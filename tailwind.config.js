/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./Web/index.html", "./Web/*.js"],
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