from job_discovery import gui


def test_gui_server_runs_without_console_logging(monkeypatch, tmp_path):
    calls: dict[str, object] = {}

    class FakeTimer:
        daemon = False

        def start(self) -> None:
            calls["timer_started"] = True
            calls["timer_daemon"] = self.daemon

    timer = FakeTimer()
    app = object()
    monkeypatch.setattr(gui, "dashboard_is_ready", lambda: False)
    monkeypatch.setattr(gui.threading, "Timer", lambda *_args: timer)
    monkeypatch.setattr(gui, "create_app", lambda _path: app)
    monkeypatch.setattr(
        gui.uvicorn,
        "run",
        lambda passed_app, **kwargs: calls.update(app=passed_app, kwargs=kwargs),
    )

    result = gui.main(["--config", str(tmp_path / "companies.yml")])

    assert result == 0
    assert calls["timer_started"] is True
    assert calls["timer_daemon"] is True
    assert calls["app"] is app
    assert calls["kwargs"] == {
        "host": "127.0.0.1",
        "port": 8000,
        "log_config": None,
        "access_log": False,
    }
