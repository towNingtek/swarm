# Fork note

This directory is a modified copy of [`@summersec/dsh-web-auth`](https://www.npmjs.com/package/@summersec/dsh-web-auth) **0.2.0** (MIT). The original copyright and license are kept in [LICENSE](LICENSE).

We plan to send these changes upstream. Once they are merged, Swarm will install the npm release and this directory will be removed.

## What changed

| Change | Why |
|---|---|
| `renderIndex()` on the auth web server | DSH `dsh-host-frontend-static` calls the stock web server's `renderIndex`; without it the GUI returns `bad_request` after login |
| Tokenless native handoff (`/auth/handoff`, `/auth/native-session`) | After the password login, the browser gets DSH's own session cookie through a same-origin exchange; the DSH launch token never appears in a URL |
| Single-use entry tickets (`entrySecret`, `/auth/enter`) | The platform lets a customer into their site without the customer knowing the site password. Tickets are HMAC-signed, expire in seconds and are burned on first use |
| `publicPrefixes` | Lets one registered plugin route (for example `/share` from dsh-share-room) serve guests without a login. Only one path segment, reserved paths refused, and only prefix routes registered inside it may answer |
| Remote-page ownership hint | A site is served on a public hostname, so the page declares that it owns the Host. Without that, DSH hides the settings |
| Branding | Swarm favicon (`src/assets/swarm-favicon.svg`), login page title and Traditional Chinese messages |
| `auth.js` | Hardening of the session store, the attempt limiter and return-path checks used by the above |

To see the exact diff:

```sh
npm pack @summersec/dsh-web-auth@0.2.0 && tar xzf summersec-dsh-web-auth-0.2.0.tgz
diff -ru package site/dsh-profile/vendor/dsh-web-auth
```
