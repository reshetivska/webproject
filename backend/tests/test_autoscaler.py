import json
import os
import socketserver
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.autoscaler import Config, Docker, decide, free_name


def metrics(queue=0, workers=2, busy=0, auto=0, auto_alive=0):
    return {"queue_len": queue, "workers": workers, "busy": busy,
            "auto_containers": auto, "auto_alive": auto_alive}


def new_state():
    return {"pressure": 0, "idle_since": None, "last_action": 0.0}


def test_decide():
    cfg = Config()
    cfg.max_workers, cfg.min_workers = 4, 2
    cfg.cooldown, cfg.idle_sec, cfg.pressure_polls = 8, 30, 2
    st, t = new_state(), 1000.0

    assert decide(metrics(queue=5, busy=4), cfg, st, t) is None
    assert decide(metrics(queue=5, busy=4), cfg, st, t + 3) == "up"
    assert decide(metrics(queue=5, busy=4, auto=1), cfg, st, t + 6) is None
    assert decide(metrics(queue=5, busy=4, auto=1, auto_alive=0), cfg, st, t + 12) == "up"
    assert decide(metrics(queue=9, workers=4, busy=8, auto=2, auto_alive=2), cfg, st, t + 30) is None
    assert decide(metrics(queue=9, workers=4, busy=8, auto=2, auto_alive=2), cfg, st, t + 40) is None
    print("OK  запуск нових воркерів і ліміт максимуму")

    st = new_state()
    m = metrics(queue=0, workers=4, busy=0, auto=2, auto_alive=2)
    assert decide(m, cfg, st, t) is None
    assert decide(m, cfg, st, t + 20) is None
    assert decide(m, cfg, st, t + 31) == "down"
    assert decide(m, cfg, st, t + 40) is None
    assert decide(metrics(workers=3, auto=1, auto_alive=1), cfg, st, t + 62) == "down"
    assert decide(metrics(workers=2, auto=0), cfg, st, t + 100) is None
    assert decide(metrics(workers=2, auto=1, auto_alive=0), cfg, st, t + 200) is None
    print("OK  зупинка простоюючих воркерів, базові не чіпаємо")

    st = new_state()
    busy = metrics(queue=0, workers=4, busy=1, auto=2, auto_alive=2)
    assert decide(busy, cfg, st, t) is None
    assert decide(busy, cfg, st, t + 100) is None
    print("OK  зайняті воркери не зупиняються")


class FakeDocker(BaseHTTPRequestHandler):
    log = []

    def _send(self, code, obj=None):
        data = json.dumps(obj).encode() if obj is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n)) if n else None

    def do_GET(self):
        FakeDocker.log.append(("GET", self.path, None))
        if self.path.startswith("/containers/json"):
            self._send(200, [{"Id": "b", "Created": 2, "State": "running", "Names": ["/webproject-auto-2"]},
                             {"Id": "a", "Created": 1, "State": "running", "Names": ["/webproject-auto-1"]}])
        else:
            self._send(200, {"Config": {"Image": "img", "Env": ["A=1"]}})

    def do_POST(self):
        body = self._body()
        FakeDocker.log.append(("POST", self.path, body))
        if self.path.startswith("/containers/create"):
            self._send(201, {"Id": "newid"})
        else:
            self._send(204)

    def do_DELETE(self):
        FakeDocker.log.append(("DELETE", self.path, None))
        self._send(204)

    def log_message(self, *a):
        pass


class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass

    def get_request(self):
        request, _ = super().get_request()
        return request, ("local", 0)


def test_docker_client():
    path = os.path.join(tempfile.mkdtemp(), "docker.sock")
    server = UnixServer(path, FakeDocker)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        d = Docker(path)
        assert d.inspect("x")["Config"]["Image"] == "img"
        assert [c["Id"] for c in d.list_autoscaled()] == ["a", "b"]
        cid = d.create_worker("webproject-auto-3", "img", ["A=1"], "net", "auto-3", 2)
        assert cid == "newid"
        d.remove("newid", grace=5)
        log = FakeDocker.log
        create = [x for x in log if "/containers/create" in x[1]][0]
        assert "name=webproject-auto-3" in create[1]
        body = create[2]
        assert body["Cmd"][:4] == ["celery", "-A", "app.celery_app", "worker"]
        assert "auto-3@%h" in body["Cmd"] and "--concurrency=2" in body["Cmd"]
        assert body["Labels"] == {"webproject.autoscaled": "true"}
        assert body["HostConfig"]["NetworkMode"] == "net"
        assert any(x[0] == "POST" and x[1].startswith("/containers/newid/start") for x in log)
        assert any(x[0] == "POST" and "/containers/newid/stop?t=5" in x[1] for x in log)
        assert any(x[0] == "DELETE" and x[1].startswith("/containers/newid") for x in log)
        print("OK  клієнт Docker API: створення, запуск, зупинка, видалення")
    finally:
        server.shutdown()


def test_names():
    assert free_name(set()) == (1, "webproject-auto-1")
    assert free_name({"webproject-auto-1", "webproject-auto-3"}) == (2, "webproject-auto-2")
    print("OK  імена контейнерів")


if __name__ == "__main__":
    test_decide()
    test_docker_client()
    test_names()
    print("Усі тести автоскейлера пройшли")
