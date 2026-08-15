'use client';

import { useEffect, useRef, useState } from 'react';
// eslint-disable-next-line import/no-named-as-default
import Chart from 'chart.js/auto';

type CallRow = {
  call_id: string;
  started_at: string;
  ended_at: string | null;
  channel: string | null;
  language: string | null;
  outcome: string | null;
  failure_type: string | null;
};

type Stats = {
  total: number;
  successful: number;
  failed: number;
  success_rate: number;
  failure_breakdown: Record<string, number>;
  recent: CallRow[];
};

const PALETTE = ['#ef4444', '#f59e0b', '#8b5cf6', '#3b82f6', '#14b8a6', '#64748b'];

export default function DashboardPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const chartRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    fetch('/api/calls')
      .then((r) => r.json())
      .then(setStats)
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    const canvas = chartRef.current;
    if (!stats || !canvas || Object.keys(stats.failure_breakdown).length === 0) return;
    const chart = new Chart(canvas, {
      type: 'doughnut',
      data: {
        labels: Object.keys(stats.failure_breakdown),
        datasets: [
          {
            data: Object.values(stats.failure_breakdown),
            backgroundColor: PALETTE,
            borderWidth: 0,
          },
        ],
      },
      options: { plugins: { legend: { position: 'bottom' } } },
    });
    return () => chart.destroy();
  }, [stats]);

  if (error) return <p className="p-8 text-red-500">Failed to load: {error}</p>;
  if (!stats) return <p className="p-8">Loading…</p>;

  const hasFailures = Object.keys(stats.failure_breakdown).length > 0;

  return (
    <main className="mx-auto max-w-5xl px-6 pt-28 pb-16">
      <h1 className="text-2xl font-bold">Call Analytics</h1>
      <p className="text-muted-foreground mt-1 text-sm">
        Live from the calls table — refresh to update.
      </p>

      <section className="mt-8 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Total calls" value={stats.total} />
        <StatCard label="Successful" value={stats.successful} accent="text-green-500" />
        <StatCard label="Failed" value={stats.failed} accent="text-red-500" />
        <StatCard label="Success rate" value={`${stats.success_rate}%`} />
      </section>

      <section className="mt-8 grid gap-4 lg:grid-cols-2">
        {hasFailures && (
          <div className="rounded-xl border p-5">
            <h2 className="mb-3 text-sm font-semibold">Failure breakdown</h2>
            <div className="mx-auto max-w-xs">
              <canvas ref={chartRef} aria-label="Failure type breakdown chart" />
            </div>
          </div>
        )}
        {!hasFailures && stats.failed > 0 && (
          <div className="text-muted-foreground rounded-xl border p-5 text-sm">
            No failure-type data for current failures.
          </div>
        )}

        <div className="rounded-xl border p-5">
          <h2 className="mb-3 text-sm font-semibold">Recent calls</h2>
          {stats.recent.length === 0 ? (
            <p className="text-muted-foreground text-sm">No calls recorded yet.</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted-foreground border-b text-left">
                  <th className="py-1 pr-3 font-medium">Call</th>
                  <th className="py-1 pr-3 font-medium">Started</th>
                  <th className="py-1 pr-3 font-medium">Channel</th>
                  <th className="py-1 pr-3 font-medium">Language</th>
                  <th className="py-1 font-medium">Outcome</th>
                </tr>
              </thead>
              <tbody>
                {stats.recent.map((c) => (
                  <tr key={c.call_id} className="border-muted border-b last:border-0">
                    <td className="py-1.5 pr-3 font-mono">Call #{c.call_id}</td>
                    <td className="text-muted-foreground py-1.5 pr-3">
                      {new Date(c.started_at).toLocaleString()}
                    </td>
                    <td className="py-1.5 pr-3">{c.channel ?? '—'}</td>
                    <td className="py-1.5 pr-3">{c.language ?? '—'}</td>
                    <td className="py-1.5">
                      <span
                        className={
                          c.outcome === 'success'
                            ? 'text-green-500'
                            : c.outcome === 'failed'
                              ? 'text-red-500'
                              : 'text-muted-foreground'
                        }
                      >
                        {c.outcome ?? 'in progress'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>
    </main>
  );
}

function StatCard({
  label,
  value,
  accent = '',
}: {
  label: string;
  value: number | string;
  accent?: string;
}) {
  return (
    <div className="rounded-xl border p-5">
      <p className="text-muted-foreground text-xs tracking-wide uppercase">{label}</p>
      <p className={`mt-1 text-3xl font-bold ${accent}`}>{value}</p>
    </div>
  );
}
