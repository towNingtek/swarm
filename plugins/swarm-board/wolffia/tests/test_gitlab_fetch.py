"""fetch-project.py 讀 GitLab 專案（gitlab:<path>）的單元測試。

不連網：本機起一個假的 GitLab REST API v4（http.server），資料在 tests/_fixtures/gitlab/：
  project.json               GET /api/v4/projects/acme%2Fgeneral
  issues-<state>-<page>.json GET /api/v4/projects/42/issues?state=...&page=...（opened 有兩頁，第 1 頁帶 X-Next-Page: 2）
  label-events.json          {iid: [...]}：GET .../issues/<iid>/resource_label_events
  state-events.json          {iid: [...]}：GET .../issues/<iid>/resource_state_events
每個請求（路徑與標頭）都記下來，用來檢查 token 只放在 PRIVATE-TOKEN 標頭。

  cd wolffia && python3 -m unittest tests.test_gitlab_fetch
"""
import hashlib, http.server, json, os, subprocess, sys, tempfile, threading, unittest, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(ROOT, "fetch-project.py")
FIX = os.path.join(HERE, "_fixtures", "gitlab")
TOKEN = "glpat-SECRET-do-not-leak-0123456789"
KEY = "gitlab:acme/general"
BASE = "g-" + hashlib.sha1(b"acme/general").hexdigest()[:12]
TODO, PROGRESS, REVIEW, DONE = "待辦", "進行中 (7 天一輪)", "審核後請關閉", "完成"


def fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


class FakeGitLab(http.server.BaseHTTPRequestHandler):
    log = []

    def log_message(self, *a):
        pass

    def send(self, code, body, next_page=""):
        data = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Next-Page", next_page)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        FakeGitLab.log.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
        if self.headers.get("PRIVATE-TOKEN") != TOKEN:
            return self.send(401, {"message": "401 Unauthorized"})
        u = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        page = q.get("page", "1")
        if u.path == "/api/v4/projects/acme%2Fgeneral":
            return self.send(200, fixture("project.json"))
        if u.path == "/api/v4/projects/42/issues":
            name = "issues-%s-%s.json" % (q.get("state"), page)
            if not os.path.exists(os.path.join(FIX, name)):
                return self.send(200, [])
            more = os.path.exists(os.path.join(FIX, "issues-%s-%d.json" % (q.get("state"), int(page) + 1)))
            return self.send(200, fixture(name), str(int(page) + 1) if more else "")
        parts = u.path.split("/")
        if len(parts) == 8 and parts[:6] == ["", "api", "v4", "projects", "42", "issues"]:
            kind = {"resource_label_events": "label-events.json", "resource_state_events": "state-events.json"}.get(parts[7])
            if kind:
                return self.send(200, fixture(kind).get(parts[6], []) if page == "1" else [])
        self.send(404, {"message": "404 Not Found"})


class GitLabCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeGitLab)
        cls.url = "http://127.0.0.1:%d" % cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, "out")
        os.makedirs(self.out)
        FakeGitLab.log = []

    def tearDown(self):
        self.tmp.cleanup()

    def run_fetch(self, conf=None, token=TOKEN, key=KEY):
        conf = conf or {"default": KEY, "projects": [2, KEY]}
        pj = os.path.join(self.tmp.name, "projects.json")
        with open(pj, "w", encoding="utf-8") as f:
            json.dump(conf, f, ensure_ascii=False)
        env = {k: v for k, v in os.environ.items() if k not in ("GITLAB_TOKEN", "GITLAB_URL")}
        env.update(WOLFFIA_ORG="acme", WOLFFIA_PROJECTS=pj, GITLAB_URL=self.url)
        if token:
            env["GITLAB_TOKEN"] = token
        self.r = subprocess.run([sys.executable, "-B", SCRIPT, "--one", key, self.out], env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
        return self.r

    def board(self):
        with open(os.path.join(self.out, BASE + ".js"), encoding="utf-8") as f:
            text = f.read()
        prefix = "window.PROJECTS=window.PROJECTS||{};window.PROJECTS[%s]=" % json.dumps(KEY)
        self.assertTrue(text.startswith(prefix), text[:120])
        return json.loads(text[len(prefix):].rstrip(";\n"))

    def index(self):
        with open(os.path.join(self.out, "index.js"), encoding="utf-8") as f:
            return json.loads(f.read().split("=", 1)[1].rstrip(";\n"))

    def items(self, **kw):
        self.assertEqual(self.run_fetch(**kw).returncode, 0, self.r.stderr)
        return {i["n"]: i for i in self.board()["items"]}


class GitLabFetchTest(GitLabCase):
    LABELS = {"default": KEY, "projects": [KEY],
              "gitlabLabels": {"GitLab:Acme/General": {"progress": "In Progress", "review": "Review"}}}

    def test_pages_are_followed(self):
        items = self.items()
        self.assertEqual(sorted(items), [1, 2, 3, 4, 5, 6, 7])  # 4, 6, 7 are on page 2; 5 is closed
        self.assertTrue(any("state=opened" in p and "page=2" in p for p, _ in FakeGitLab.log))

    def test_configured_labels_case_insensitive(self):
        items = self.items(conf=self.LABELS)
        st = {n: i["status"] for n, i in items.items()}
        self.assertEqual(st, {1: TODO, 2: PROGRESS, 3: REVIEW, 4: TODO, 5: DONE, 6: TODO, 7: TODO})

    def test_default_labels(self):
        st = {n: i["status"] for n, i in self.items().items()}
        self.assertEqual(st, {1: TODO, 2: TODO, 3: TODO, 4: TODO, 5: DONE, 6: PROGRESS, 7: REVIEW})

    def test_four_columns_and_project_fields(self):
        self.assertEqual(self.run_fetch().returncode, 0, self.r.stderr)
        d = self.board()
        self.assertEqual(d["cols"], [TODO, PROGRESS, REVIEW, DONE])
        self.assertEqual((d["key"], d["owner"], d["num"]), (KEY, "acme", 42))
        self.assertEqual(d["url"], "https://gitlab.example.invalid/acme/general")

    def test_card_fields(self):
        items = self.items()
        i = items[2]
        self.assertEqual((i["t"], i["u"], i["repo"], i["owner"]),
                         ("issue 2", "https://gitlab.example.invalid/acme/general/-/issues/2", "general", "acme"))
        self.assertEqual((i["state"], i["who"], i["labels"], i["target"]), ("OPEN", ["alice"], ["in progress"], "2026-11-30"))
        self.assertEqual((items[5]["state"], items[5]["closed"]), ("CLOSED", "2026-10-19T00:00:00Z"))
        for i in items.values():
            self.assertIsNone(i["type"])
            self.assertIsNone(i["parent"])
            self.assertEqual(i["blockedBy"], [])

    def test_label_and_state_events_become_hist(self):
        items = self.items(conf=self.LABELS)
        self.assertEqual(items[2]["hist"], [["2026-10-10T00:00:00Z", PROGRESS]])
        self.assertEqual(items[2]["since"], "2026-10-10T00:00:00Z")
        self.assertEqual(items[5]["hist"], [["2026-10-11T00:00:00Z", PROGRESS], ["2026-10-12T00:00:00Z", REVIEW],
                                            ["2026-10-19T00:00:00Z", "closed"]])
        self.assertEqual(items[1]["hist"], [])
        d = self.items()  # default names: In Progress / Review are not status labels
        self.assertEqual(d[2]["hist"], [])
        self.assertEqual(d[5]["hist"], [["2026-10-19T00:00:00Z", "closed"]])
        self.assertEqual(d[6]["hist"], [["2026-10-13T00:00:00Z", PROGRESS]])

    def test_file_name_and_index_row(self):
        self.assertEqual(self.run_fetch(key="gitlab:ACME/General").returncode, 0, self.r.stderr)
        self.assertRegex(BASE, r"^g-[0-9a-f]{12}$")
        self.assertIn(BASE + ".js", os.listdir(self.out))
        rows = self.index()["projects"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["key"], rows[0]["owner"], rows[0]["num"]), (KEY, "acme", 42))
        self.assertEqual((rows[0]["open"], rows[0]["total"]), (6, 7))

    def test_index_keeps_other_rows(self):
        with open(os.path.join(self.out, "index.js"), "w", encoding="utf-8") as f:
            f.write("window.PROJECT_INDEX=" + json.dumps({"org": "acme", "default": 2, "projects": [
                {"num": 2, "owner": "acme", "key": 2, "title": "P2", "open": 1, "total": 1}]}) + ";\n")
        self.assertEqual(self.run_fetch().returncode, 0, self.r.stderr)
        self.assertEqual([r["key"] for r in self.index()["projects"]], [2, KEY])

    def test_token_only_in_private_token_header(self):
        self.assertEqual(self.run_fetch().returncode, 0, self.r.stderr)
        self.assertTrue(FakeGitLab.log)
        for path, headers in FakeGitLab.log:
            self.assertNotIn(TOKEN, path)
            self.assertEqual(headers.get("private-token"), TOKEN)
            self.assertEqual([k for k, v in headers.items() if TOKEN in v], ["private-token"])
        self.assertNotIn(TOKEN, self.r.stdout + self.r.stderr)
        files = [os.path.join(d, n) for d, _, ns in os.walk(self.out) for n in ns]
        self.assertTrue(any(f.endswith(".hist-cache/%s.json" % BASE) for f in files))
        for f in files:
            with open(f, encoding="utf-8") as fh:
                self.assertNotIn(TOKEN, fh.read(), f)

    def test_no_token_fails_and_keeps_old_file(self):
        old = os.path.join(self.out, BASE + ".js")
        with open(old, "w", encoding="utf-8") as f:
            f.write("old\n")
        r = self.run_fetch(token=None)
        self.assertEqual(r.returncode, 1)
        self.assertIn("GITLAB_TOKEN", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(FakeGitLab.log, [])
        with open(old, encoding="utf-8") as f:
            self.assertEqual(f.read(), "old\n")
        self.assertEqual(sorted(os.listdir(self.out)), [BASE + ".js"])

    def test_wrong_token_fails_and_keeps_old_file(self):
        old = os.path.join(self.out, BASE + ".js")
        with open(old, "w", encoding="utf-8") as f:
            f.write("old\n")
        r = self.run_fetch(token="wrong")
        self.assertEqual(r.returncode, 1)
        self.assertIn("401", r.stderr)
        with open(old, encoding="utf-8") as f:
            self.assertEqual(f.read(), "old\n")

    def test_bad_gitlab_keys_rejected(self):
        for bad in ("gitlab:", "gitlab:acme", "gitlab:acme/../x", "gitlab:acme//x", "gitlab:-a/b"):
            with self.subTest(bad=bad):
                r = self.run_fetch(key=bad)
                self.assertEqual(r.returncode, 2)
                self.assertNotIn("Traceback", r.stderr)
                self.assertEqual(os.listdir(self.out), [])


if __name__ == "__main__":
    unittest.main()
