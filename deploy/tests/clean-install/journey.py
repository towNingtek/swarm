# Customer journey on a freshly installed host, driven like a browser.
# Prints status codes and non-secret facts only. Run by run.sh.
import json, secrets, subprocess, sys, time, uuid
import httpx

P = "https://platform.example.com"
ADMIN_PW = open("/etc/swarm/secrets/admin-password").read().strip()
H = {"Origin": P, "Sec-Fetch-Site": "same-origin"}
# Throwaway credentials, generated per run.
ALICE = {"username": "alice", "password": secrets.token_urlsafe(18)}
BOB = {"username": "bob", "password": secrets.token_urlsafe(18)}

def step(name, r, want):
    ok = r.status_code in (want if isinstance(want, tuple) else (want,))
    print(f"{'ok ' if ok else 'BAD'} {name}: {r.status_code}")
    if not ok:
        print("   ", r.text[:300]); sys.exit(1)
    return r

admin = httpx.Client(base_url=P, headers=H, follow_redirects=False, timeout=180)
step("admin wrong password", admin.post("/admin/login", data={"password": "nope-" + "x" * 20}), 401)
step("admin login", admin.post("/admin/login", data={"password": ADMIN_PW}), (302, 303))
step("admin customers page", admin.get("/admin/customers"), 200)
t = step("create tenant", admin.post("/admin/tenants", json={"client_request_id": str(uuid.uuid4()), "host": "acme.example.com"}), (200, 201)).json()
tid = t["tenant"]["id"] if isinstance(t.get("tenant"), dict) else t.get("tenant_id")
step("quota", admin.post(f"/admin/tenants/{tid}/quota", json={"policy": {"mode": "capped", "monthly_limit": 100000}}), 200)
inv = step("invite", admin.post(f"/admin/tenants/{tid}/invites", json={"ttl_seconds": 3600}), 201).json()
token = inv.get("token") or inv.get("invite")
step("queue site build", admin.post(f"/admin/tenants/{tid}/site-jobs", json={"client_request_id": str(uuid.uuid4())}), (200, 201, 202))

cust = httpx.Client(base_url=P, headers=H, follow_redirects=False, timeout=180)
step("landing with invite", cust.get("/", params={"token": token}), 200)
step("activate", cust.post("/customer/activate", json={"invite": token, **ALICE}), (200, 201))
step("invite cannot be reused", httpx.Client(base_url=P, headers=H).post("/customer/activate", json={"invite": token, **BOB}), (400, 401, 409))
step("me", cust.get("/customer/me"), 200)

deadline = time.time() + 900
while True:
    st = cust.get("/customer/onboarding/status").json()
    job = st.get("site_job") or st.get("site") or {}
    state = job.get("state") or job.get("status") if isinstance(job, dict) else job
    if state in ("succeeded_provisioned", "failed") or str(state).startswith("failed") or time.time() > deadline:
        break
    print("    ...", state, flush=True); time.sleep(10)
print("    site job:", state)
if state != "succeeded_provisioned":
    print(json.dumps(st)[:800]); sys.exit(1)

url = step("site entry", cust.post("/customer/site-entry", json={}), (200,)).json()["url"]
print("    entry host:", httpx.URL(url).host, "path:", httpx.URL(url).path)
site = httpx.Client(follow_redirects=False, timeout=180)
r = step("enter with ticket", site.get(url), (303,))
assert r.headers["location"].startswith("/auth/handoff"), r.headers["location"]
step("a spent ticket is refused", httpx.Client(follow_redirects=False, timeout=180).get(url), (401,))
S = "https://acme.example.com"
step("handoff page", site.get(S + "/auth/handoff?next=%2F"), (200,))
step("handoff script", site.get(S + "/auth/handoff.js"), (200,))
step("cross-origin exchange refused", site.post(S + "/auth/native-session", content="{}", headers={"Content-Type": "application/json", "Origin": "https://evil.example.net"}), (403,))
step("native session exchange", site.post(S + "/auth/native-session", content="{}", headers={"Content-Type": "application/json", "Origin": S, "Sec-Fetch-Site": "same-origin"}), (204,))
r = step("site app with session", site.get(S + "/"), (200,))
assert "__DSH_BOOT__" in r.text, "not the DSH app"
step("the site refuses anonymous visitors", httpx.Client(follow_redirects=False, timeout=180).get(S + "/"), (401,))

# Inside the site: what its AI can and cannot reach.
probe = subprocess.run(["docker", "exec", "-i", "site-acme", "node", "-"], input=open("/root/clean-install/in_site.mjs").read(),
                       capture_output=True, text=True, timeout=300)
print(probe.stdout.rstrip())
if probe.returncode != 0:
    print(probe.stderr[:1500]); sys.exit(1)
step("platform logout", cust.post("/customer/logout", json={}), (200, 204))
step("session gone after logout", cust.get("/customer/me"), (401,))
step("delete refuses a wrong host", admin.post(f"/admin/tenants/{tid}/delete", json={"expected_host": "wrong.example.com"}), (409,))
step("delete tenant", admin.post(f"/admin/tenants/{tid}/delete", json={"expected_host": "acme.example.com"}), (200,))
left = subprocess.run(["docker", "ps", "-aq", "--filter", "name=site-acme"], capture_output=True, text=True).stdout.strip()
assert not left, "site container left behind"
import os, glob
assert not os.path.exists("/var/lib/swarm/sites/acme"), "site directory left behind"
assert not glob.glob("/etc/nginx/conf.d/swarm-site-*"), "nginx config left behind"
print("ok  delete removed container, files and nginx config")
print("JOURNEY OK")
