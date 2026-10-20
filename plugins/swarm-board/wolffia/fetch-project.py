#!/usr/bin/env python3
"""Read GitHub Projects via gh and write data files for v7.html / v8.html.

Usage:
  python3 fetch-project.py [OUT_DIR]       # fetch every project listed in projects.json
  python3 fetch-project.py OUT_FILE.js 3   # legacy: fetch one project number into one file

projects.json lists which projects the galaxy can show; edit it to add or remove one.
An entry is an integer (a project number of the default org, WOLFFIA_ORG) or "org/number" for another org's project,
e.g. "acme/1"; --one takes the same forms.
"publish" is where the old cron signature (a single .js argument) publishes every project.
Each project is written as data/p<N>.js (integer entries) or data/p-<org>-<N>.js ("org/N" entries),
plus data/index.js with the list (each row: num, owner, key = the projects.json entry, title, open, total).

Before fetching, scheduler/gh_budget.py checks the GitHub API budget (#485): below 20% left the whole
run is skipped (old data files stay, exit 0). If the budget cannot be read, fetch as usual.
"""
import fcntl, json, os, subprocess, sys, datetime as D
ORG = (os.environ.get("WOLFFIA_ORG") or "").lower()  # owner of integer entries in projects.json
HERE = os.path.dirname(os.path.abspath(__file__))
Q = '''query($org:String!,$num:Int!,$after:String){organization(login:$org){projectV2(number:$num){title url
 items(first:100,after:$after){pageInfo{hasNextPage endCursor} nodes{
  content{... on Issue{number title url state createdAt closedAt updatedAt repository{name owner{login}}
   assignees(first:5){nodes{login}} labels(first:10){nodes{name}}
   issueType{name} parent{url} blockedBy(first:20){nodes{url}}}}
  fieldValueByName(name:"Status"){... on ProjectV2ItemFieldSingleSelectValue{name updatedAt}}
  target: fieldValueByName(name:"Target date"){... on ProjectV2ItemFieldDateValue{date}}}}}}}'''
ORDER = ["待辦", "進行中 (7 天一輪)", "審核後請關閉", "完成"]
H = """query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){issue(number:$n){
 timelineItems(first:100,itemTypes:[PROJECT_V2_ITEM_STATUS_CHANGED_EVENT,CLOSED_EVENT,REOPENED_EVENT]){nodes{__typename
  ... on ProjectV2ItemStatusChangedEvent{createdAt status project{number url}}
  ... on ClosedEvent{createdAt} ... on ReopenedEvent{createdAt}}}}}}"""


class FetchError(Exception):
    """GitHub could not be read: gh failed, or its answer is not what we expect."""


def parse_key(x):
    """projects.json entry -> (owner, num, key). key is the int for the default org, else "org/num" (org lowercased:
    GitHub logins are case-insensitive). "<default org>/2" is the same project as 2. None for anything else."""
    if isinstance(x, bool):
        return None
    if isinstance(x, int):
        return (ORG, x, x) if x > 0 else None
    if not isinstance(x, str):
        return None
    x = x.strip()
    if x.isdigit():
        return parse_key(int(x))
    org, _, num = x.partition("/")
    org = org.lower()
    if not (num.isdigit() and int(num) > 0 and 0 < len(org) <= 39 and all(c.isalnum() or c == "-" for c in org)
            and org.isascii() and not org.startswith("-")):
        return None
    if org == ORG:
        return (ORG, int(num), int(num))
    return (org, int(num), "%s/%d" % (org, int(num)))


def keys_of(conf):
    """The valid entries of conf["projects"] as (owner, num, key), in order, without duplicates."""
    out, seen = [], set()
    for x in conf.get("projects") or []:
        k = parse_key(x)
        if k and k[2] not in seen:
            seen.add(k[2])
            out.append(k)
    return out


def file_base(key):
    """Data file name without .js: p2 for the default org's 2, p-acme-1 for "acme/1".
    Org names hold only letters, digits and "-", and the number is the part after the last "-", so names never collide."""
    if isinstance(key, int):
        return "p%d" % key
    org, num = key.split("/")
    return "p-%s-%s" % (org, num)


def data_js(key, d):
    return "window.PROJECTS=window.PROJECTS||{};window.PROJECTS[%s]=%s;\n" % (json.dumps(key), json.dumps(d, ensure_ascii=False))


def index_row(owner, num, key, d):
    return {"num": num, "owner": owner, "key": key, "title": d["title"],
            "open": sum(1 for i in d["items"] if i["state"] == "OPEN"), "total": len(d["items"])}


def row_key(r):
    """Key of an index.js row; rows written before "key" existed are default-org numbers."""
    return r.get("key", r.get("num")) if isinstance(r, dict) else None


def gql(query, **v):
    cmd = ["gh", "api", "graphql", "-f", "query=" + query]
    for k, x in v.items():
        cmd += ["-F" if isinstance(x, int) else "-f", "%s=%s" % (k, x)]
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise FetchError("cannot run gh: %s" % e)
    if r.returncode != 0:
        raise FetchError("gh exited %d: %s" % (r.returncode, r.stderr.decode("utf-8", "replace").strip()[:500]))
    try:
        d = json.loads(r.stdout)
    except ValueError as e:
        raise FetchError("gh returned non-JSON: %s" % e)
    if not isinstance(d, dict) or not isinstance(d.get("data"), dict) or d.get("errors"):
        raise FetchError("gh returned no data: %s" % str(d.get("errors") if isinstance(d, dict) else d)[:500])
    return d["data"]


def hist(c, num, owner=ORG):
    """Return [[time, status]] for this project only; closed/reopened become "closed"/"reopen".

    Batched project queries silently drop nested timeline events, so each issue is queried alone.
    """
    # Issues can live in repos of other orgs (e.g. another-org/repo), so use the issue's own owner.
    nodes = gql(H, o=c["repository"]["owner"]["login"], r=c["repository"]["name"], n=c["number"])["repository"]["issue"]["timelineItems"]["nodes"]
    h = []
    for e in nodes:
        k = e["__typename"]
        if k == "ProjectV2ItemStatusChangedEvent":
            pj = e.get("project") or {}
            # Another org's project can have the same number: its url says whose it is.
            mine = not pj.get("url") or ("/%s/projects/" % owner) in pj["url"].lower().replace("/orgs/", "/").replace("/users/", "/")
            if pj.get("number") == num and mine and e.get("status"):
                h.append([e["createdAt"], e["status"]])
        elif k == "ClosedEvent":
            h.append([e["createdAt"], "closed"])
        elif k == "ReopenedEvent":
            h.append([e["createdAt"], "reopen"])
    return sorted(h)


def load_cache(path):
    """{url: {"key", "hist"}} from the history cache; a missing or broken cache is just empty."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(d, dict):
        return {}
    return {u: e for u, e in d.items() if isinstance(e, dict) and isinstance(e.get("key"), str) and isinstance(e.get("hist"), list)}


def fetch(num, cache_dir=None, owner=ORG, pkey=None):
    """owner: the org owning project num; pkey: its projects.json key (default num).
    cache_dir: where .hist-cache lives; an issue whose updatedAt and Status updatedAt are unchanged
    reuses its cached history instead of a timeline query (one query per issue is most of the API cost)."""
    pkey = num if pkey is None else pkey
    cpath = os.path.join(cache_dir, ".hist-cache", file_base(pkey) + ".json") if cache_dir else None
    cache = load_cache(cpath) if cpath else {}
    fresh, queries = {}, 0
    opts = gql('query($org:String!,$num:Int!){organization(login:$org){projectV2(number:$num){field(name:"Status"){'
               '... on ProjectV2SingleSelectField{options{name}}}}}}', org=owner, num=num)
    cols = {o["name"] for o in opts["organization"]["projectV2"]["field"]["options"]}
    items, after, title, url = [], None, "", ""
    while True:
        v = {"org": owner, "num": num}
        if after:
            v["after"] = after
        p = gql(Q, **v)["organization"]["projectV2"]
        title, url = p["title"], p["url"]
        for n in p["items"]["nodes"]:
            c, s = n["content"], n["fieldValueByName"] or {}
            if not c:
                continue  # draft issue or pull request
            key = "%s|%s" % (c.get("updatedAt") or "", s.get("updatedAt") or "")
            hit = cache.get(c["url"])
            if hit and hit["key"] == key:
                h = hit["hist"]
            else:
                h, queries = hist(c, num, owner), queries + 1
            fresh[c["url"]] = {"key": key, "hist": h}
            items.append({"n": c["number"], "t": c["title"], "u": c["url"], "repo": c["repository"]["name"], "owner": c["repository"]["owner"]["login"],
                          "state": c["state"], "created": c["createdAt"], "closed": c["closedAt"],
                          "status": s.get("name"), "since": s.get("updatedAt"), "hist": h,
                          "who": [a["login"] for a in c["assignees"]["nodes"]],
                          "labels": [l["name"] for l in c["labels"]["nodes"]],
                          # EPIC graph (contract: discussion.md「issue 契約」). Empty for issues that do not use it.
                          "type": (c.get("issueType") or {}).get("name"),
                          "parent": (c.get("parent") or {}).get("url"),
                          "blockedBy": [b["url"] for b in (c.get("blockedBy") or {}).get("nodes", [])],
                          "target": (n.get("target") or {}).get("date")})
        if not p["items"]["pageInfo"]["hasNextPage"]:
            break
        after = p["items"]["pageInfo"]["endCursor"]
    print("%s timeline queries: %d / %d" % (file_base(pkey), queries, len(items)))
    if cpath:
        os.makedirs(os.path.dirname(cpath), exist_ok=True)
        write_atomic(cpath, json.dumps(fresh, ensure_ascii=False))  # only issues still on the board
    return {"num": num, "owner": owner, "key": pkey, "cols": [c for c in ORDER if c in cols], "title": title, "url": url,
            "fetched": D.datetime.now(D.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "items": items}


def safe_fetch(num, cache_dir=None, owner=ORG, key=None):
    """fetch() that turns any failure (network, gh exit, bad shape) into FetchError."""
    try:
        return fetch(num, cache_dir, owner, key)
    except FetchError:
        raise
    except (KeyError, TypeError, AttributeError, ValueError) as e:
        raise FetchError("unexpected response shape for project %s/%d: %r" % (owner, num, e))


def write_atomic(path, text):
    """Write via a temp file so the page never reads a half-written file."""
    tmp = "%s.%d.tmp" % (path, os.getpid())  # unique: --one may run beside the cron fetch
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def fetch_or_die(num, cache_dir=None, owner=ORG, key=None):
    """Single-project modes: on failure leave the existing file alone and exit non-zero."""
    try:
        return safe_fetch(num, cache_dir, owner, key)
    except FetchError as e:
        print("failed", num if key is None else key, e, file=sys.stderr)
        sys.exit(1)


def budget_low():
    """True when gh_budget says the GitHub API budget is below its threshold; never raises."""
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scheduler"))
    try:
        import gh_budget
    except ImportError as e:
        print("gh_budget unavailable, fetching anyway:", e, file=sys.stderr)
        return False
    b = gh_budget.check(who="fetch-project")
    if b["low"]:
        print("額度偏低，跳過到 %s (keeping old data)" % b["reset_at"])
    return b["low"]


def read_index(path):
    """Rows of an existing index.js; [] when missing or broken."""
    try:
        rows = json.loads(open(path, encoding="utf-8").read().split("=", 1)[1].rstrip(";\n"))["projects"]
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return []
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def write_index(path, conf, rows):
    keys = [k[2] for k in keys_of(conf)]
    rows = [r for r in rows if row_key(r) in keys]
    rows.sort(key=lambda r: keys.index(row_key(r)))
    default = parse_key(conf.get("default", keys[0] if keys else None))
    write_atomic(path, "window.PROJECT_INDEX=" + json.dumps({"org": ORG, "default": default[2] if default else (keys[0] if keys else None),
                 "projects": rows}, ensure_ascii=False) + ";\n")


def update_index(out, conf, key, row):
    """Keep data/index.js in step after a --one refresh: replace this project's row, keep the others.
    The board page needs index.js to list projects; a site that only ever runs --one would have none."""
    path = os.path.join(out, "index.js")
    write_index(path, conf, [r for r in read_index(path) if row_key(r) != key] + [row])


def main():
    if budget_low():
        sys.exit(0)
    # WOLFFIA_PROJECTS: path of projects.json (the DSH site plugin keeps it next to its data dir).
    conf = json.load(open(os.environ.get("WOLFFIA_PROJECTS") or os.path.join(HERE, "projects.json"), encoding="utf-8"))
    legacy = None
    if len(sys.argv) > 1 and sys.argv[1] == "--one":
        # After a write from wolffia-api.py: refresh one project file in place (no lock; atomic replace).
        k, out = parse_key(sys.argv[2]), sys.argv[3]
        if not k:
            print("failed", sys.argv[2], "is not a project number or org/number", file=sys.stderr)
            sys.exit(2)
        owner, num, key = k
        d = fetch_or_die(num, out, owner, key)
        write_atomic(os.path.join(out, file_base(key) + ".js"), data_js(key, d))
        update_index(out, conf, key, index_row(owner, num, key, d))
        print("refreshed %s.js" % file_base(key))
        return
    if len(sys.argv) > 2:  # explicit single project: OUT_FILE.js NUM
        k = parse_key(sys.argv[2])
        if not k:
            print("failed", sys.argv[2], "is not a project number or org/number", file=sys.stderr)
            sys.exit(2)
        d = fetch_or_die(k[1], os.path.dirname(os.path.abspath(sys.argv[1])), k[0], k[2])
        write_atomic(sys.argv[1], "window.PROJECT=" + json.dumps(d, ensure_ascii=False) + ";\n")
        print("wrote", sys.argv[1], len(d["items"]), "issues")
        return
    if len(sys.argv) > 1 and sys.argv[1].endswith(".js"):
        # The deployed cron still calls "fetch-project.py /tmp/x.js": keep writing that single file
        # for the default project, and publish every project to conf["publish"] in the same run.
        legacy = sys.argv[1]
        out = conf.get("publish") or os.path.join(HERE, "data")
    else:
        out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "data")
    os.makedirs(out, exist_ok=True)
    # One run at a time: a full fetch takes minutes and cron fires every 10.
    lock = open(os.path.join(out, ".lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another fetch is running; skip", file=sys.stderr)
        sys.exit(1)
    index, failed = [], []
    keys = keys_of(conf)
    default = parse_key(conf.get("default", keys[0][2] if keys else None))
    for owner, num, key in keys:
        try:
            d = safe_fetch(num, out, owner, key)
        except Exception as e:  # keep the previous file for this project
            failed.append(key)
            print("failed", key, e, file=sys.stderr)
            continue
        if legacy and default and key == default[2]:
            write_atomic(legacy, "window.PROJECT=" + json.dumps(d, ensure_ascii=False) + ";\n")
        write_atomic(os.path.join(out, file_base(key) + ".js"), data_js(key, d))
        index.append(index_row(owner, num, key, d))
        print("wrote %s.js" % file_base(key), d["title"], len(d["items"]), "issues")
    # Projects that failed this round keep their old entry, so the picker does not lose them.
    old = os.path.join(out, "index.js")
    if failed:
        index += [p for p in read_index(old) if row_key(p) in failed]
    write_index(old, conf, index)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
