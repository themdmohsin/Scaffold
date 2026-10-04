import type { Argv } from "yargs"
import { UI } from "../ui"

/**
 * Scaffold fork: the upstream OpenCode upgrade command is disabled. Running it
 * must never download/replace this install with upstream OpenCode — Scaffold
 * updates ship from the Scaffold repository. Kept as a command (instead of
 * removed) so old muscle memory/scripts get a clear explanation, not a
 * yargs "unknown command" error.
 */
export const UpgradeCommand = {
  command: "upgrade [target]",
  describe: "disabled — Scaffold updates ship from the Scaffold repository",
  builder: (yargs: Argv) => {
    return yargs.positional("target", {
      describe: "ignored; upstream OpenCode versions cannot be installed over Scaffold",
      type: "string",
    })
  },
  handler: async () => {
    UI.empty()
    UI.println(UI.logo("  "))
    UI.empty()
    UI.println("Scaffold upgrades are disabled.")
    UI.empty()
    UI.println("This install is the Scaffold client, not upstream OpenCode, so the")
    UI.println("upstream upgrade path is turned off to prevent it from replacing Scaffold.")
    UI.empty()
    UI.println("Update Scaffold from its repository instead:")
    UI.println("  git pull   (in your Scaffold checkout, then re-run bun install)")
    UI.empty()
  },
}
