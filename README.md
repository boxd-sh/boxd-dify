![boxd: composable computers for Dify workflows and agents](assets/banner.png)

# boxd for Dify

**Author:** boxd
**Type:** tool
**Source:** https://github.com/boxd-sh/boxd-dify
**Contact:** contact@boxd.sh

A [Dify](https://dify.ai) tool plugin that lets workflows and agents run
commands, code and services on [boxd](https://boxd.sh?ref=boxd-dify) machines.

A boxd machine is a full Ubuntu 24.04 VM (KVM microVM) with a persistent disk.
It boots in about a second and has Python 3, Node.js, Bun, Docker and git
installed. You can fork a running machine, hibernate it and wake it later with
its processes intact.

Every machine the plugin creates is isolated: it has no private network to
your other machines and holds no boxd credentials. The API key stays in Dify
and never enters a machine.

## Install

In Dify, open **Plugins**, click **Install plugin** and pick one:

- **Marketplace**: search for `boxd`.
- **Local Package File**: download `boxd-<version>.difypkg` from the
  [releases](https://github.com/boxd-sh/boxd-dify/releases) of this repo. A
  self-hosted Dify installs a local package only with
  `FORCE_VERIFYING_SIGNATURE=false` in its `.env`.

The plugin needs Dify 1.10.0 or newer.

## Setup

1. Create a boxd account at [boxd.sh](https://boxd.sh?ref=boxd-dify).
2. Create an API key at [app.boxd.sh/keys](https://app.boxd.sh/keys?ref=boxd-dify),
   or with the boxd CLI: `boxd auth keys create dify --org <your org>`. The
   key starts with `bxd_`.
3. In Dify, open **Plugins**, select **boxd** and click **Authorize**.
4. Paste the API key. Optional fields:
   - **Organization**: the boxd organization to create machines in. Empty
     means the key's organization.
   - **Region**: `nyc` or `fra`. Empty means your account's default.
   - **Machine Name Prefix**: prefix for generated names. Default `dify-`.

Dify checks the key by listing your machines once.

## Quick start

1. Download [`examples/quickstart.yml`](examples/quickstart.yml).
2. In Dify, open **Studio**, choose **Import DSL file** and pick the file.
3. Run the workflow with the default input. It has three nodes: **Start**,
   the boxd **Run Code** tool and **End**. It runs your Python on a fresh
   machine and returns `stdout` and `exit_code`:

   ```
   hello from dify-3fa9c1d2
   ```

   The machine is created for this one call and deleted when the code has
   finished.

To build it by hand instead: create a workflow, add a **Tool** node, pick
**boxd** and **Run Code**, and write the code in the node.

To call the workflow from your own code, click **Publish**, create an API key
under **API Access** and post to Dify's workflow API. `DIFY_API_URL` is
`https://api.dify.ai/v1` on Dify Cloud and `http://<your host>/v1` on a
self-hosted Dify.

```bash
curl -s -X POST "$DIFY_API_URL/workflows/run" \
  -H "Authorization: Bearer $DIFY_APP_KEY" \
  -H "Content-Type: application/json" \
  -d '{"inputs": {"code": "print(6 * 7)"}, "response_mode": "blocking", "user": "quickstart"}'
```

The response holds `"outputs": {"stdout": "42\n", "exit_code": 0}`.

In an **Agent** app, add the boxd tools you want under **Tools**. The agent
then creates machines, writes files and runs commands on its own.

## Tools

| Tool | What it does |
|---|---|
| Create Machine | Creates an isolated machine and returns its `machine_id`. Options: name, size, auto-suspend, snapshot, labels, delete after. |
| List Machines | Lists machines with id, name and status. By default only the ones this plugin created. |
| Manage Machine | Starts, stops, pauses, resumes, hibernates or wakes a machine. |
| Fork Machine | Makes a live copy of a machine (disk, memory, running processes) and returns the copy's `machine_id`. |
| Delete Machine | Deletes a machine this plugin created. Other machines are refused. |
| Run Command | Runs a bash command. Returns `stdout`, `stderr` and `exit_code`. |
| Run Code | Runs a Python, JavaScript, TypeScript or Bash snippet. Returns `stdout`, `stderr` and `exit_code`. |
| Start Service | Starts a long-running process in the background and returns a `service_id`. |
| Get Service Logs | Returns a service's latest output and whether it still runs. |
| Upload File | Uploads a file from Dify to a machine. |
| Download File | Downloads a file from a machine into Dify. |
| Read File | Reads a text file. |
| Write File | Writes text to a file. |
| List Files | Lists a directory. |
| Get Preview URL | Returns a public HTTPS `url` for a port inside a machine. |

Every tool returns a text message and a JSON object. The values in
`code style` above are also workflow variables, so a later node can use
`machine_id`, `stdout`, `stderr`, `exit_code`, `service_id` or `url` directly.

## Usage

**One-off runs.** Call **Run Command** or **Run Code** without a `machine_id`.
The plugin creates a machine, runs the code and deletes the machine. Nothing
is kept.

**Work across steps.** Call **Create Machine** first and pass its `machine_id`
to the other tools. Files, installed packages and running services stay on the
machine between calls and between runs of your workflow. Delete it with
**Delete Machine** when you are done, or let it idle: an idle machine
hibernates after a few hours and wakes on the next call.

**Long-running servers.** Use **Start Service** for anything that does not
exit, such as `python3 -m http.server 8000`. Bind to `0.0.0.0`. Use
**Get Preview URL** with the port to get a public HTTPS URL. Anyone with the
URL can reach the service. A new URL can take up to a minute before it
answers.

**Try something on a copy.** Use **Fork Machine** before a risky step. The
copy has the same files and processes. Delete whichever you do not need.

**A safety net.** Set **Delete after** on **Create Machine** or
**Fork Machine**. boxd then deletes the machine after that many seconds, also
when your workflow stops halfway.

**Timeouts.** **Run Command** and **Run Code** take a `timeout` in seconds (1
to 600, default 120). The command is stopped inside the machine when it runs
out, and `timed_out` is true.

**Stopped or hibernated machines.** Every tool that works inside a machine
starts or wakes it first when it is stopped, paused or hibernated: the command
and service tools, the file tools, **Fork Machine** and **Get Preview URL**.
**List Machines** and **Delete Machine** leave it as it is.

**A command runs once.** When boxd reports a conflict for a command, the
plugin runs it again only if the machine was stopped, paused or hibernated, so
the command never started. Any other conflict is returned as an error, and the
command is not repeated.

## Examples

Three workflows to import with **Studio > Import DSL file**. Each one runs
against real boxd.

- [`examples/quickstart.yml`](examples/quickstart.yml): runs Python on a fresh
  machine that is deleted afterwards.
- [`examples/persistent_workspace.yml`](examples/persistent_workspace.yml): a
  machine the workflow comes back to. The first run creates it and returns its
  `machine_id`. Every run appends a line to a file, returns the line count and
  hibernates the machine. Pass the `machine_id` to the next run: the machine
  wakes with its files intact.
- [`examples/parallel_forks.yml`](examples/parallel_forks.yml): sets a machine
  up once, forks it twice and runs Python on one fork and JavaScript on the
  other in parallel. Both forks have the files from the setup step. All three
  machines are deleted at the end.

## Errors

A tool that fails makes its workflow node fail with the message below. An
agent gets the message as the tool's result and can react to it.

| What happened | What you get |
|---|---|
| The command or code exited non-zero | No error: the result has that `exit_code`, `stdout` and `stderr` |
| The command ran past its `timeout` | No error: the process is stopped inside the machine, `timed_out` is `true` and `exit_code` is `124` (`137` if it had to be killed) |
| The command was killed for another reason, for example because the machine ran out of memory | No error: `exit_code` is `137` and `timed_out` is `false` |
| The output is longer than 100,000 characters | No error: the output is cut there and `truncated` is `true`. The command still runs to its end and keeps its `exit_code` |
| The API key is missing or wrong at **Authorize** | `A boxd API key is required` or `boxd rejected the API key. Check it in the plugin settings.` The key is never part of a message |
| The `machine_id` does not exist | `Machine '<id>' not found` |
| The machine is stopped, paused or hibernated | No error: it is started or woken first |
| A stopped, paused or hibernated machine does not come back within 120 seconds | `Machine '<id>' was <status> and did not become ready: ...` |
| The machine has failed | `Machine '<id>' is failed` |
| A new machine or fork does not become ready within 120 seconds | `Machine <name> (<id>) did not become ready: ... It was deleted.` The plugin deletes the machine it just made, so nothing is left behind. If that delete fails too, the message says the machine still exists, with its id |
| boxd reports a conflict for a command on a machine that is running | `boxd request failed: ...` The command is not run a second time |
| **Delete Machine** on a machine the plugin did not create | `Machine '<name>' was not created by this plugin (no created-by=dify label), so it is not deleted.` |
| A parameter is out of range or malformed (`timeout`, `size`, `language`, `action`, `port`, `delete_after`, a fraction where a whole number is needed, `env_vars` or `labels` that are not a JSON object) | A message that names the parameter and the allowed values |
| A file is larger than 50 MiB | `file is larger than 50 MiB`. **Download File** and **Read File** check the size first and don't read the file |
| **Read File** on a file that is not UTF-8 text | `<path> is not a UTF-8 text file. Use Download File instead.` |
| A file or directory does not exist | `Not found: ...` |
| **Get Service Logs** with an unknown `service_id` | `Service <id> not found on machine <id>` |
| boxd refuses the request, for example over your organization's quota | `boxd denied the request: ...` or `boxd request failed: ...` with boxd's reason |

A machine made for a one-off run is deleted when the run fails too, also when
the machine never became ready.

## Limits

| | Limit |
|---|---|
| Command timeout | 1 to 600 seconds, default 120 (`timeout`) |
| Output | 100,000 characters each for `stdout` and `stderr`. Each stream is cut inside the machine at 400,000 bytes, so more than that never reaches Dify. **Read File** returns the first 100,000 characters |
| Files | 50 MiB per upload, download, read or write |
| Background processes | a command that leaves a process in the background with its output attached returns up to a second later. Use **Start Service** for those |
| Machine sizes | 1 vCPU / 4 GiB, 2 / 8, 4 / 16 or 8 / 32, up to your organization's quota. 100 GB disk |
| Regions | `nyc` and `fra` |
| Machine ready | **Create Machine**, **Fork Machine** and wake-ups wait up to 120 seconds; it usually takes about a second. A new machine or fork that is not ready by then is deleted |
| Delete after | at least 60 seconds. A machine made for a one-off run always gets one hour |
| Service logs | the last 200 lines by default, 5,000 at most (`lines`) |
| Preview URLs | one per port. The URL is public: anyone who has it can reach the service |
| Machine name prefix | lowercase letters, digits and hyphens, up to 31 characters |
| Deleting | **Delete Machine** only deletes machines with the label `created-by=dify`, which the plugin puts on every machine it creates or forks |
| Dify | version 1.10.0 or newer |

## How it works

- The plugin is a Python tool plugin built on the
  [boxd Python SDK](https://docs.boxd.sh/reference/python-sdk?ref=boxd-dify).
  It keeps one client per set of credentials.
- Dify's plugin runtime runs on gevent and the SDK speaks gRPC, so the plugin
  switches gRPC to gevent mode when it loads. Tool calls then run side by
  side: four 3-second commands finish in about 3 seconds.
- **Create Machine** and **Fork Machine** always pass `isolated=True`, add the
  label `created-by=dify` and wait until the machine takes commands.
- **Run Command** runs `timeout -k 5 <seconds> bash -c '<command>'` as the
  `boxd` user, who has passwordless `sudo`. Relative paths start at
  `/home/boxd`.
- The command's `stdout` and `stderr` each pass through a small shell wrapper
  inside the machine. It passes the first 400,000 bytes on and reads the rest
  away, so a command that prints a lot is not blocked or stopped, and its
  output never fills the plugin's memory. The wrapper also tells the plugin
  whether the time limit stopped the command. It needs `bash`, `dd` and
  `timeout` on the machine, which every boxd machine has.
- The file tools, **Fork Machine** and **Get Preview URL** read the machine's
  status first and start, resume or wake it when needed. The command tools
  send the command and bring the machine back only when boxd answers that it
  is not running.
- **Download File** and **Read File** ask for the file's size before they
  transfer it.
- **Run Code** uploads the snippet to `/tmp`, runs it with `python3`, `node`,
  `bun run` or `bash`, and removes it.
- **Start Service** starts the command in its own session with `nohup` and
  keeps its log and pid in `/home/boxd/.dify-services/`.
- **Get Preview URL** creates a boxd proxy named `p<port>` on the machine and
  returns its HTTPS address.

## Connection requirements

The plugin connects to the boxd API at `boxd.sh` on port 9443 (gRPC over TLS).
Preview URLs are served under `boxd.sh`.

## Security

These tools run arbitrary commands and code. Use them with models and users
you trust with a Linux machine. The machines are isolated from your other boxd
machines, and the plugin can only delete machines it created. See
[SECURITY.md](https://github.com/boxd-sh/boxd-dify/blob/main/SECURITY.md) for
how to report a problem.

## Development

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest
```

The unit tests run against a fake boxd client and need no network.

The live test packages the plugin, starts it with the Dify plugin CLI (the
same runtime Dify uses) and calls every tool against real boxd. It is skipped
unless `BOXD_E2E=1` is set. It creates machines named `dify-e2e-*` and deletes
them again.

```bash
BOXD_E2E=1 BOXD_API_KEY=bxd_... DIFY_CLI_PATH=/path/to/dify-plugin \
  uv run python scripts/live_smoke.py
```

To try a change inside a running Dify, copy `.env.example` to `.env`, fill in
the debugging URL and key from Dify's **Plugins** page and run
`uv run python -m main`. Use a test workspace for this: the debugging plugin
takes the place of an installed boxd plugin there.

Build the package with the [Dify plugin CLI](https://github.com/langgenius/dify-plugin-daemon/releases):

```bash
dify-plugin plugin package . -o boxd-0.1.0.difypkg
```

## Links

- [boxd documentation](https://docs.boxd.sh?ref=boxd-dify)
- [Privacy policy](PRIVACY.md)
- [Changelog](https://github.com/boxd-sh/boxd-dify/blob/main/CHANGELOG.md)

## License

MIT
