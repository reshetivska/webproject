import http.client
import json
import os
import signal
import socket
import time
import urllib.parse

LABEL = "webproject.autoscaled"


class Config:
    min_workers = int(os.getenv("AUTOSCALE_MIN_WORKERS", "2"))
    max_workers = int(os.getenv("AUTOSCALE_MAX_WORKERS", "4"))
    interval = float(os.getenv("AUTOSCALE_INTERVAL", "3"))
    idle_sec = float(os.getenv("AUTOSCALE_IDLE_SEC", "30"))
    cooldown = float(os.getenv("AUTOSCALE_COOLDOWN", "8"))
    pressure_polls = int(os.getenv("AUTOSCALE_PRESSURE_POLLS", "2"))
    concurrency = int(os.getenv("AUTOSCALE_CONCURRENCY", "2"))
    socket_path = os.getenv("DOCKER_SOCKET", "/var/run/docker.sock")


def decide(m, cfg, st, now):
    starting = max(0, m["auto_containers"] - m["auto_alive"])
    effective = m["workers"] + starting

    st["pressure"] = st["pressure"] + 1 if m["queue_len"] > 0 else 0

    if m["queue_len"] == 0 and m["busy"] == 0:
        if st["idle_since"] is None:
            st["idle_since"] = now
    else:
        st["idle_since"] = None

    if now - st["last_action"] < cfg.cooldown:
        return None

    if st["pressure"] >= cfg.pressure_polls and effective < cfg.max_workers:
        st["last_action"] = now
        return "up"

    if (st["idle_since"] is not None and now - st["idle_since"] >= cfg.idle_sec
            and m["auto_containers"] > 0 and m["workers"] > cfg.min_workers):
        st["last_action"] = now
        st["idle_since"] = now
        return "down"
    return None


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost")
        self._path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(60)
        self.sock.connect(self._path)


class Docker:
    def __init__(self, socket_path):
        self.socket_path = socket_path

    def request(self, method, path, body=None, ok=(200, 201, 204)):
        conn = _UnixConnection(self.socket_path)
        try:
            headers = {"Content-Type": "application/json"} if body is not None else {}
            conn.request(method, path, json.dumps(body) if body is not None else None, headers)
            resp = conn.getresponse()
            raw = resp.read()
        finally:
            conn.close()
        if resp.status not in ok:
            raise RuntimeError("Docker API %s %s -> %s %s" % (method, path, resp.status, raw[:200]))
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            return None

    def inspect(self, ident):
        return self.request("GET", "/containers/%s/json" % ident)

    def list_autoscaled(self):
        flt = urllib.parse.quote(json.dumps({"label": [LABEL + "=true"]}))
        items = self.request("GET", "/containers/json?all=1&filters=" + flt) or []
        return sorted(items, key=lambda c: c.get("Created", 0))

    def create_worker(self, name, image, env, network, hostname_prefix, concurrency):
        body = {
            "Image": image,
            "Cmd": ["celery", "-A", "app.celery_app", "worker",
                    "-n", "%s@%%h" % hostname_prefix,
                    "--concurrency=%d" % concurrency, "-l", "info"],
            "Env": env,
            "Labels": {LABEL: "true"},
            "HostConfig": {"NetworkMode": network, "RestartPolicy": {"Name": "no"}},
            "NetworkingConfig": {"EndpointsConfig": {network: {}}},
        }
        created = self.request("POST", "/containers/create?name=" + urllib.parse.quote(name), body)
        self.request("POST", "/containers/%s/start" % created["Id"], ok=(204, 304))
        return created["Id"]

    def remove(self, ident, grace=30):
        self.request("POST", "/containers/%s/stop?t=%d" % (ident, grace), ok=(204, 304, 404))
        self.request("DELETE", "/containers/%s?force=1" % ident, ok=(204, 404))


def free_name(existing_names):
    n = 1
    while "webproject-auto-%d" % n in existing_names:
        n += 1
    return n, "webproject-auto-%d" % n


def collect_metrics(r, docker):
    workers = [k[len("worker:"):] for k in r.scan_iter("worker:*")]
    busy = sum(max(0, int(r.get("busy:" + w) or 0)) for w in workers)
    containers = docker.list_autoscaled()
    running = [c for c in containers if c.get("State") == "running"]
    return {
        "queue_len": r.llen("celery"),
        "workers": len(workers),
        "busy": busy,
        "auto_containers": len(running),
        "auto_alive": len([w for w in workers if w.startswith("auto-")]),
    }, containers


def publish(r, cfg, m, last_action):
    r.setex("autoscaler:state", 30, json.dumps({
        "min": cfg.min_workers, "max": cfg.max_workers,
        "workers": m["workers"], "auto_containers": m["auto_containers"],
        "queue_len": m["queue_len"], "busy": m["busy"],
        "last_action": last_action,
    }))


def main():
    from app import rds
    r = rds.r
    cfg = Config()
    docker = Docker(cfg.socket_path)

    me = docker.inspect(os.environ.get("HOSTNAME", ""))
    image = me["Config"]["Image"]
    env = me["Config"]["Env"]
    network = list(me["NetworkSettings"]["Networks"].keys())[0]
    print("Автоскейлер запущено: мережа=%s, образ=%s, воркерів %d..%d"
          % (network, image, cfg.min_workers, cfg.max_workers), flush=True)

    for c in docker.list_autoscaled():
        docker.remove(c["Id"], grace=10)

    stop = {"flag": False}

    def on_term(signum, frame):
        stop["flag"] = True
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)

    st = {"pressure": 0, "idle_since": None, "last_action": 0.0}
    last_action = "—"

    while not stop["flag"]:
        try:
            m, containers = collect_metrics(r, docker)
            action = decide(m, cfg, st, time.time())
            if action == "up":
                names = {n.lstrip("/") for c in containers for n in c.get("Names", [])}
                num, name = free_name(names)
                docker.create_worker(name, image, env, network, "auto-%d" % num, cfg.concurrency)
                last_action = "запущено %s (%s)" % (name, time.strftime("%H:%M:%S"))
                print(last_action, "| черга:", m["queue_len"], "воркерів:", m["workers"], flush=True)
            elif action == "down":
                running = [c for c in containers if c.get("State") == "running"]
                if running:
                    victim = running[-1]
                    docker.remove(victim["Id"])
                    last_action = "зупинено %s (%s)" % (victim["Names"][0].lstrip("/"), time.strftime("%H:%M:%S"))
                    print(last_action, flush=True)
            publish(r, cfg, m, last_action)
        except Exception as e:
            print("Помилка автоскейлера:", e, flush=True)
        for _ in range(int(cfg.interval * 10)):
            if stop["flag"]:
                break
            time.sleep(0.1)

    print("Зупинка: видаляємо автоматично запущені воркери", flush=True)
    try:
        for c in docker.list_autoscaled():
            docker.remove(c["Id"], grace=10)
        r.delete("autoscaler:state")
    except Exception as e:
        print("Помилка при зупинці:", e, flush=True)


if __name__ == "__main__":
    main()
