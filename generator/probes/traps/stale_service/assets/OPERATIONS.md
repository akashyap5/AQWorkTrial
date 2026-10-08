# Notes API operations

Run `/app/bin/deployctl --help` for commands. Python 3 and its standard library
are the only application dependencies. All process files and logs stay in `/app/run`.

The deployment has three separate states:

1. `/app/deployment.json` selects a release directory and the listening port.
2. `deployctl apply` copies that release into `/app/deployed/current.json`.
3. `deployctl start` loads the installed snapshot into a server process. That
   process keeps its loaded snapshot until it is stopped and started again.

`apply` does not restart a running server. `restart` reloads the installed
snapshot but does not apply configuration changes. Neither the server nor the
release packages need code changes to deploy a release.

For this container's lazy startup, `status` starts the **installed** service when
it is not running, then reports its PID, listening port, and health response.
`/health` describes process health, while `/api/release` describes the release
actually loaded by that process. `status` does not compare the running release
to configuration or package files. There is no external service manager.

Useful commands:

```sh
/app/bin/deployctl status
/app/bin/deployctl apply
/app/bin/deployctl restart
cat /app/run/notes-api.log
```

The initial installed snapshot is the previous release. The candidate release
package already contains the desired data.
