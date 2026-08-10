export type RunnableCommand =
  | "nominations"
  | "self-stake-stats"
  | "validator-info";
export type CliCommand = RunnableCommand | "help";

export const COMMANDS: ReadonlyMap<string, RunnableCommand> = new Map([
  ["--nominations", "nominations"],
  ["--self-stake-stats", "self-stake-stats"],
  ["--validator-info", "validator-info"],
]);

export const USAGE = `Usage: node dist/index.js <command>

Commands:
  --nominations       Nomination reports for validators from config.json
  --self-stake-stats  Network-wide validator self-stake statistics
  --validator-info    Validator stake, actual rewards, and reward projections
  --help              Show this help`;

export function parseCliArgs(args: readonly string[]): CliCommand {
  const argument = args[0];
  if (args.length === 1 && (argument === "--help" || argument === "-h")) {
    return "help";
  }

  const command = argument === undefined ? undefined : COMMANDS.get(argument);
  if (args.length !== 1 || command === undefined) {
    throw new Error(`Exactly one command is required.\n\n${USAGE}`);
  }

  return command;
}
