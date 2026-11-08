import { type CxOptions, cx } from "class-variance-authority";
import { extendTailwindMerge } from "tailwind-merge";

// From T3 Code apps/web/src/lib/utils.ts: registers the extra font sizes and
// background images so tailwind-merge does not treat them as colours.
const twMerge = extendTailwindMerge({
  extend: {
    theme: { text: ["2xs", "3xs", "4xs", "5xs"] },
    classGroups: { "bg-image": [{ bg: ["composer-seam-below", "composer-seam-above"] }] },
  },
});

export function cn(...inputs: CxOptions) {
  return twMerge(cx(inputs));
}
