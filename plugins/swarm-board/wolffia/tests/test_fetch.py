"""fetch-project.py 的單元測試：抓取失敗時不覆蓋（unchanged）既有資料檔，成功時原子寫入。

不連網：PATH 最前面放一支假的 gh，行為由環境變數 FAKE_GH 決定：
  ok       回傳一個只有一張 issue 的 project
  exit     exit 1（模擬網路錯、權限錯）
  garbage  回傳不是 JSON 的東西
  errors   回傳 GraphQL errors、沒有 data
  shape    回傳 JSON 但結構不對（projectV2 為 null）
另外（歷史快取用）：
  FAKE_ITEMS  JSON 檔：[[number, issue.updatedAt, status.updatedAt], ...]，取代預設那一張 issue
  FAKE_LOG    每次 timeline 查詢附加一行 issue number，用來數查詢次數
另外（多組織用）：
  FAKE_ORGLOG 每次 organization 查詢附加一行「org num」
  FAKE_MULTI  設了就把 project 標題改成「Fake <org>/<num>」，分得出是哪一個 project 的資料
另外（欄位對應用）：
  FAKE_ENGLISH 設了就用 GitHub 預設的 Status 選項（Backlog/Ready/In progress/In review/Done），
               那張 issue 在 In review，歷史有一筆 In progress

  cd wolffia && python3 -m unittest tests.test_fetch
"""
import json, os, stat, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(ROOT, "fetch-project.py")
PROJECTS = json.load(open(os.path.join(ROOT, "projects.example.json"), encoding="utf-8"))["projects"]

FAKE_GH = r'''#!%s
import json, os, sys
mode = os.environ.get("FAKE_GH", "ok")
q = next(a[6:] for a in sys.argv if a.startswith("query="))
if mode == "exit":
    sys.stderr.write("error connecting to api.github.com\n"); sys.exit(1)
if mode == "garbage":
    print("<html>502 Bad Gateway</html>"); sys.exit(0)
if mode == "errors":
    print(json.dumps({"errors": [{"message": "Could not resolve to a ProjectV2"}]})); sys.exit(0)
if mode == "shape":
    print(json.dumps({"data": {"organization": {"projectV2": None}}})); sys.exit(0)
arg = lambda k: next((a[len(k) + 1:] for a in sys.argv if a.startswith(k + "=")), None)
if os.environ.get("FAKE_ORGLOG") and "organization(login" in q:
    with open(os.environ["FAKE_ORGLOG"], "a") as f:
        f.write("%%s %%s\n" %% (arg("org"), arg("num")))
EN = os.environ.get("FAKE_ENGLISH")
if "options{name}" in q:
    names = ["Backlog", "Ready", "In progress", "In review", "Done"] if EN else ["待辦", "完成"]
    d = {"organization": {"projectV2": {"field": {"options": [{"name": n} for n in names]}}}}
elif "timelineItems" in q:
    if os.environ.get("FAKE_LOG"):
        with open(os.environ["FAKE_LOG"], "a") as f:
            f.write(next(a[2:] for a in sys.argv if a.startswith("n=")) + "\n")
    d = {"repository": {"issue": {"timelineItems": {"nodes": [
        {"__typename": "ClosedEvent", "createdAt": "2026-01-02T00:00:00Z"}]}}}}
    if EN:
        d["repository"]["issue"]["timelineItems"]["nodes"] = [
            {"__typename": "ProjectV2ItemStatusChangedEvent", "createdAt": "2026-01-02T00:00:00Z", "status": "In progress",
             "project": {"number": int(arg("num") or 0) or 1, "url": ""}}]
else:
    d = {"organization": {"projectV2": {"title": "Fake", "url": "https://example.invalid/p", "items": {
        "pageInfo": {"hasNextPage": False, "endCursor": None},
        "nodes": [{"content": {"number": 1, "title": "t", "url": "https://example.invalid/i/1", "state": "OPEN",
                               "createdAt": "2026-01-01T00:00:00Z", "closedAt": None,
                               "repository": {"name": "r", "owner": {"login": "o"}},
                               "assignees": {"nodes": []}, "labels": {"nodes": []},
                               "issueType": None, "parent": None, "blockedBy": {"nodes": []}},
                   "fieldValueByName": {"name": "待辦", "updatedAt": "2026-01-01T00:00:00Z"}, "target": None}]}}}}
    if os.environ.get("FAKE_ITEMS"):
        nodes = d["organization"]["projectV2"]["items"]["nodes"]
        one = json.dumps(nodes[0])
        del nodes[:]
        for n, iu, su in json.load(open(os.environ["FAKE_ITEMS"])):
            x = json.loads(one)
            x["content"].update(number=n, url="https://example.invalid/i/%%d" %% n, updatedAt=iu)
            x["fieldValueByName"]["updatedAt"] = su
            nodes.append(x)
    if EN:
        d["organization"]["projectV2"]["items"]["nodes"][0]["fieldValueByName"]["name"] = "In review"
    if os.environ.get("FAKE_MULTI"):
        d["organization"]["projectV2"]["title"] = "Fake %%s/%%s" %% (arg("org"), arg("num"))
print(json.dumps({"data": d}))
''' % sys.executable

OLD = "window.PROJECT={\"old\":true};\n"
FAILURES = ("exit", "garbage", "errors", "shape")


class GhCase(unittest.TestCase):
    """假 gh 放在 PATH 最前面、輸出到暫存目錄。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bin = os.path.join(self.tmp.name, "bin")
        self.out = os.path.join(self.tmp.name, "out")
        os.makedirs(self.bin)
        os.makedirs(self.out)
        gh = os.path.join(self.bin, "gh")
        with open(gh, "w", encoding="utf-8") as f:
            f.write(FAKE_GH)
        os.chmod(gh, os.stat(gh).st_mode | stat.S_IXUSR)

    def tearDown(self):
        self.tmp.cleanup()

    def run_fetch(self, mode, *args):
        env = dict(os.environ, FAKE_GH=mode, PATH=self.bin + os.pathsep + os.environ.get("PATH", ""))
        return subprocess.run([sys.executable, SCRIPT] + list(args), env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)

    def seed(self, path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(OLD)

    def read(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def leftovers(self):
        return [n for n in os.listdir(self.out) if n.endswith(".tmp")]


class FetchTest(GhCase):
    # --- 失敗：不覆蓋，exit 非 0，原因寫 stderr ---

    def test_single_failure_leaves_file_unchanged(self):
        path = os.path.join(self.out, "x.js")
        for mode in FAILURES:
            with self.subTest(mode=mode):
                self.seed(path)
                r = self.run_fetch(mode, path, "3")
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("failed", r.stderr)
                self.assertNotIn("Traceback", r.stderr)
                self.assertEqual(self.read(path), OLD, "existing file must not be overwritten")
                self.assertEqual(self.leftovers(), [])

    def test_one_failure_leaves_file_unchanged(self):
        path = os.path.join(self.out, "p3.js")
        for mode in FAILURES:
            with self.subTest(mode=mode):
                self.seed(path)
                r = self.run_fetch(mode, "--one", "3", self.out)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("failed", r.stderr)
                self.assertEqual(self.read(path), OLD, "existing file must not be overwritten")
                self.assertEqual(self.leftovers(), [])

    def test_all_projects_failure_leaves_files_unchanged(self):
        paths = [os.path.join(self.out, "p%d.js" % n) for n in PROJECTS]
        for p in paths:
            self.seed(p)
        index = os.path.join(self.out, "index.js")
        with open(index, "w", encoding="utf-8") as f:
            f.write("window.PROJECT_INDEX=" + json.dumps({"default": PROJECTS[0], "projects": [
                {"num": n, "title": "old%d" % n, "open": 1, "total": 2} for n in PROJECTS]}) + ";\n")
        r = self.run_fetch("exit", self.out)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("gh exited", r.stderr)
        for p in paths:
            self.assertEqual(self.read(p), OLD, "%s must stay unchanged" % p)
        prev = json.loads(self.read(index).split("=", 1)[1].rstrip(";\n"))
        self.assertEqual([p["title"] for p in prev["projects"]], ["old%d" % n for n in PROJECTS])

    def test_missing_gh_is_an_error(self):
        path = os.path.join(self.out, "x.js")
        self.seed(path)
        os.remove(os.path.join(self.bin, "gh"))
        env = dict(os.environ, PATH=self.bin)  # no gh anywhere
        r = subprocess.run([sys.executable, SCRIPT, path, "3"], env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("cannot run gh", r.stderr)
        self.assertEqual(self.read(path), OLD)

    # --- 成功：寫入新內容，不留暫存檔 ---

    def test_single_success_replaces_file(self):
        path = os.path.join(self.out, "x.js")
        self.seed(path)
        r = self.run_fetch("ok", path, "3")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = self.read(path)
        self.assertTrue(text.startswith("window.PROJECT="))
        d = json.loads(text[len("window.PROJECT="):].rstrip(";\n"))
        self.assertEqual(d["title"], "Fake")
        self.assertEqual(d["cols"], ["待辦", "完成"])
        self.assertEqual(d["items"][0]["hist"], [["2026-01-02T00:00:00Z", "closed"]])
        self.assertEqual(self.leftovers(), [])

    def test_one_success_replaces_file(self):
        r = self.run_fetch("ok", "--one", "3", self.out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("window.PROJECTS[3]=", self.read(os.path.join(self.out, "p3.js")))
        self.assertEqual(self.leftovers(), [])

    def test_one_writes_index_and_keeps_other_rows(self):
        # --one keeps data/index.js in step (the board lists projects from it), replacing only its row.
        with open(os.path.join(self.out, "index.js"), "w", encoding="utf-8") as f:
            f.write('window.PROJECT_INDEX={"default": 3, "projects": [{"num": 7, "title": "other", "open": 1, "total": 1}]};\n')
        r = self.run_fetch("ok", "--one", "3", self.out)
        self.assertEqual(r.returncode, 0, r.stderr)
        idx = json.loads(self.read(os.path.join(self.out, "index.js")).split("=", 1)[1].rstrip(";\n"))
        self.assertEqual(sorted(p["num"] for p in idx["projects"]), [3, 7])
        self.assertEqual([p for p in idx["projects"] if p["num"] == 3][0]["title"] != "", True)

    def test_one_reads_projects_json_from_env(self):
        # The site plugin keeps projects.json beside its data dir and passes WOLFFIA_PROJECTS.
        conf = os.path.join(self.tmp.name, "projects.json")
        with open(conf, "w", encoding="utf-8") as f:
            json.dump({"default": 3, "projects": [3]}, f)
        env = dict(os.environ, FAKE_GH="ok", WOLFFIA_PROJECTS=conf, PATH=self.bin + os.pathsep + os.environ.get("PATH", ""))
        r = subprocess.run([sys.executable, SCRIPT, "--one", "3", self.out], env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        idx = json.loads(self.read(os.path.join(self.out, "index.js")).split("=", 1)[1].rstrip(";\n"))
        self.assertEqual([p["num"] for p in idx["projects"]], [3])

    def test_all_projects_success(self):
        r = self.run_fetch("ok", self.out)
        self.assertEqual(r.returncode, 0, r.stderr)
        for n in PROJECTS:
            self.assertIn("window.PROJECTS[%d]=" % n, self.read(os.path.join(self.out, "p%d.js" % n)))
        idx = json.loads(self.read(os.path.join(self.out, "index.js")).split("=", 1)[1].rstrip(";\n"))
        self.assertEqual([p["num"] for p in idx["projects"]], PROJECTS)
        self.assertEqual(self.leftovers(), [])



class HistCacheTest(GhCase):
    """歷史快取：<out>/.hist-cache/p<N>.json，只有 updatedAt 或 Status updatedAt 變了才查 timeline。"""

    def setUp(self):
        super().setUp()
        self.items = os.path.join(self.tmp.name, "items.json")
        self.log = os.path.join(self.tmp.name, "timeline.log")
        self.cache = os.path.join(self.out, ".hist-cache", "p3.json")
        self.board = [[n, "2026-01-0%dT00:00:00Z" % n, "2026-02-01T00:00:00Z"] for n in (1, 2, 3)]

    def fetch3(self):
        with open(self.items, "w") as f:
            json.dump(self.board, f)
        if os.path.exists(self.log):
            os.remove(self.log)
        env = dict(os.environ, FAKE_GH="ok", FAKE_ITEMS=self.items, FAKE_LOG=self.log,
                   PATH=self.bin + os.pathsep + os.environ.get("PATH", ""))
        r = subprocess.run([sys.executable, SCRIPT, "--one", "3", self.out], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        queried = sorted(int(x) for x in self.read(self.log).split()) if os.path.exists(self.log) else []
        return r, queried

    def items_out(self):
        d = json.loads(self.read(os.path.join(self.out, "p3.js")).split("]=", 1)[1].rstrip(";\n"))
        return d["items"]

    def test_first_run_queries_all_and_writes_cache(self):
        r, q = self.fetch3()
        self.assertEqual(q, [1, 2, 3])
        c = json.loads(self.read(self.cache))
        self.assertEqual(sorted(c), ["https://example.invalid/i/%d" % n for n in (1, 2, 3)])
        e = c["https://example.invalid/i/2"]
        self.assertEqual(e["key"], "2026-01-02T00:00:00Z|2026-02-01T00:00:00Z")
        self.assertEqual(e["hist"], [["2026-01-02T00:00:00Z", "closed"]])
        self.assertEqual(os.listdir(os.path.dirname(self.cache)), ["p3.json"], "no temp file left")

    def test_unchanged_second_run_queries_nothing(self):
        self.fetch3()
        r, q = self.fetch3()
        self.assertEqual(q, [])
        self.assertTrue(all(i["hist"] == [["2026-01-02T00:00:00Z", "closed"]] for i in self.items_out()),
                        "cached history is still written to the data file")

    def test_one_issue_updated_queries_only_it(self):
        self.fetch3()
        self.board[1][1] = "2026-03-01T00:00:00Z"
        r, q = self.fetch3()
        self.assertEqual(q, [2])
        self.assertEqual(json.loads(self.read(self.cache))["https://example.invalid/i/2"]["key"],
                         "2026-03-01T00:00:00Z|2026-02-01T00:00:00Z")

    def test_status_updated_requeries(self):
        self.fetch3()
        self.board[2][2] = "2026-03-01T00:00:00Z"
        r, q = self.fetch3()
        self.assertEqual(q, [3])

    def test_broken_cache_queries_all_without_error(self):
        os.makedirs(os.path.dirname(self.cache))
        for bad in ("{not json", "[1, 2]", json.dumps({"https://example.invalid/i/1": "x",
                                                      "https://example.invalid/i/2": {"key": 5}})):
            with self.subTest(bad=bad):
                with open(self.cache, "w") as f:
                    f.write(bad)
                r, q = self.fetch3()
                self.assertNotIn("Traceback", r.stderr)
                self.assertEqual(q, [1, 2, 3])
                self.assertEqual(len(json.loads(self.read(self.cache))), 3, "cache is rebuilt")

    def test_output_reports_timeline_queries(self):
        r, q = self.fetch3()
        self.assertIn("timeline queries: 3 / 3", r.stdout)
        self.board[0][1] = "2026-03-01T00:00:00Z"
        r, q = self.fetch3()
        self.assertIn("timeline queries: 1 / 3", r.stdout)

    def test_removed_issue_drops_out_of_cache(self):
        self.fetch3()
        del self.board[0]
        r, q = self.fetch3()
        self.assertEqual(q, [])
        self.assertNotIn("https://example.invalid/i/1", json.loads(self.read(self.cache)))


class MultiOrgTests(GhCase):
    """projects.json 的項目可以是整數（acme 的編號）或 "組織/編號"（別的組織的 project）。"""

    def setUp(self):
        super().setUp()
        self.conf = os.path.join(self.tmp.name, "projects.json")
        self.orglog = os.path.join(self.tmp.name, "org.log")

    def run_conf(self, projects, *args, default=None):
        with open(self.conf, "w", encoding="utf-8") as f:
            json.dump({"default": projects[0] if default is None else default, "projects": projects}, f)
        if os.path.exists(self.orglog):
            os.remove(self.orglog)
        env = dict(os.environ, FAKE_GH="ok", FAKE_MULTI="1", FAKE_ORGLOG=self.orglog, WOLFFIA_PROJECTS=self.conf,
                   PATH=self.bin + os.pathsep + os.environ.get("PATH", ""))
        r = subprocess.run([sys.executable, SCRIPT] + list(args), env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def queried(self):
        return [tuple(l.split()) for l in self.read(self.orglog).splitlines()] if os.path.exists(self.orglog) else []

    def board(self, name):
        text = self.read(os.path.join(self.out, name))
        self.assertTrue(text.startswith("window.PROJECTS=window.PROJECTS||{};window.PROJECTS["), text[:80])
        key, body = text.split("window.PROJECTS[", 1)[1].split("]=", 1)
        return json.loads(key), json.loads(body.rstrip(";\n"))

    def index(self):
        return json.loads(self.read(os.path.join(self.out, "index.js")).split("=", 1)[1].rstrip(";\n"))

    def test_string_entry_queries_its_org(self):
        self.run_conf(["other/1"], self.out)
        self.assertEqual(set(self.queried()), {("other", "1")})
        key, d = self.board("p-other-1.js")
        self.assertEqual(key, "other/1")
        self.assertEqual((d["title"], d["owner"], d["num"], d["key"]), ("Fake other/1", "other", 1, "other/1"))

    def test_one_with_string_entry_queries_its_org(self):
        r = self.run_conf([2, "other/1"], "--one", "other/1", self.out)
        self.assertEqual(set(self.queried()), {("other", "1")})
        self.assertIn("refreshed p-other-1.js", r.stdout)
        self.assertEqual(self.board("p-other-1.js")[1]["title"], "Fake other/1")
        self.assertTrue(os.path.exists(os.path.join(self.out, ".hist-cache", "p-other-1.json")))

    def test_same_number_other_org_does_not_overwrite(self):
        self.run_conf([1, "other/1"], self.out)
        self.assertEqual(sorted(set(self.queried())), [("acme", "1"), ("other", "1")])
        k1, d1 = self.board("p1.js")
        k2, d2 = self.board("p-other-1.js")
        self.assertEqual((k1, d1["title"], d1["owner"]), (1, "Fake acme/1", "acme"))
        self.assertEqual((k2, d2["title"], d2["owner"]), ("other/1", "Fake other/1", "other"))
        # --one 其中一個，另一個的檔案不動
        before = self.read(os.path.join(self.out, "p1.js"))
        self.run_conf([1, "other/1"], "--one", "other/1", self.out)
        self.assertEqual(self.read(os.path.join(self.out, "p1.js")), before)
        self.run_conf([1, "other/1"], "--one", "1", self.out)
        self.assertEqual(self.board("p-other-1.js")[1]["title"], "Fake other/1")
        self.assertEqual([p["key"] for p in self.index()["projects"]], [1, "other/1"])

    def test_integer_entry_output_unchanged(self):
        self.run_conf([2], self.out)
        self.assertEqual(set(self.queried()), {("acme", "2")})
        self.assertIn("window.PROJECTS[2]=", self.read(os.path.join(self.out, "p2.js")))
        self.assertEqual(sorted(n for n in os.listdir(self.out) if n.endswith(".js")), ["index.js", "p2.js"])
        self.assertEqual(self.board("p2.js")[0], 2)

    def test_index_rows_have_owner_and_key(self):
        self.run_conf([2, "other/1"], self.out, default="other/1")
        idx = self.index()
        self.assertEqual(idx["default"], "other/1")
        rows = [(p["num"], p["owner"], p["key"]) for p in idx["projects"]]
        self.assertEqual(rows, [(2, "acme", 2), (1, "other", "other/1")])

    def test_one_index_rows_have_owner_and_key(self):
        self.run_conf([2, "other/1"], "--one", "other/1", self.out)
        self.run_conf([2, "other/1"], "--one", "2", self.out)
        rows = [(p["num"], p["owner"], p["key"]) for p in self.index()["projects"]]
        self.assertEqual(rows, [(2, "acme", 2), (1, "other", "other/1")])

    def test_town_intelligent_string_is_the_integer(self):
        self.run_conf(["acme/2"], "--one", "acme/2", self.out)
        self.assertEqual(self.board("p2.js")[0], 2)
        self.assertEqual(self.index()["projects"][0]["key"], 2)

    def test_bad_entry_is_rejected(self):
        env = dict(os.environ, FAKE_GH="ok", PATH=self.bin + os.pathsep + os.environ.get("PATH", ""))
        for bad in ("other/x", "../1", "a/b/1", "/1"):
            with self.subTest(bad=bad):
                r = subprocess.run([sys.executable, SCRIPT, "--one", bad, self.out], env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
                self.assertNotEqual(r.returncode, 0)
                self.assertNotIn("Traceback", r.stderr)
                self.assertEqual(os.listdir(self.out), [])


if __name__ == "__main__":
    unittest.main()


class ColumnMapTest(GhCase):
    """projects.json "columns": a project with GitHub's default Status options lands in the board's rings."""

    def fetch_en(self, conf):
        pj = os.path.join(self.tmp.name, "projects.json")
        with open(pj, "w", encoding="utf-8") as f:
            json.dump(conf, f, ensure_ascii=False)
        out = os.path.join(self.tmp.name, "data")
        env = dict(os.environ, FAKE_GH="ok", FAKE_ENGLISH="1", WOLFFIA_ORG="acme", WOLFFIA_PROJECTS=pj,
                   PATH=self.bin + os.pathsep + os.environ.get("PATH", ""))
        r = subprocess.run([sys.executable, "-B", SCRIPT, "--one", "1", out], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        t = open(os.path.join(out, "p1.js"), encoding="utf-8").read()
        return json.loads(t.split("]=", 1)[1].rstrip(";\n"))

    def test_mapped_columns_statuses_and_history(self):
        d = self.fetch_en({"projects": [1], "columns": {"1": {"Backlog": "待辦", "Ready": "待辦", "In progress": "進行中 (7 天一輪)",
                                                            "In review": "審核後請關閉", "Done": "完成"}}})
        self.assertEqual(d["cols"], ["待辦", "進行中 (7 天一輪)", "審核後請關閉", "完成"])
        self.assertEqual(d["items"][0]["status"], "審核後請關閉")
        self.assertEqual(d["items"][0]["hist"], [["2026-01-02T00:00:00Z", "進行中 (7 天一輪)"]])

    def test_without_columns_nothing_is_mapped(self):
        d = self.fetch_en({"projects": [1]})
        self.assertEqual(d["cols"], [])          # the board cannot draw it: the old behaviour, unchanged
        self.assertEqual(d["items"][0]["status"], "In review")
