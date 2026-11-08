import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from ink import panel as ink

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(email="me@example.com", aud="AUD", iss="https://team.example.com", key=KEY, exp=3600):
    return jwt.encode({"email": email, "aud": aud, "iss": iss, "exp": int(time.time()) + exp}, key, algorithm="RS256")


class AccessTest(unittest.TestCase):
    def setUp(self):
        signing = mock.Mock(key=KEY.public_key())
        client = mock.Mock(get_signing_key_from_jwt=mock.Mock(return_value=signing))
        self.patches = [mock.patch.object(ink, "_jwks", client)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_good_token(self):
        self.assertEqual(ink.check_access({"Cf-Access-Jwt-Assertion": token()}), "me@example.com")

    def test_rejects(self):
        for headers in [{}, {"Cf-Access-Jwt-Assertion": "junk"},
                        {"Cf-Access-Jwt-Assertion": token(email="someone@else.com")},
                        {"Cf-Access-Jwt-Assertion": token(aud="OTHER-APP")},
                        {"Cf-Access-Jwt-Assertion": token(iss="https://evil.cloudflareaccess.com")},
                        {"Cf-Access-Jwt-Assertion": token(key=OTHER)},
                        {"Cf-Access-Jwt-Assertion": token(exp=-60)}]:
            with self.assertRaises(ink.Denied):
                ink.check_access(headers)

    def test_unconfigured_refuses_and_none_lets_in(self):
        with mock.patch.object(ink.config, "ACCESS_AUD", ""):
            with self.assertRaises(ink.Denied):
                ink.check_access({"Cf-Access-Jwt-Assertion": token()})
        with mock.patch.object(ink.config, "AUTH", "none"):
            ink.check_access({})
        with mock.patch.object(ink.config, "AUTH", "acess"):  # a typo is not "none"
            with self.assertRaises(ink.Denied):
                ink.check_access({"Cf-Access-Jwt-Assertion": token()})


class RenderTest(unittest.TestCase):
    def test_escapes_everything(self):
        evil = '<script>alert(1)</script> **<img src=x onerror=alert(1)>** `<b>` [<i>x</i>](javascript:alert(1))'
        out = ink.markdown(evil)
        self.assertNotIn("<script", out)
        self.assertNotIn("<img", out)
        self.assertNotIn("<i>", out)
        self.assertNotIn("javascript:", out)
        tool = ink._tool_line({"name": "Bash", "input": {"description": '<svg onload="x">'}})
        self.assertNotIn("<svg", tool)

    def test_markdown(self):
        out = ink.markdown("Hi **there**\n\n- one\n- two `x`\n\n```\na < b\n```")
        self.assertEqual(out, "<p>Hi <b>there</b></p><ul><li>one</li><li>two <code>x</code></li></ul><pre>a &lt; b</pre>")

    def test_what_shows(self):
        you = {"type": "user", "message": {"content": "hello"}}
        self.assertEqual(ink.render(you)[0], "you")
        hidden = [
            {"type": "user", "message": {"content": "<task-notification>x</task-notification>"}},
            {"type": "user", "origin": {"kind": "task-notification"}, "message": {"content": "x"}},
            {"type": "user", "isSynthetic": True, "message": {"content": "summary"}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "out"}]}},
            {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "hm"}]}},
            {"type": "system", "subtype": "task_started"},
        ]
        for e in hidden:
            self.assertIsNone(ink.render(e), e)
        # Odd shapes: plain strings inside the content list count as text; unknown shapes are skipped.
        self.assertEqual(ink.render({"type": "user", "message": {"content": ["hi", {"type": "text", "text": "there"}]}})[0], "you")
        self.assertEqual(ink.render({"type": "assistant", "message": {"content": ["plain"]}})[0], "claude")
        for odd in [{"type": "user", "message": "x"}, {"type": "assistant", "message": {"content": [3, None]}},
                    {"type": "user", "message": {"content": [{"type": "text", "text": None}]}}]:
            ink.render(odd)  # no exception
        said = ink.render({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Done."}, {"type": "tool_use", "name": "Bash", "input": {"description": "List files"}}]}})
        self.assertEqual(said[0], "claude")
        self.assertIn("List files", said[1])


class RefreshTest(unittest.TestCase):
    def test_pages_and_continues(self):
        pages = {
            None: {"data": [{"uuid": "1", "type": "user", "message": {"content": "q"}}], "has_more": True, "last_id": "1"},
            "1": {"data": [{"uuid": "2", "type": "assistant", "message": {"content": [{"type": "text", "text": "a"}]}}],
                  "has_more": False, "last_id": "2"},
            "2": {"data": [], "has_more": False, "last_id": None},
        }

        def api(method, path):
            after = path.split("after_id=")[1] if "after_id=" in path else None
            return pages[after]

        with tempfile.TemporaryDirectory() as d, mock.patch.object(ink, "CACHE_DIR", Path(d)):
            ink._chats.clear()
            chat = ink.refresh("session_x", api)
            self.assertEqual([i["who"] for i in chat["items"]], ["you", "claude"])
            self.assertEqual(chat["last_id"], "2")
            ink._chats.clear()  # from the disk cache: starts after the last event seen
            chat = ink.refresh("session_x", api)
            self.assertEqual(len(chat["items"]), 2)


    def test_event_stored_late_still_shows(self):
        # A message stored after later events were already read (sent mid-compaction) is caught on the next look.
        events = [{"uuid": str(i), "type": "system"} for i in range(3)]

        def api(method, path):
            after = path.split("after_id=")[1] if "after_id=" in path else None
            start = 0 if after is None else [e["uuid"] for e in events].index(after) + 1
            data = events[start:]
            return {"data": data, "has_more": False, "last_id": data[-1]["uuid"] if data else None}

        with tempfile.TemporaryDirectory() as d, mock.patch.object(ink, "CACHE_DIR", Path(d)), \
                mock.patch.object(ink, "OVERLAP", 3):
            ink._chats.clear()
            ink.refresh("session_x", api)
            events.insert(1, {"uuid": "late", "type": "user", "message": {"content": "hello"}})
            events.append({"uuid": "3", "type": "system"})
            ink.refresh("session_x", api)
            chat = ink.refresh("session_x", api)
            self.assertEqual([i["who"] for i in chat["items"]], ["you"])

    def test_sub_agent_prompt_hidden(self):
        self.assertIsNone(ink.render({"type": "user", "parent_tool_use_id": "toolu_1", "message": {"content": "do x"}}))


class OpeningTest(unittest.TestCase):
    def test_opens_with_last_few_and_pages_back(self):
        chat = {"items": [{"n": n, "who": "you", "html": str(n)} for n in range(100)], "next": 100}
        with mock.patch.object(ink, "refresh", return_value=chat), mock.patch.object(ink, "state", return_value={}), \
                mock.patch.object(ink, "OPENING", 40):
            first = ink.chat_update("session_x", -1)
            self.assertEqual([i["n"] for i in first["items"]], list(range(60, 100)))
            self.assertTrue(first["older"])
            self.assertEqual(len(ink.chat_update("session_x", 97)["items"]), 2)
            page = ink.chat_before("session_x", 60)
            self.assertEqual([i["n"] for i in page["items"]], list(range(20, 60)))
            self.assertTrue(page["older"])
            self.assertFalse(ink.chat_before("session_x", 20)["older"])


class ContextTest(unittest.TestCase):
    def test_placeholder(self):
        s = {"session_context": {"model": "claude-opus-5-5[1m]", "effort_level": "medium"},
             "external_metadata": {"context_usage": {"used_tokens": 71035, "max_tokens": 1000000}}}
        self.assertEqual(ink._context(s), "Opus 5.5 · medium · 71k/1M")
        self.assertEqual(ink._model("claude-sonnet-4-5-20250929"), "Sonnet 4.5")
        self.assertEqual(ink._context({"external_metadata": {"context_usage": {"used_tokens": 150500, "max_tokens": 200000}}}), "150k/200k")
        self.assertEqual(ink._context({}), "Reply...")


class SessionsServerTest(unittest.TestCase):
    """The optional sessions server (KINDLE_SESSIONS_URL): reconnecting a stopped chat before sending, new chats."""

    def _reply(self, data):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(data).encode()
        return response

    def test_send_brings_a_stopped_chat_back_first(self):
        with mock.patch.object(ink.config, "SESSIONS_URL", "http://127.0.0.1:1"), \
                mock.patch.object(ink.claude.urllib.request, "urlopen",
                                  side_effect=[self._reply({"running": False}), self._reply({"running": True})]) as opened, \
                mock.patch.object(ink.claude, "api") as api:
            ink.send("session_Test", "hi")
        self.assertEqual([c.args[0].full_url for c in opened.call_args_list],
                         ["http://127.0.0.1:1/api/sessions/session_Test", "http://127.0.0.1:1/api/sessions/session_Test/connect"])
        self.assertEqual(opened.call_args_list[1].args[0].get_method(), "POST")
        api.assert_called_once()

    def test_send_without_a_sessions_server_just_sends(self):
        with mock.patch.object(ink.config, "SESSIONS_URL", ""), \
                mock.patch.object(ink.claude.urllib.request, "urlopen") as opened, \
                mock.patch.object(ink.claude, "api") as api:
            ink.send("session_Test", "hi")
        opened.assert_not_called()
        api.assert_called_once()

    def test_new_chat_page(self):
        with mock.patch.object(ink.config, "SESSIONS_URL", ""):
            self.assertIn("KINDLE_SESSIONS_URL", ink.new_page())
        with tempfile.TemporaryDirectory() as d, mock.patch.object(ink.config, "SESSIONS_URL", "http://127.0.0.1:1"), \
                mock.patch.object(ink.config, "FOLDERS", [d]), \
                mock.patch.object(ink.claude.urllib.request, "urlopen", return_value=self._reply({"folders": [d, "/nonexistent"]})):
            self.assertEqual(ink._folders(), [d])

    def test_refused_and_errors(self):
        import urllib.error
        def http_error(code, body=b""):
            import io
            return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(body))
        with mock.patch.object(ink.config, "SESSIONS_URL", "http://127.0.0.1:1"):
            with mock.patch.object(ink.claude.urllib.request, "urlopen", side_effect=http_error(403)):
                with self.assertRaises(ink.claude.Refused):
                    ink.claude.start("/etc")
            with mock.patch.object(ink.claude.urllib.request, "urlopen", side_effect=http_error(502, b'{"error": "no trust"}')):
                with self.assertRaisesRegex(RuntimeError, "no trust"):
                    ink.claude.start("/x")
            with mock.patch.object(ink.claude.urllib.request, "urlopen", side_effect=http_error(404)):
                self.assertIsNone(ink.claude.lookup("session_x"))


class SendTest(unittest.TestCase):
    def test_platform_reaches_ingress_as_http_header(self):
        credentials = json.dumps({"claudeAiOauth": {"accessToken": "test-token"}})
        account = json.dumps({"oauthAccount": {"organizationUuid": "test-org"}})
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"{}"
        with mock.patch.object(ink.claude, "lookup", return_value=None), \
                mock.patch.object(Path, "read_text", side_effect=[credentials, account]), \
                mock.patch.object(ink.claude.urllib.request, "urlopen", return_value=response) as opened:
            ink.send("session_Test", "Hello from Kindle")
        request = opened.call_args.args[0]
        self.assertEqual(request.get_header("Anthropic-client-platform"), "web_claude_ai")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-token")
        event = json.loads(request.data)["events"][0]
        self.assertEqual(event["message"]["content"], "Hello from Kindle")
        self.assertNotIn("client_platform", event)

    def test_stop_interrupts(self):
        with mock.patch.object(ink.claude, "api") as api:
            ink.stop("session_Test")
        method, path, body = api.call_args.args
        self.assertEqual((method, path), ("POST", "/v1/sessions/session_Test/events"))
        self.assertEqual(body["events"][0]["type"], "control_request")
        self.assertEqual(body["events"][0]["request"], {"subtype": "interrupt"})


class ArtifactTest(unittest.TestCase):
    def test_links(self):
        out = ink.markdown("[Plan · Markdown · v2](https://artifacts.example.com/a/Ab_c-1/v2) and [x](https://example.com/a?b=1&c=2)"
                           " [y](https://artifacts.example.com/a/Q)")
        self.assertIn('<a href="/art/Ab_c-1/v2">Plan · Markdown · v2</a>', out)
        self.assertIn('<a href="/art/Q">y</a>', out)
        self.assertIn('<a href="https://example.com/a?b=1&amp;c=2">x</a>', out)
        # Bare links too, without trailing punctuation; not inside code.
        out = ink.markdown("See https://example.org/notes/Logic. And `https://x.dev/in-code`")
        self.assertIn('<a href="https://example.org/notes/Logic">https://example.org/notes/Logic</a>.', out)
        self.assertIn("<code>https://x.dev/in-code</code>", out)
        # Never a script link or a broken-out attribute.
        for md in ["[z](javascript:alert(1))", '[z](https://x.com/"onmouseover="alert(1))', "javascript:alert(1)"]:
            out = ink.markdown(md)
            self.assertNotIn('href="javascript', out, md)
            self.assertNotIn('"onmouseover', out, md)

    def _hub(self, version, file):
        def get(path):
            return json.dumps(version).encode() if path.startswith("/api/") else file
        return mock.patch.object(ink, "_hub", side_effect=get)

    def test_markdown_artifact(self):
        v = {"title": "<Plan>", "type": "Markdown", "version": 3, "kind": "markdown"}
        with self._hub(v, b"# Head\n\nHello <script>x</script>"):
            page = ink.artifact_page("A", None)
        self.assertIn("&lt;Plan&gt;", page)
        self.assertIn("<b>Head</b>", page)
        self.assertNotIn("<script>x", page)

    def test_other_kinds_get_a_note(self):
        v = {"title": "Song", "type": "MP3", "version": 1, "kind": "audio"}
        with self._hub(v, b"never read"):
            self.assertIn("Only Markdown, text, PDF and office", ink.artifact_page("A", "1"))

    def test_pdf_pages_are_images_sized_ahead(self):
        v = {"title": "Deck", "type": "PPTX", "version": 2, "kind": "office", "pages": [[960, 540], [960, 540]]}
        with self._hub(v, b"never read"):
            html = ink.artifact_doc("A", "2")["html"]
        self.assertIn('<img class="pg" src="/artpg/A/2/1.png" width="718" height="404"', html)
        self.assertIn('/artpg/A/2/2.png', html)

    def test_page_numbers_are_bounded(self):
        self.assertIsNone(ink.page_png("A", 1, 0))
        self.assertIsNone(ink.page_png("A", 1, ink.MAX_PAGES + 1))

    def test_mermaid_block_becomes_an_image(self):
        with mock.patch.object(ink, "mermaid_img", return_value='<img class="pg mmd" src="/mmd/x.png">') as draw:
            out = ink.markdown("Before\n```mermaid\nflowchart TD\n  A --> B\n```\nAfter")
        draw.assert_called_once_with("flowchart TD\n  A --> B")
        self.assertIn('<img class="pg mmd"', out)
        self.assertNotIn("<pre>", out)

    def test_mermaid_that_cannot_be_drawn_shows_its_code(self):
        with mock.patch.object(ink, "mermaid_img", return_value=None):
            out = ink.markdown("```mermaid\nnot a diagram\n```")
        self.assertIn("couldn't be drawn", out)
        self.assertIn("<pre>not a diagram</pre>", out)

    def test_narrow_table_is_a_table(self):
        out = ink.markdown("| Name | Cost |\n|---|--:|\n| **Opus** | $5 |\n| a \\| b | 1 |")
        self.assertIn('<table class="md"><tr><th>Name</th><th>Cost</th></tr>', out)
        self.assertIn("<td><b>Opus</b></td><td>$5</td>", out)
        self.assertIn("<td>a | b</td>", out)

    def test_wide_table_is_cards(self):
        out = ink.markdown("| A | B | C | D | E |\n|-|-|-|-|-|\n| x | 1 | 2 | 3 | 4 |")
        self.assertNotIn("<table", out)
        self.assertIn('<p class="rowcard"><b>x</b><br>B: 1<br>C: 2<br>D: 3<br>E: 4</p>', out)

    def test_other_code_blocks_stay_code(self):
        with mock.patch.object(ink, "mermaid_img") as draw:
            self.assertIn("<pre>x = 1</pre>", ink.markdown("```python\nx = 1\n```"))
        draw.assert_not_called()

    def test_missing(self):
        with mock.patch.object(ink, "_hub", return_value=None):
            self.assertIsNone(ink.artifact_page("A", None))

    def test_doc_for_the_split_pane(self):
        v = {"title": "Plan", "type": "Markdown", "version": 2, "kind": "markdown"}
        with self._hub(v, b"Hello"):
            doc = ink.artifact_doc("A", "2")
        self.assertEqual(doc["title"], "Plan")
        self.assertIn("v2", doc["html"])
        self.assertIn("Hello", doc["html"])
        self.assertNotIn("<html", doc["html"])

    def test_artifact_list(self):
        rows = [{"artifact_id": "A1", "title": "Plan", "label": "Markdown", "updated": time.time() - 7200}]
        with mock.patch.object(ink, "_hub", return_value=json.dumps(rows).encode()):
            self.assertEqual(ink.arts_json(), {"arts": [{"id": "A1", "title": "Plan", "label": "Markdown", "ago": "2 h"}]})

    def test_chat_page_has_the_split_pane(self):
        page = ink.chat_page("session_x")
        self.assertIn('id="split"', page)
        self.assertIn('id="artx"', page)
        self.assertIn('id="artback"', page)
        self.assertIn('id="artb"', page)


if __name__ == "__main__":
    unittest.main()
