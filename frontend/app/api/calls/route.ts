import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';

// No hardcoded numbers - everything is queried live from the backend SQLite DB.
// KHATA_DB_PATH mirrors backend/src/khata_memory.py's default: backend/khata.db.
const DB_PATH = process.env.KHATA_DB_PATH ?? path.join(process.cwd(), '..', 'backend', 'khata.db');

export const revalidate = 0;

type Row = Record<string, unknown>;

export async function GET() {
  let db: DatabaseSync | null = null;
  try {
    db = new DatabaseSync(DB_PATH, { readOnly: true });
    const hasTable = db
      .prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='calls'")
      .get();
    if (!hasTable) {
      return Response.json(
        { total: 0, successful: 0, failed: 0, success_rate: 0, failure_breakdown: {}, recent: [] },
        { headers: { 'Cache-Control': 'no-store' } }
      );
    }

    const count = (sql: string): number => (db!.prepare(sql).get() as Row).n as number;

    const total = count('SELECT COUNT(*) AS n FROM calls');
    const successful = count("SELECT COUNT(*) AS n FROM calls WHERE outcome = 'success'");
    const failed = count("SELECT COUNT(*) AS n FROM calls WHERE outcome = 'failed'");

    const breakdown: Record<string, number> = {};
    for (const r of db
      .prepare(
        "SELECT failure_type, COUNT(*) AS n FROM calls WHERE outcome = 'failed' GROUP BY failure_type"
      )
      .all() as Row[]) {
      breakdown[(r.failure_type as string | null) ?? 'null'] = r.n as number;
    }

    const recent = (
      db
        .prepare(
          `SELECT call_id, started_at, ended_at, channel, language, outcome, failure_type
           FROM calls ORDER BY started_at DESC, rowid DESC LIMIT 20`
        )
        .all() as Row[]
    ).map((r) => ({
      call_id: r.call_id as string,
      started_at: r.started_at as string,
      ended_at: r.ended_at as string | null,
      channel: r.channel as string | null,
      language: r.language as string | null,
      outcome: r.outcome as string | null,
      failure_type: r.failure_type as string | null,
    }));

    return Response.json(
      {
        total,
        successful,
        failed,
        success_rate: total ? Math.round((successful / total) * 1000) / 10 : 0,
        failure_breakdown: breakdown,
        recent,
      },
      { headers: { 'Cache-Control': 'no-store' } }
    );
  } catch (error) {
    console.error(error);
    return Response.json({ error: 'failed to read calls table' }, { status: 500 });
  } finally {
    db?.close();
  }
}
