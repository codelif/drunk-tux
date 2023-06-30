from multiprocessing.connection import Client
from multiprocessing.context import AuthenticationError
import getpass
import os
import readline

MAX_RETRIES = 3
address = ("localhost", 6000)

tries = 0
connected = False
while tries <= MAX_RETRIES:
    try:
        pwd = getpass.getpass(prompt="Server Authkey: ")
        conn = Client(address, authkey=bytes(pwd, "ascii"))
        connected = True
        break
    except ConnectionRefusedError:
        print("Server not running")
        break
    except AuthenticationError:
        print("Wrong Password!")
        tries += 1
else:
    print("You have exceeded the number of retries. (%s)" % MAX_RETRIES)
while connected:
    print("> ", end="")
    try:
        cmd = input()
    except KeyboardInterrupt:
        print("^C")
        continue

    if cmd == "exit":
        print("Exiting...")
        connected = False
        conn.send("close connection")
        break
    elif cmd == "clear":
        os.system("clear")
        continue
    elif cmd == "":
        print()
        continue

    conn.send(cmd)
    try:
        print(conn.recv())
    except EOFError:
        pass
