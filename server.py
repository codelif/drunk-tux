from multiprocessing.connection import Listener
from multiprocessing.context import AuthenticationError
from threading import Thread, Event
from configparser import ConfigParser
from datetime import timedelta
import subprocess
import logging
import time
import os
import re


LOGIND_SRV = "systemd-logind.service"
LOGIND_CONF = "/etc/systemd/logind.conf"
LOG_FILE = "/var/log/drunk-tux.log"
PASSWORD = b"secret"


if os.geteuid() != 0:
    raise PermissionError("Root Privileges Required!")


log_formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

file_handler = logging.FileHandler(LOG_FILE)
file_handler.setFormatter(log_formatter)
logger.addHandler(file_handler)

stdout_handler = logging.StreamHandler()
stdout_handler.setFormatter(log_formatter)
logger.addHandler(stdout_handler)


class Parser(ConfigParser):
    def optionxform(self, optionstr):
        return optionstr


def convert_to_seconds(s):
    UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
    return int(
        timedelta(
            **{
                UNITS.get(m.group("unit").lower(), "seconds"): float(m.group("val"))
                for m in re.finditer(
                    r"(?P<val>\d+(\.\d+)?)(?P<unit>[smhdw]?)", s, flags=re.I
                )
            }
        ).total_seconds()
    )


def prettify_seconds(sec):
    t = []
    for d, u in [(86400, "day"), (3600, "hour"), (60, "minute"), (1, "second")]:
        n, sec = divmod(sec, d)
        if n:
            t.append(f"{n} {u}" + "s" * (n > 1))

    return ", ".join(t)


def get_parser():
    conf = Parser()
    conf.read(LOGIND_CONF)
    return conf


def get_mode(conf: Parser):
    mode = conf["Login"].get("HandleLidSwitch")

    if not mode:
        mode = "suspend"

    return mode


def toggle_lid_suspend():
    conf = get_parser()
    current = get_mode(conf)

    new = "ignore" if current != "ignore" else "suspend"

    set_mode(conf, new)
    return new


def set_mode(conf: Parser, mode: str):
    conf["Login"]["HandleLidSwitch"] = mode

    logger.info("Writing to file '%s'" % LOGIND_CONF)
    with open(LOGIND_CONF, "w") as configfile:
        conf.write(configfile)

    logger.info("Restarting Service '%s'" % LOGIND_SRV)
    subprocess.run(["systemctl", "kill", "-s", "HUP", LOGIND_SRV])


def caffeine(event: Event, time_seconds: float, gil_lock: Event):
    conf = get_parser()
    set_mode(conf, "ignore")
    
    gil_lock.set()
    event.wait(time_seconds)

    conf = get_parser()  # Just in case, it has been modified.
    set_mode(conf, "suspend")


def clean_thread(running_list: list[dict]):
    new_list = []
    for thread in running_list:
        if thread["thread"].is_alive():
            new_list.append(thread)

    return new_list


listener = Listener(("localhost", 6000), authkey=PASSWORD)
running = True

logger.info("Server Started")

running_threads = []

while running:
    try:
        conn = listener.accept()
    except AuthenticationError:
        logger.warning("Wrong Password attempted")
        continue
    except KeyboardInterrupt:
        logger.warning("Closing Server... (interrupt)")
        running = False
        break

    logger.info(f"Connection accepted from {listener.last_accepted}")
    while True:
        try:
            msg = conn.recv()
            logger.info("Command issued '%s'" % msg)
        except EOFError:
            msg = "close connection"
        except KeyboardInterrupt:
            logger.warning("Closing Server... (interrupt)")
            conn.close()
            logger.warning("Closed all connections!")
            running = False
            break

        if msg == "close connection":
            conn.close()
            logger.info(f"Closed Connection from {listener.last_accepted}")
            break
        elif msg == "close server":
            logger.info("Closing Server...")
            conn.close()
            logger.warning("Closed all connections!")
            running = False
            break
        elif msg == "toggle":
            new = toggle_lid_suspend()
            conn.send(new)

        elif msg == "current":
            conf = get_parser()
            conn.send(get_mode(conf))

        elif msg.startswith("caffeine "):
            running_threads = clean_thread(running_threads)

            if len(running_threads) == 1:
                conn.send("Already running")
                continue

            duration = convert_to_seconds(msg)
            if duration > 86400:
                conn.send("Duration cannot be greater than 1 day")
                continue
            gil_lock = Event()
            event = Event()
            t = Thread(target=caffeine, args=(event, duration, gil_lock))
            t.start()
            running_threads.append(
                {"thread": t, "event": event, "time": time.time(), "duration": duration}
            )
            gil_lock.wait(1)

            conn.send("Drinking Coffee for %s" % prettify_seconds(duration))

        elif msg == "coffee":
            running_threads = clean_thread(running_threads)

            if len(running_threads) == 0:
                conn.send("Tux is sober.")
                continue

            remaining = int(
                running_threads[0]["duration"]
                - (time.time() - running_threads[0]["time"])
            )

            conn.send("%s remaining" % prettify_seconds(remaining))
        elif msg == "spill":
            running_threads = clean_thread(running_threads)

            if len(running_threads) == 0:
                conn.send("Tux isn't drinking any coffee.")
                continue

            running_threads[0]["event"].set()
            running_threads[0]["thread"].join()
            conn.send("Spilled the coffee.")
        else:
            conn.send("The server does not recogize the command '%s'" % msg)


logger.warning("Stopping all Caffeine Sessions!")
running_threads = clean_thread(running_threads)
for thread in running_threads:
    thread["event"].set()
    thread["thread"].join()


listener.close()
logger.info("Server Closed")
