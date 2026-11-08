import type { ComponentProps, ComponentPropsWithoutRef, ReactNode } from "react";

import { cn } from "~/lib/utils";

/*
 * SettingsGroup, SettingsSection and SettingsRow from T3 Code
 * (components/settings/), with the markup and classes unchanged and T3's
 * settings-scope plumbing removed.
 */

export function SettingsGroup({
  variant = "grouped",
  divided = true,
  className,
  ...props
}: ComponentProps<"div"> & {
  variant?: "grouped" | "plain";
  divided?: boolean;
}) {
  return (
    <div
      data-slot="settings-group"
      {...props}
      className={cn(
        "relative overflow-visible text-foreground",
        variant === "grouped"
          ? "rounded-xl border border-border/60 bg-card/40 shadow-xs/5"
          : "space-y-1",
        variant === "grouped" &&
          divided &&
          "[&>*+*]:border-t [&>*+*]:border-border/50 [&>[data-slot=settings-row]]:rounded-none",
        className,
      )}
    />
  );
}

export function SettingsSection({
  title,
  headerAction,
  children,
  className,
  ...sectionProps
}: ComponentPropsWithoutRef<"section"> & {
  title: string;
  headerAction?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section {...sectionProps} className={cn("space-y-2.5", className)}>
      <div className="flex min-h-7 items-start justify-between gap-4 px-3 sm:px-4">
        <div className="flex min-w-0 items-center gap-1">
          <h2 className="flex min-h-7 items-center gap-2 text-base font-semibold text-foreground">
            {title}
          </h2>
        </div>
        <div className="flex min-h-7 min-w-7 items-center justify-end">{headerAction}</div>
      </div>
      <SettingsGroup>{children}</SettingsGroup>
    </section>
  );
}

export function SettingsRow({
  title,
  description,
  status,
  control,
  children,
  className,
  ...rowProps
}: Omit<ComponentPropsWithoutRef<"div">, "title"> & {
  title: ReactNode;
  description?: ReactNode;
  status?: ReactNode;
  control?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div
      {...rowProps}
      data-slot="settings-row"
      className={cn(
        "group/settings-row @container/settings-row rounded-xl px-3 sm:px-4 aria-disabled:opacity-64 aria-disabled:[&_*]:text-muted-foreground",
        children ? "pt-3 pb-1" : "py-3",
        className,
      )}
    >
      <div className="flex flex-col gap-3 @min-[32rem]/settings-row:grid @min-[32rem]/settings-row:grid-cols-[minmax(0,1fr)_minmax(10rem,auto)] @min-[32rem]/settings-row:items-center @min-[32rem]/settings-row:gap-8">
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex min-h-5 items-center gap-1.5">
            <h3 className="text-sm font-medium text-foreground">{title}</h3>
          </div>
          {description ? (
            <p className="max-w-2xl text-balance text-xs leading-normal text-muted-foreground">
              {description}
            </p>
          ) : null}
          {status ? <div className="pt-0.5 text-xs text-muted-foreground">{status}</div> : null}
        </div>
        {control ? (
          <div className="flex w-full min-w-0 shrink-0 items-center gap-2 @min-[32rem]/settings-row:w-auto @min-[32rem]/settings-row:justify-end">
            {control}
          </div>
        ) : null}
      </div>
      {children}
    </div>
  );
}
