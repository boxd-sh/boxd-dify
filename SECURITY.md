# Security

Please don't report security problems in public issues.

Report them privately through
[GitHub's vulnerability reporting](https://github.com/boxd-sh/boxd-dify/security/advisories/new),
or email [contact@boxd.sh](mailto:contact@boxd.sh). Include what you found, how
to reproduce it, and the version of the plugin and of Dify you use. We'll
confirm we received it and keep you posted while we fix it.

## Supported versions

Fixes go into the latest release.

## Scope

This repo holds the boxd tool plugin for Dify and its example workflows.
Issues in boxd itself, such as the API or the machines, can go to the same
addresses.

## What the plugin does with your key

The API key is stored by Dify in the plugin's credentials and sent only to the
boxd API. It is never sent into a machine and never part of a tool result or
an error message.

## Running agents safely

An agent or workflow with these tools can run any command on its machine. That
machine is a full VM with its own kernel, so a command can't reach your Dify
host, but it can reach the internet.

- Every machine the plugin creates or forks is isolated: it has no private
  network to your other boxd machines and holds no boxd credentials.
- Delete Machine only deletes machines the plugin created. Your other machines
  can't be deleted through it.
- Set **Delete after** on Create Machine and Fork Machine, so a machine a
  workflow forgets about deletes itself.
- Use **Fork Machine** before a risky step and keep working on the copy. The
  original stays as it was.
- A preview URL is public. Don't serve anything on it that needs a login it
  does not have.
- Give the tools only to models and users you trust with a Linux machine.

## What the plugin limits

Code on a machine is not trusted, so the plugin bounds what a machine can send
back into Dify's plugin process:

- Command output is cut inside the machine at 400,000 bytes per stream, and at
  100,000 characters in the result.
- Download File and Read File check a file's size before they transfer it and
  refuse anything over 50 MiB.
- A command is never run a second time unless the machine was stopped, paused
  or hibernated when it was sent, so it had not started.
- A machine the plugin creates or forks that does not become ready is deleted
  again. A machine made for a one-off run also has a one-hour delete timer.
