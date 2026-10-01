# platform/

The platform site: where customers are invited, sign in, talk to the copilot and support, and are handed into their own site. Operators manage customers and sites from `/admin`.

Three processes share one SQLite database:

| Service | Entry point | Binds | Job |
|---|---|---|---|
| Platform | `platform_service.py` | `127.0.0.1:8210` | Invitations, login, copilot, support rooms, single-use entry into a site |
| Admin console | `admin_service.py` | `127.0.0.1:8211` | Operator login, customers, invites, site builds, password reset, deletion |
| Site model relay | `site_model_relay.py` | Docker bridge `:8212` | The only model endpoint sites can reach. Each site has its own key; the platform key never leaves the host |

Put a reverse proxy with TLS in front of the first two (same origin; admin under `/admin`). The relay is reached only from site containers.

Site work (create, delete, password, isolation) is done by `../site/dsh_sitectl.py`; `_site_path.py` puts `site/` on the import path. Set `SWARM_SITE_DIR` if it lives elsewhere.

## Settings

Each entry point documents its variables at the top of the file. The essentials:

| Variable | Used by | Meaning |
|---|---|---|
| `PLATFORM_ORIGIN` | platform, admin | `https://` origin customers use |
| `PLATFORM_DB` | all | Absolute path of the SQLite database |
| `ADMIN_ORIGIN`, `ADMIN_PASSWORD` | admin | Admin origin (normally the same as the platform) and a 16+ character password |
| `PLATFORM_PROVISION` | platform | `1` to really build sites; off by default |
| `SWARM_DOMAIN` | all | Parent domain for sites |
| `SWARM_ENTRY_SECRET` | platform, site | Master secret for single-use entry tickets |
| `PLATFORM_MODEL_BASE_URL`, `PLATFORM_MODEL_KEY` | relay | Upstream OpenAI-compatible gateway and its key |

`platform_admin_cli.py` is a small command line for the same database (add a customer, mint an invite, set up a site's starter model).

## Tests

```sh
pip install -r ../requirements.txt
./run_tests.sh
```

The UI tests run small scripts under Node 22. `platform_isolated_fixture.py` starts the whole app with fake runners and no real provisioning; tests use it to walk a customer journey end to end.
