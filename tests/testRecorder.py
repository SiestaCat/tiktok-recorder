import io
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from tiktokRecorder.app import handleCommand, runRecorder
from tiktokRecorder.browser import BrowserSession, checkLiveWithRecovery
from tiktokRecorder.recording import RecordingJob
from tiktokRecorder.profile import cloneProfile, createBrowserFactory
from tiktokRecorder.streams import addStream, getLiveStreams


class FakeProcess:
    def __init__(self, args, **kwargs):
        self.args = args
        self.stdin = io.BytesIO()
        self.returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


class FakePage:
    def __init__(self, data=None, closed=False):
        self.data = data
        self.visited = []
        self.closed = closed

    def is_closed(self):
        return self.closed

    def close(self):
        self.closed = True

    def goto(self, url, **kwargs):
        self.visited.append(url)

    def wait_for_timeout(self, milliseconds):
        pass

    def evaluate(self, script, *args):
        if script == "navigator.userAgent":
            return "AgentForTest"
        return self.data


class FakeContext:
    def __init__(self, pages=None, failNewPage=False):
        self.pages = [] if pages is None else pages
        self.failNewPage = failNewPage

    def new_page(self):
        if self.failNewPage:
            raise RuntimeError("Browser has been closed")
        page = FakePage()
        self.pages.append(page)
        return page


class FakeBrowserManager:
    def __init__(self, context):
        self.context = context
        self.wasClosed = False

    def __enter__(self):
        return self.context

    def __exit__(self, *args):
        self.wasClosed = True


class RecorderTests(unittest.TestCase):
    def testStreamSelectionPrefersH264(self):
        streams = {}
        addStream({
            "hevc": {"VCodec": "h265", "origin": "https://server/path/hevc.flv"},
            "avc": {"VCodec": "h264", "origin": "https://server/path/h264.m3u8"}
        }, streams)
        self.assertEqual(max(streams, key=streams.get), "https://server/path/h264.m3u8")

    def testRoomVerificationRejectsOtherUser(self):
        page = FakePage({"statusCode": 0, "data": {
            "user": {"uniqueId": "unrelated", "roomId": "123"},
            "liveRoom": {"status": 2, "roomId": "123", "streamData": {"origin": "https://server/live.flv"}}
        }})
        with self.assertRaises(ValueError):
            getLiveStreams(page, "requested")

    def testTwoIndependentProcessesUseSeparateOutputs(self):
        with tempfile.TemporaryDirectory() as temporaryDir:
            with patch("tiktokRecorder.recording.subprocess.Popen", side_effect=FakeProcess) as popen:
                first = RecordingJob("first", Path(temporaryDir) / "first")
                second = RecordingJob("second", Path(temporaryDir) / "second")
                first.start(["https://server/first.flv"], "TestAgent")
                second.start(["https://server/second.flv"], "TestAgent")
                self.assertEqual(popen.call_count, 2)
                self.assertTrue(first.isRecording())
                self.assertTrue(second.isRecording())
                self.assertNotEqual(first.outputFile, second.outputFile)
                self.assertIn("https://www.tiktok.com/@first/live", first.process.args)
                self.assertIn("https://www.tiktok.com/@second/live", second.process.args)
                first.stop()
                second.stop()

    def testFailedSourceTriesNextSource(self):
        with tempfile.TemporaryDirectory() as temporaryDir:
            with patch("tiktokRecorder.recording.subprocess.Popen", side_effect=FakeProcess) as popen:
                job = RecordingJob("first", Path(temporaryDir))
                job.start(["https://server/bad.flv", "https://server/good.m3u8"], "Agent")
                job.process.returncode = 1
                job.update()
                self.assertEqual(popen.call_count, 2)
                self.assertIn("https://server/good.m3u8", job.process.args)
                job.stop()

    def testInteractiveSecondUserWhileFirstIsRecording(self):
        firstStarted = threading.Event()
        secondStarted = threading.Event()
        managers = []
        firstStillActive = []
        processes = []

        def readCommands(commands):
            commands.put("first")
            if firstStarted.wait(3):
                commands.put("second")
            secondStarted.wait(3)
            commands.put("salir")

        def getStreams(page, username):
            if username == "first":
                firstStarted.set()
            if username == "second":
                firstStillActive.append(processes[0].poll() is None)
                secondStarted.set()
            return [f"https://server/{username}.flv"]

        def browserFactoryForUser(username):
            def browserFactory():
                manager = FakeBrowserManager(FakeContext(pages=[FakePage()]))
                managers.append((username, manager))
                return manager
            return browserFactory

        def spawn(args, **kwargs):
            process = FakeProcess(args, **kwargs)
            processes.append(process)
            return process

        with tempfile.TemporaryDirectory() as temporaryDir:
            with patch("tiktokRecorder.app.readCommands", side_effect=readCommands):
                with patch("tiktokRecorder.app.getLiveStreams", side_effect=getStreams):
                    with patch("tiktokRecorder.recording.subprocess.Popen", side_effect=spawn) as popen:
                        runRecorder(None, Path(temporaryDir), browserFactoryForUser)
                        self.assertTrue(firstStarted.is_set())
                        self.assertTrue(secondStarted.is_set())
                        self.assertEqual(popen.call_count, 2)
                        self.assertEqual([name for name, manager in managers], ["first", "second"])
                        self.assertTrue(all(manager.wasClosed for name, manager in managers))
                        self.assertEqual(firstStillActive, [True])

    def testProfileCloneCopiesOnlyRequestedLoginData(self):
        with tempfile.TemporaryDirectory() as temporaryDir:
            original = Path(temporaryDir) / "profile"
            (original / "storage" / "tiktok").mkdir(parents=True)
            for filename in ("cookies.sqlite", "cookies.sqlite-wal", "storage.sqlite"):
                (original / filename).write_text(filename, encoding="utf-8")
            (original / "storage" / "tiktok" / "session.json").write_text("session", encoding="utf-8")
            (original / "unrelated.txt").write_text("do not copy", encoding="utf-8")
            first = cloneProfile(original, "User.Name")
            second = cloneProfile(original, "User.Name")
            self.assertNotEqual(first, second)
            self.assertRegex(first.name, r"^profile_user\.name_[0-9a-f]{12}$")
            self.assertEqual({entry.name for entry in first.iterdir()}, {
                "cookies.sqlite", "cookies.sqlite-wal", "storage", "storage.sqlite"
            })
            self.assertEqual((first / "cookies.sqlite").read_text(), "cookies.sqlite")
            self.assertEqual((first / "storage" / "tiktok" / "session.json").read_text(), "session")
            (first / "cookies.sqlite").write_text("modified", encoding="utf-8")
            self.assertEqual((original / "cookies.sqlite").read_text(), "cookies.sqlite")
            self.assertEqual((second / "cookies.sqlite").read_text(), "cookies.sqlite")

    def testMissingWalIsAllowedButMissingCookiesIsNot(self):
        with tempfile.TemporaryDirectory() as temporaryDir:
            original = Path(temporaryDir) / "profile"
            original.mkdir()
            with self.assertRaises(FileNotFoundError):
                cloneProfile(original, "first")
            (original / "cookies.sqlite").write_bytes(b"cookie-data")
            copy = cloneProfile(original, "first")
            self.assertEqual((copy / "cookies.sqlite").read_bytes(), b"cookie-data")
            self.assertFalse((copy / "cookies.sqlite-wal").exists())

    def testEveryBrowserOpenCopiesFreshProfile(self):
        created = []

        def fakePlaywright(**kwargs):
            created.append(kwargs)
            return FakeBrowserManager(FakeContext(pages=[FakePage()]))

        with tempfile.TemporaryDirectory() as temporaryDir:
            base = Path(temporaryDir) / "profile"
            base.mkdir()
            (base / "cookies.sqlite").write_text("login-state")
            factory = createBrowserFactory(base, "first", playwrightClass=fakePlaywright)
            with BrowserSession(factory) as session:
                session.getPage()
                session.restart()
                session.getPage()
            self.assertEqual(len(created), 2)
            self.assertNotEqual(created[0]["profile_dir"], created[1]["profile_dir"])
            self.assertEqual(created[0]["seed"], created[1]["seed"])
            self.assertEqual((base / "cookies.sqlite").read_text(), "login-state")
            self.assertTrue(all((entry["profile_dir"] / "cookies.sqlite").exists() for entry in created))

    def testEachRetryCreatesAnotherPlaywrightInstance(self):
        checkedTwice = threading.Event()
        opened = []
        checks = []

        def readCommands(commands):
            commands.put("first")
            checkedTwice.wait(3)
            commands.put("salir")

        def getStreams(page, username):
            checks.append(username)
            if len(checks) == 2:
                checkedTwice.set()
                return ["https://server/live.flv"]
            return []

        def factoryForUser(username):
            def factory():
                manager = FakeBrowserManager(FakeContext(pages=[FakePage()]))
                opened.append(manager)
                return manager
            return factory

        with tempfile.TemporaryDirectory() as temporaryDir:
            with patch("tiktokRecorder.app.readCommands", side_effect=readCommands):
                with patch("tiktokRecorder.app.getLiveStreams", side_effect=getStreams):
                    with patch("tiktokRecorder.app.RETRY_SECONDS", 0):
                        with patch("tiktokRecorder.recording.subprocess.Popen", side_effect=FakeProcess):
                            runRecorder(None, Path(temporaryDir), factoryForUser)
            self.assertEqual(checks, ["first", "first"])
            self.assertEqual(len(opened), 2)
            self.assertTrue(all(manager.wasClosed for manager in opened))

    def testClosedInitialPageIsReplaced(self):
        deadPage = FakePage(closed=True)
        context = FakeContext(pages=[deadPage])
        with BrowserSession(lambda: FakeBrowserManager(context)) as session:
            self.assertIsNot(session.getPage(), deadPage)
            self.assertEqual(len(context.pages), 2)
            self.assertFalse(session.getPage().is_closed())

    def testPageClosureRetriesWithoutReopeningBrowser(self):
        context = FakeContext(pages=[FakePage()])
        manager = FakeBrowserManager(context)
        with BrowserSession(lambda: manager) as session:
            checked = []

            def streams(page, username):
                checked.append(page)
                if len(checked) == 1:
                    page.close()
                    raise RuntimeError("Page.goto: Target page, context or browser has been closed")
                return ["https://server/video.flv"]

            urls, agent = checkLiveWithRecovery(session, "first", streams)
            self.assertEqual(urls, ["https://server/video.flv"])
            self.assertEqual(agent, "AgentForTest")
            self.assertEqual(len(checked), 2)
            self.assertIsNot(checked[0], checked[1])
            self.assertFalse(manager.wasClosed)

    def testBrowserClosureRestartsSessionWithSameFactory(self):
        firstContext = FakeContext(pages=[FakePage()])
        secondContext = FakeContext(pages=[FakePage()])
        managers = [FakeBrowserManager(firstContext), FakeBrowserManager(secondContext)]
        created = []

        def browserFactory():
            manager = managers[len(created)]
            created.append(manager)
            return manager

        with BrowserSession(browserFactory) as session:
            def streams(page, username):
                if page in firstContext.pages:
                    firstContext.failNewPage = True
                    page.close()
                    raise RuntimeError("Page.goto: Target page, context or browser has been closed")
                return ["https://server/live.flv"]

            urls, agent = checkLiveWithRecovery(session, "first", streams)
            self.assertEqual(urls, ["https://server/live.flv"])
            self.assertEqual(agent, "AgentForTest")
            self.assertEqual(len(created), 2)
            self.assertTrue(managers[0].wasClosed)

    def testBrowserRecoveryDoesNotStopActiveFfmpeg(self):
        firstStarted = threading.Event()
        secondStarted = threading.Event()
        firstAliveWhileSecondStarts = []
        processList = []
        browserContexts = {}

        def readCommands(commands):
            commands.put("first")
            if firstStarted.wait(3):
                commands.put("second")
            secondStarted.wait(3)
            commands.put("salir")

        def streams(page, username):
            if username == "first":
                firstStarted.set()
                return ["https://server/first.flv"]
            secondContext = browserContexts["second"]
            if len(secondContext.pages) == 1:
                page.close()
                raise RuntimeError("Page.goto: Target page, context or browser has been closed")
            firstAliveWhileSecondStarts.append(processList[0].poll() is None)
            secondStarted.set()
            return ["https://server/second.flv"]

        def browserFactoryForUser(username):
            def browserFactory():
                context = FakeContext(pages=[FakePage()])
                browserContexts[username] = context
                return FakeBrowserManager(context)
            return browserFactory

        def spawn(args, **kwargs):
            process = FakeProcess(args, **kwargs)
            processList.append(process)
            return process

        with tempfile.TemporaryDirectory() as temporaryDir:
            with patch("tiktokRecorder.app.readCommands", side_effect=readCommands):
                with patch("tiktokRecorder.app.getLiveStreams", side_effect=streams):
                    with patch("tiktokRecorder.recording.subprocess.Popen", side_effect=spawn):
                        runRecorder(None, Path(temporaryDir), browserFactoryForUser)
        self.assertEqual(len(processList), 2)
        self.assertEqual(firstAliveWhileSecondStarts, [True])

    def testNonBrowserErrorDoesNotRestartSession(self):
        context = FakeContext(pages=[FakePage()])
        with BrowserSession(lambda: FakeBrowserManager(context)) as session:
            with self.assertRaisesRegex(ValueError, "respuesta inválida"):
                checkLiveWithRecovery(session, "first", lambda page, username: (_ for _ in ()).throw(ValueError("respuesta inválida")))
            self.assertEqual(len(context.pages), 1)


if __name__ == "__main__":
    unittest.main()
