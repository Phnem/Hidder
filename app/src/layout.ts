/**
 * A keyboard to draw keys on, and an honest statement of what it is not.
 *
 * # This layout was not read from any device
 *
 * It is a generic 75% arrangement, written here as data. Nothing has asked a
 * board how its keys are placed, no layout came out of a registry, and the fact
 * that this arrangement happens to total 84 keys does not make it the
 * arrangement of the 84-key board on the developer's desk.
 *
 * Real layouts arrive with the registry's `layout` table and a KLE import
 * (spec.md, Appendix B and the SAFE_DEFAULT on layout import), and TICKET-13
 * explicitly allows a placeholder until then. The screen that renders this says
 * so where the user can read it. A placeholder that did not announce itself
 * would be this application's first invented fact about someone's hardware.
 *
 * # Why it is here rather than fetched
 *
 * Because it is presentation, not device knowledge. `pcore` deals in labelled
 * keys and millimetres; which rectangle a label is drawn in is this layer's
 * business, and putting a placeholder in the backend would make it look like
 * something the backend established.
 */

export interface LayoutKey {
  /** Unique within the layout, so selection survives duplicate labels. */
  id: string;
  /** What is printed on the key, and what a per-key value is matched against. */
  label: string;
  /** Width in key units. Defaults to 1. */
  w?: number;
}

export interface PlaceholderLayout {
  name: string;
  rows: LayoutKey[][];
}

function row(...keys: (string | [string, number])[]): LayoutKey[] {
  return keys.map((key, index) => {
    const [label, w] = typeof key === "string" ? [key, 1] : key;
    return { id: `${label}#${index}`, label, w };
  });
}

/**
 * A generic 75% arrangement. Not a claim about any board.
 */
export const PLACEHOLDER_75: PlaceholderLayout = {
  name: "generic 75%",
  rows: [
    row(
      "Esc", "F1", "F2", "F3", "F4", "F5", "F6",
      "F7", "F8", "F9", "F10", "F11", "F12", "Del",
    ),
    row(
      "`", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "-", "=",
      ["Bksp", 2], "Home",
    ),
    row(
      ["Tab", 1.5], "Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P",
      "[", "]", ["\\", 1.5], "PgUp",
    ),
    row(
      ["Caps", 1.75], "A", "S", "D", "F", "G", "H", "J", "K", "L",
      ";", "'", ["Enter", 2.25], "PgDn",
    ),
    row(
      ["Shift", 2.25], "Z", "X", "C", "V", "B", "N", "M", ",", ".", "/",
      ["Shift", 1.75], "↑", "End",
    ),
    row(
      ["Ctrl", 1.25], ["Win", 1.25], ["Alt", 1.25], ["Space", 6.25],
      "Alt", "Fn", "Ctrl", "←", "↓", "→",
    ),
  ],
};

/** Every label in a layout, in order, duplicates included. */
export function layoutLabels(layout: PlaceholderLayout): string[] {
  return layout.rows.flatMap((keys) => keys.map((key) => key.label));
}

/** How many keys the layout draws. */
export function layoutKeyCount(layout: PlaceholderLayout): number {
  return layout.rows.reduce((total, keys) => total + keys.length, 0);
}
