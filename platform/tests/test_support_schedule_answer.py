"""Scheduled-run model chain and answer cleanup (support_schedule_answer)."""
import unittest

from support_schedule_answer import deliverable, model_overlay, schedule_models

TRENDING = """🔥 GitHub Trending — 10/21

1. acme/one ⭐ +3,861 today
   Python｜在本機跑的 AI 求職助手
2. acme/two ⭐ +1,712 today
   JavaScript｜安全稽核技能

---

已完成 GitHub Trending 排程工作，兩項完成條件均達成：

1. **實際產出**：抓取 GitHub 全語言 daily trending（成功，HTTP 200）
2. **更新 WORKING.md**：已寫回 `hives/x/agents/pm/WORKING.md`

本地詳細紀錄：`hives/x/issues/pm/021.md`
"""


class Answer(unittest.TestCase):
    def test_closing_report_after_rule_is_dropped(self):
        out, why = deliverable(TRENDING)
        self.assertEqual(why, '')
        self.assertIn('acme/two', out)
        self.assertNotIn('WORKING.md', out)
        self.assertNotIn('本地詳細紀錄', out)

    def test_reasoning_before_think_end_is_dropped(self):
        out, why = deliverable("I'll read the files first.\n</think>\n" + TRENDING)
        self.assertEqual(why, '')
        self.assertNotIn("I'll read", out)

    def test_narration_lines_are_dropped(self):
        out, why = deliverable("讓我先讀取設定檔\n已更新 WORKING.md。\n" + TRENDING)
        self.assertNotIn('讓我先', out)
        self.assertNotIn('已更新 WORKING.md', out)

    def test_too_short_or_empty_is_rejected(self):
        self.assertEqual(deliverable('')[1], '輸出為空')
        self.assertTrue(deliverable('好的，')[1])
        self.assertIn('太短', deliverable('今天沒有新東西。')[1])

    def test_a_completion_report_without_the_report_is_rejected(self):
        text = """Both required tasks have been successfully completed:

1. **AI News Digest Output** - Produced with 7 articles from 4 sources, translated to Traditional Chinese.

2. **WORKING.md Updated** - Added the entry with timestamp.

任務完成。"""
        out, why = deliverable(text)
        self.assertEqual(out, '')
        self.assertIn('已完成', why)

    def test_a_real_list_is_not_mistaken_for_a_completion_report(self):
        text = """已完成本週 AI 新聞整理：

1. **OpenAI 發表新模型** - 推理速度提升兩倍，價格降低一半，支援更長的上下文。
2. **Google 更新 Gemini** - 新增影片理解，開放給所有開發者試用。
3. 蘋果發表 M5 晶片，AI 運算效能提升 40%。"""
        out, why = deliverable(text)
        self.assertEqual(why, '')
        self.assertIn('蘋果', out)

    def test_a_rule_inside_the_content_is_kept(self):
        text = "第一段內容，說明今天的重點與三個連結，足夠長。\n\n---\n\n第二段：更多說明，同樣是要推播的內容。"
        out, why = deliverable(text)
        self.assertEqual(why, '')
        self.assertIn('第二段', out)


class Models(unittest.TestCase):
    def test_parse_order_and_drop_malformed(self):
        self.assertEqual(schedule_models('nvidia/deepseek-ai/deepseek-v4.1-flash, bad, opencode/big-pickle,nvidia/deepseek-ai/deepseek-v4.1-flash'),
                         [('nvidia', 'deepseek-ai/deepseek-v4.1-flash'), ('opencode', 'big-pickle')])
        self.assertEqual(schedule_models(''), [])

    def test_overlay_sets_only_the_default_model(self):
        y = model_overlay('nvidia', 'deepseek-ai/deepseek-v4.1-flash')
        self.assertIn("provider: 'nvidia'", y)
        self.assertIn("model: 'deepseek-ai/deepseek-v4.1-flash'", y)
        with self.assertRaises(ValueError):
            model_overlay("x'; rm -rf /", 'm')


class Chain(unittest.TestCase):
    def make(self, answers, models):
        from support_site_scheduler import SiteScheduler
        s = SiteScheduler.__new__(SiteScheduler)
        s.models = models
        s.office = None
        calls = []
        def fake(site, prompt, model=None, workdir=None):
            calls.append(model)
            return answers.pop(0)
        s._docker_run = fake
        return s, calls

    def test_first_usable_answer_wins_and_is_cleaned(self):
        s, calls = self.make([(False, 'timeout'), (True, TRENDING)], [('a', 'm1'), ('b', 'm2')])
        ok, out = s._chain_run('acme', 'p')
        self.assertTrue(ok)
        self.assertEqual(calls, [('a', 'm1'), ('b', 'm2')])
        self.assertIn('[model=b/m2] [tried=a/m1,b/m2]', out)
        self.assertNotIn('本地詳細紀錄', out)

    def test_a_narration_only_answer_falls_through(self):
        s, calls = self.make([(True, '好的，'), (True, TRENDING)], [('a', 'm1'), ('b', 'm2')])
        ok, out = s._chain_run('acme', 'p')
        self.assertTrue(ok)
        self.assertEqual(len(calls), 2)

    def test_all_fail(self):
        s, _ = self.make([(False, 'x'), (False, 'y')], [('a', 'm1'), ('b', 'm2')])
        ok, out = s._chain_run('acme', 'p')
        self.assertFalse(ok)
        self.assertIn('全部模型都失敗', out)

    def test_no_models_uses_the_site_default(self):
        s, calls = self.make([(True, TRENDING)], [])
        ok, _ = s._chain_run('acme', 'p')
        self.assertTrue(ok)
        self.assertEqual(calls, [None])


if __name__ == '__main__':
    unittest.main()
