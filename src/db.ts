import { Pool, QueryResult } from "pg";
import * as fs from "fs";
import * as path from "path";

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: process.env.DATABASE_URL?.includes("railway") || process.env.DATABASE_URL?.includes("sslmode=require")
    ? { rejectUnauthorized: false }
    : undefined,
});

export async function query<T = any>(text: string, params?: any[]): Promise<QueryResult<T>> {
  const client = await pool.connect();
  try {
    return await client.query<T>(text, params);
  } finally {
    client.release();
  }
}

export async function runSqlFile(filePath: string): Promise<void> {
  const sql = fs.readFileSync(filePath, "utf8");
  const statements = sql
    .split(";")
    .map(s => s.trim())
    .filter(s => s.length > 0 && !s.startsWith("--"));
  for (const stmt of statements) {
    await query(stmt + ";");
  }
}

export { pool };
