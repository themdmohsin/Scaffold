// Scaffold rebrand: home-screen logo rendered as the box-drawing SCAFFOLD wordmark
// (same glyphs as the CLI banner in packages/opencode/src/cli/ui.ts). The original
// marker-font glyphs (OP ENS CODE in _^~ markers) are preserved in git history.
// The `go` glyph is unchanged — bg-pulse shimmer geometry depends on it.

const rows = [
  `███████╗  ██████╗  █████╗  ███████╗ ███████╗  ██████╗  ██╗      ██████╗ `,
  `██╔════╝ ██╔════╝ ██╔══██╗ ██╔════╝ ██╔════╝ ██╔═══██╗ ██║      ██╔══██╗`,
  `███████╗ ██║      ███████║ █████╗   █████╗   ██║   ██║ ██║      ██║  ██║`,
  `╚════██║ ██║      ██╔══██║ ██╔══╝   ██╔══╝   ██║   ██║ ██║      ██║  ██║`,
  `███████╗  ╚██████ ██║  ██║ ██║      ██║      ╚██████╔╝ ███████╗ ██████╔╝`,
]

export const logo = {
  left: rows,
  right: ["", "", "", "", ""],
}

export const go = {
  left: ["    ", "█▀▀▀", "█_^█", "▀▀▀▀"],
  right: ["    ", "█▀▀█", "█__█", "▀▀▀▀"],
}

export const marks = "_^~,"
