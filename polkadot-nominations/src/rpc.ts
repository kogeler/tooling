import { describeError } from "./log.js";

const RETRY_BASE_DELAY_MS = 500;

export interface RpcLimits {
  readonly requestTimeoutMs: number;
  readonly requestRetries: number;
  readonly maxConcurrency: number;
}

export interface RpcStats {
  readonly started: number;
  readonly completed: number;
  readonly failed: number;
  readonly retried: number;
  readonly inFlight: number;
  readonly queued: number;
}

/**
 * `run` is generic over the callback's own return type rather than over a
 * `Promise<T>` shape. Contextually typing the callback as `() => Promise<T>`
 * makes TypeScript collapse polkadot-api storage payloads to `{}` when several
 * wrapped calls share one `Promise.all` array literal.
 */
export interface RpcRunner {
  readonly run: <P>(operation: string, call: () => P) => Promise<Awaited<P>>;
  readonly stats: () => RpcStats;
}

export class RpcTimeoutError extends Error {
  constructor(operation: string, timeoutMs: number) {
    super(`"${operation}" produced no response within ${timeoutMs} ms`);
    this.name = "RpcTimeoutError";
  }
}

export class RpcRequestError extends Error {
  constructor(operation: string, attempts: number, cause: unknown) {
    super(
      `"${operation}" failed after ${attempts} attempt(s): ${describeError(cause)}`,
      { cause },
    );
    this.name = "RpcRequestError";
  }
}

export function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

/**
 * Rejects with RpcTimeoutError when `call` produces no result in time. The
 * underlying request cannot be cancelled, so it is only abandoned; callers that
 * treat a timeout as fatal must terminate the process themselves.
 */
export async function withTimeout<P>(
  operation: string,
  timeoutMs: number,
  call: () => P,
): Promise<Awaited<P>> {
  if (timeoutMs <= 0) return await call();

  let timer: NodeJS.Timeout | undefined;
  try {
    return await Promise.race([
      (async () => await call())(),
      new Promise<never>((_resolve, reject) => {
        timer = setTimeout(() => {
          reject(new RpcTimeoutError(operation, timeoutMs));
        }, timeoutMs);
      }),
    ]);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}

/**
 * Hands out at most `limit` slots. Releasing passes the slot straight to the
 * next waiter, so the active count never exceeds the limit.
 */
function createSemaphore(limit: number): () => Promise<() => void> {
  const waiting: Array<() => void> = [];
  let active = 0;

  const release = (): void => {
    const next = waiting.shift();
    if (next === undefined) {
      active -= 1;
    } else {
      next();
    }
  };

  return async (): Promise<() => void> => {
    if (active < limit) {
      active += 1;
    } else {
      await new Promise<void>((resolve) => {
        waiting.push(resolve);
      });
    }
    return release;
  };
}

export interface RpcRunnerHooks {
  readonly onRetry?: (
    operation: string,
    attempt: number,
    error: unknown,
  ) => void;
}

export function createRpcRunner(
  limits: RpcLimits,
  hooks: RpcRunnerHooks = {},
): RpcRunner {
  const acquire = createSemaphore(Math.max(1, limits.maxConcurrency));
  const attempts = Math.max(1, limits.requestRetries + 1);
  let started = 0;
  let completed = 0;
  let failed = 0;
  let retried = 0;
  let inFlight = 0;
  let queued = 0;

  const run = async <P>(
    operation: string,
    call: () => P,
  ): Promise<Awaited<P>> => {
    queued += 1;
    const release = await acquire();
    queued -= 1;
    started += 1;
    inFlight += 1;
    try {
      let lastError: unknown;
      for (let attempt = 1; attempt <= attempts; attempt += 1) {
        try {
          const result = await withTimeout(
            operation,
            limits.requestTimeoutMs,
            call,
          );
          completed += 1;
          return result;
        } catch (error) {
          lastError = error;
          if (attempt === attempts) break;
          retried += 1;
          hooks.onRetry?.(operation, attempt, error);
          await delay(RETRY_BASE_DELAY_MS * 2 ** (attempt - 1));
        }
      }
      failed += 1;
      throw new RpcRequestError(operation, attempts, lastError);
    } finally {
      inFlight -= 1;
      release();
    }
  };

  return {
    run,
    stats: () => ({ started, completed, failed, retried, inFlight, queued }),
  };
}

async function runDirect<P>(
  _operation: string,
  call: () => P,
): Promise<Awaited<P>> {
  return await call();
}

/** Passes calls straight through; used as the default in unit tests. */
export const directRunner: RpcRunner = {
  run: runDirect,
  stats: () => ({
    started: 0,
    completed: 0,
    failed: 0,
    retried: 0,
    inFlight: 0,
    queued: 0,
  }),
};

export interface ProgressOptions {
  readonly label: string;
  readonly intervalMs: number;
  readonly runner: RpcRunner;
  readonly log: (message: string) => void;
}

/**
 * Prints a periodic line while long scans are in flight so a stalled RPC is
 * visible instead of looking like slow progress. Returns a stop function.
 */
export function startProgress({
  label,
  intervalMs,
  runner,
  log,
}: ProgressOptions): () => void {
  if (intervalMs <= 0) return () => undefined;

  const startedAt = Date.now();
  let previousCompleted = runner.stats().completed;

  const timer = setInterval(() => {
    const stats = runner.stats();
    const elapsedSec = Math.round((Date.now() - startedAt) / 1000);
    const done = stats.completed - previousCompleted;
    previousCompleted = stats.completed;
    const idleNote =
      done === 0 && stats.inFlight > 0 ? " (no response since last tick)" : "";
    log(
      `[${label}] ${elapsedSec}s | rpc done ${stats.completed}/${stats.started}` +
        ` (+${done}) | in flight ${stats.inFlight} | queued ${stats.queued}` +
        ` | retries ${stats.retried} | failed ${stats.failed}${idleNote}`,
    );
  }, intervalMs);
  timer.unref();

  return () => {
    clearInterval(timer);
  };
}
