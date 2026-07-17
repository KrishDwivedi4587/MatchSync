"use client";

import { AlertTriangle, CheckCircle2, XCircle } from "lucide-react";

import type { ImportReportDto, ImportRunSummary } from "@/lib/fixtures/api";
import { cn } from "@/lib/utils";

const STATUS_STYLES: Record<string, string> = {
  success: "text-primary",
  partial: "text-amber-600 dark:text-amber-500",
  failed: "text-destructive",
  running: "text-muted-foreground",
};

function StatusIcon({ status }: { status: string }) {
  if (status === "success") return <CheckCircle2 className="h-4 w-4" aria-hidden />;
  if (status === "failed") return <XCircle className="h-4 w-4" aria-hidden />;
  return <AlertTriangle className="h-4 w-4" aria-hidden />;
}

/** Statistics grid for one import report. */
export function ImportReportView({ report }: { report: ImportReportDto }) {
  const entries: [string, number][] = [
    ["Fetched", report.stats.fetched],
    ["Created", report.stats.created],
    ["Updated", report.stats.updated],
    ["Unchanged", report.stats.unchanged],
    ["Duplicates", report.stats.duplicates],
    ["Invalid", report.stats.invalid],
    ["Skipped", report.stats.skipped_out_of_window + report.stats.skipped_stale],
    ["Deleted", report.stats.deleted],
    ["Failed", report.stats.failed],
  ];

  return (
    <div className="space-y-4 rounded-lg border p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span
          className={cn("flex items-center gap-2 font-medium", STATUS_STYLES[report.status])}
        >
          <StatusIcon status={report.status} />
          Import {report.status}
        </span>
        <span className="text-xs text-muted-foreground">
          {report.provider_key} · {report.duration_ms} ms
        </span>
      </div>

      <dl className="grid grid-cols-3 gap-3 sm:grid-cols-5">
        {entries.map(([label, value]) => (
          <div key={label} className="rounded-md bg-muted/50 p-2 text-center">
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="text-lg font-semibold tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>

      {report.errors.length > 0 && (
        <details className="rounded-md border border-destructive/40 bg-destructive/5 p-3">
          <summary className="cursor-pointer text-sm font-medium text-destructive">
            {report.errors.length} error(s)
          </summary>
          <ul className="mt-2 space-y-1 text-xs">
            {report.errors.slice(0, 20).map((issue, i) => (
              <li key={i}>
                <code className="font-mono">{issue.code}</code> — {issue.message}
              </li>
            ))}
          </ul>
        </details>
      )}

      {report.warnings.length > 0 && (
        <details className="rounded-md border p-3">
          <summary className="cursor-pointer text-sm font-medium">
            {report.warnings.length} warning(s)
          </summary>
          <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
            {report.warnings.slice(0, 20).map((issue, i) => (
              <li key={i}>
                <code className="font-mono">{issue.code}</code> — {issue.message}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

/** Recent import runs table. */
export function ImportRunsTable({ runs }: { runs: ImportRunSummary[] }) {
  if (runs.length === 0) {
    return <p className="text-sm text-muted-foreground">No imports have run yet.</p>;
  }

  return (
    <div className="overflow-x-auto rounded-lg border">
      <table className="w-full text-sm">
        <thead className="bg-muted/50 text-left text-xs uppercase text-muted-foreground">
          <tr>
            <th className="p-3">Provider</th>
            <th className="p-3">Status</th>
            <th className="p-3 text-right">Created</th>
            <th className="p-3 text-right">Updated</th>
            <th className="p-3 text-right">Dupes</th>
            <th className="p-3 text-right">Errors</th>
            <th className="p-3 text-right">Duration</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {runs.map((run) => (
            <tr key={run.id}>
              <td className="p-3">
                <span className="font-medium">{run.provider_key}</span>
                {run.sport_key && (
                  <span className="block text-xs text-muted-foreground">{run.sport_key}</span>
                )}
              </td>
              <td className={cn("p-3 font-medium", STATUS_STYLES[run.status])}>{run.status}</td>
              <td className="p-3 text-right tabular-nums">{run.created_count}</td>
              <td className="p-3 text-right tabular-nums">{run.updated_count}</td>
              <td className="p-3 text-right tabular-nums">{run.duplicate_count}</td>
              <td className="p-3 text-right tabular-nums">
                {run.invalid_count + run.failed_count}
              </td>
              <td className="p-3 text-right tabular-nums">{run.duration_ms} ms</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
