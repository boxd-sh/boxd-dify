## Privacy Policy for the boxd Dify Plugin

The plugin connects Dify to the boxd API (`boxd.sh`) to create and use boxd machines. It sends data only to boxd. It does not collect personal data for itself and keeps nothing after a call ends.

### Data the plugin sends to boxd

- **API key**: stored by Dify in the plugin credentials and sent to boxd to authenticate each request. It is never sent into a machine and never included in tool output or error messages.
- **Organization, region and name prefix**: the optional settings you enter.
- **Commands and code**: the commands and code you or your agent run, and their environment variables, are sent to boxd and run inside your machine.
- **Files and text**: files you upload and text you write go into the machine. Files you download or read come back from the machine into Dify.
- **Machine settings**: names, sizes, labels and other options you choose when creating or forking a machine.

### Data the plugin returns to Dify

Machine ids, names and status, command output (stdout, stderr, exit code), file contents you request and preview URLs.

### Storage

The plugin itself stores nothing. Data inside a machine stays on its disk until you delete the machine. boxd processes account and machine data under its own [privacy policy](https://boxd.sh/privacy?ref=boxd-dify).

### Third parties

The plugin talks only to boxd. Code you run inside a machine can reach the internet; what it sends is up to that code.

### Contact

contact@boxd.sh
