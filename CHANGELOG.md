# Changelog

## 0.1.0 (2026-11-11)

Initial release.

- A Dify tool plugin with 15 tools on boxd machines:
  - machines: Create Machine, List Machines, Manage Machine (start, stop,
    pause, resume, hibernate, wake), Fork Machine, Delete Machine;
  - running things: Run Command, Run Code (Python, JavaScript, TypeScript,
    Bash), Start Service, Get Service Logs;
  - files: Upload File, Download File, Read File, Write File, List Files;
  - network: Get Preview URL.
- Run Command and Run Code work without a `machine_id`: the plugin creates a
  machine for that one call and deletes it afterwards.
- Every machine the plugin creates or forks is isolated and labelled
  `created-by=dify`. Delete Machine refuses any other machine.
- Create Machine and Fork Machine take `delete_after`, so boxd deletes the
  machine after that many seconds whatever happens to the workflow.
- `machine_id`, `stdout`, `stderr`, `exit_code`, `service_id` and `url` are
  workflow variables, next to each tool's text and JSON output.
- Every tool that works inside a machine starts or wakes it first when it is
  stopped, paused or hibernated: commands, services, files, Fork Machine and
  Get Preview URL.
- A command runs once. It is sent again only when the machine was stopped,
  paused or hibernated, so it had not started.
- Command output is cut inside the machine (400,000 bytes per stream, 100,000
  characters in the result), and the command keeps its exit code.
- Download File and Read File check the size before they transfer a file and
  refuse anything over 50 MiB.
- A new machine or fork that does not become ready is deleted again, and the
  error names it.
- `timed_out` is true only when the time limit stopped the command, not for
  every exit code 137.
- Three example workflows (`examples/`): a quick start, a persistent
  workspace that hibernates between runs, and two forks working in parallel.
