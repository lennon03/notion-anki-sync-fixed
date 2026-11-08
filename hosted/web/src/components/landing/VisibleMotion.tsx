"use client";

import type { ComponentProps } from "react";

import { observeVisibleAnimation } from "~/lib/visibleAnimation";

/** Runs the animations inside only while on screen and motion is allowed. */
export function VisibleMotion(props: ComponentProps<"div">) {
  return <div {...props} ref={observeVisibleAnimation} />;
}
