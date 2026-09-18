import { writeSync } from "node:fs";

const STDERR_FD = 2;
const processStartedAt = Date.now();

let fatalReported = false;

export function formatDuration(milliseconds: number): string {
  if (milliseconds < 1_000) return `${milliseconds}ms`;
  const seconds = milliseconds / 1_000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}m${String(rest).padStart(2, "0")}s`;
}

function prefix(): string {
  const elapsed = formatDuration(Date.now() - processStartedAt);
  return `${new Date().toISOString()} [+${elapsed}]`;
}

/** Normal operational logging; asynchronous when stderr is a pipe. */
export function log(message: string): void {
  process.stderr.write(`${prefix()} ${message}\n`);
}

/**
 * Synchronous stderr write. Survives `process.exit` in the same tick, unlike
 * `console.error`, which is asynchronous on a pipe and silently drops its
 * buffer when the process exits immediately afterwards. Every message that
 * explains why the process is ending must go through this.
 */
export function logSync(message: string): void {
  try {
    writeSync(STDERR_FD, `${prefix()} ${message}\n`);
  } catch {
    // stderr is already gone; there is nowhere left to report this.
  }
}

/** Flattens an error and its `cause` chain into one line. */
export function describeError(error: unknown): string {
  if (!(error instanceof Error)) return String(error);
  const chain: string[] = [];
  const seen = new Set<Error>();
  let current: Error | undefined = error;
  while (current !== undefined && !seen.has(current)) {
    seen.add(current);
    chain.push(current.message === "" ? current.name : current.message);
    current = current.cause instanceof Error ? current.cause : undefined;
  }
  return chain.join(" <- ");
}

/** Same chain as `describeError`, plus every stack in it. */
export function formatError(error: unknown): string {
  const parts = [describeError(error)];
  const seen = new Set<Error>();
  let current: unknown = error;
  while (current instanceof Error && !seen.has(current)) {
    seen.add(current);
    if (current.stack !== undefined) parts.push(current.stack);
    current = current.cause;
  }
  return parts.join("\n");
}

/** Hides credentials that endpoint providers embed in the RPC path or query. */
export function redactUrl(url: string): string {
  try {
    const parsed = new URL(url);
    const carriesSecret =
      (parsed.pathname !== "" && parsed.pathname !== "/") ||
      parsed.search !== "";
    return `${parsed.protocol}//${parsed.host}${carriesSecret ? "/<redacted>" : ""}`;
  } catch {
    return "<unparsable rpc url>";
  }
}

export function reportFatal(error: unknown): void {
  fatalReported = true;
  logSync(`FATAL ${formatError(error)}`);
}

/**
 * Generic over the callback's own return type; see the note on `RpcRunner.run`
 * in rpc.ts for why a `() => Promise<T>` signature breaks payload inference.
 */
export async function phase<P>(
  name: string,
  run: () => P,
): Promise<Awaited<P>> {
  log(`START ${name}`);
  const startedAt = Date.now();
  try {
    const result = await run();
    log(`DONE  ${name} in ${formatDuration(Date.now() - startedAt)}`);
    return result;
  } catch (error) {
    log(
      `FAIL  ${name} after ${formatDuration(Date.now() - startedAt)}:` +
        ` ${describeError(error)}`,
    );
    throw error;
  }
}

/**
 * Makes every process ending self-explanatory: a bare non-zero exit with no
 * preceding error is itself reported as an exit-path defect, so it never has to
 * be reproduced and bisected by hand.
 */
export function installExitLogging(): void {
  process.on("exit", (code: number) => {
    const total = formatDuration(Date.now() - processStartedAt);
    if (code !== 0 && !fatalReported) {
      logSync(
        `EXIT code ${code} after ${total} with no reported error —` +
          " this is a defect in the exit path, not a chain or endpoint problem",
      );
      return;
    }
    logSync(`EXIT code ${code} after ${total}`);
  });

  process.on("uncaughtException", (error: unknown) => {
    reportFatal(error);
    process.exit(1);
  });

  // An RPC call abandoned by its deadline can still reject later. That is
  // expected and must stay non-fatal, but it is always worth seeing.
  process.on("unhandledRejection", (reason: unknown) => {
    logSync(`WARN unhandled rejection: ${describeError(reason)}`);
  });

  for (const signal of ["SIGINT", "SIGTERM"] as const) {
    process.on(signal, () => {
      logSync(`EXIT on ${signal}`);
      process.exit(130);
    });
  }
}
