# Security policy

RackWalker talks to your NetBox and can write to it, so security reports are welcome.

## Reporting a vulnerability
Please report vulnerabilities privately with GitHub's **Report a vulnerability** button on the
[Security tab](https://github.com/JohnnyHBomb/rackwalker/security). Do not open a public issue.
Include steps to reproduce and the version or commit you tested.

You can expect an acknowledgement within a week. Fixes are released as soon as they are ready, and
reporters are credited unless they ask not to be.

## Scope
- The proxy (`proxy/`): authentication, sessions, NetBox token handling, and the change preview/apply
  path.
- The web game (`web/`): XSS through NetBox data or dialogue scripts.
- The dev NetBox kit (`devnetbox/`) is for local development with synthetic data only. Do not expose it to
  a network.

## Supported versions
Only the latest commit on `main` is supported until the first release.
