# Changelog

## 0.1.0 (unreleased)

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
- Commands on a stopped, paused or hibernated machine start or wake it first.
- Three example workflows (`examples/`): a quick start, a persistent
  workspace that hibernates between runs, and two forks working in parallel.
