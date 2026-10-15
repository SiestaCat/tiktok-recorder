"""Manage one browser instance and recover closed pages or browsers."""


class BrowserSession:
    def __init__(self, browserFactory):
        self.browserFactory = browserFactory
        self.manager = None
        self.context = None
        self.page = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, excType, excValue, traceback):
        self.close()

    def open(self):
        self.manager = self.browserFactory()
        self.page = None
        try:
            self.context = self.manager.__enter__()
            return self.getPage()
        except Exception:
            self.close()
            raise

    def close(self):
        manager = self.manager
        self.manager = None
        self.context = None
        self.page = None
        if manager is not None:
            try:
                manager.__exit__(None, None, None)
            except Exception as error:
                # A dead browser may also fail during cleanup. Do not let it
                # interrupt running FFmpeg processes or prevent a relaunch.
                print(f"No se pudo cerrar completamente el navegador anterior: {error}", flush=True)

    def getPage(self):
        if self.page is not None and not self.page.is_closed():
            return self.page
        for page in self.context.pages:
            if not page.is_closed():
                self.page = page
                return page
        self.page = self.context.new_page()
        return self.page

    def replacePage(self):
        previousPage = self.page
        self.page = None
        try:
            self.page = self.context.new_page()
        except Exception:
            print("No se pudo crear otra pestaña; reiniciando el navegador...", flush=True)
            return self.restart()

        if previousPage is not None and previousPage is not self.page:
            try:
                if not previousPage.is_closed():
                    previousPage.close()
            except Exception:
                pass
        return self.page

    def restart(self):
        self.close()
        return self.open()


def isBrowserClosedError(error):
    message = str(error).lower()
    return any(part in message for part in (
        "target page, context or browser has been closed",
        "target closed",
        "browser has been closed",
        "context has been closed",
        "page has been closed",
        "connection closed",
        "browser closed",
    ))


def checkLiveWithRecovery(session, username, getLiveStreams):
    """Retry only browser/page closure, without affecting any FFmpeg job."""
    for attempt in range(3):
        try:
            page = session.getPage()
            streamUrls = getLiveStreams(page, username)
            if not streamUrls:
                return [], ""
            return streamUrls, page.evaluate("navigator.userAgent")
        except Exception as error:
            if not isBrowserClosedError(error) or attempt == 2:
                raise
            if attempt == 0:
                print(f"@{username} | La pestaña o el navegador se cerró. Recuperando sesión...", flush=True)
                session.replacePage()
            else:
                print(f"@{username} | La pestaña nueva también falló. Reiniciando la sesión del navegador...", flush=True)
                session.restart()
