"""Supervise meetnote-tray.py et le relance automatiquement s'il se termine
de façon inattendue (ex: access violation native WASAPI, que Python ne peut
pas rattraper depuis l'intérieur du process qui crashe).

Ne dépend que de la stdlib : lancé tôt (avant l'installation des dépendances
par start.bat) et doit rester robuste même si l'environnement Python de
l'appli n'est pas encore prêt.

Sortie propre (Quitter dans le tray, ou stop.bat) : meetnote-tray.py / stop.bat
déposent %APPDATA%/MeetNote/clean_exit.flag avant que le process ne se
termine → le watchdog voit le flag et s'arrête sans relancer.
Sortie sans le flag == crash → relance après un court délai.
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "meetnote-tray.py")

APPDATA_DIR = os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "MeetNote")
STOP_FLAG = os.path.join(APPDATA_DIR, "clean_exit.flag")
WATCHDOG_PID_FILE = os.path.join(APPDATA_DIR, "watchdog.pid")

RESTART_DELAY_S = 3


def _pythonw() -> str:
    exe = sys.executable
    pyw = exe.replace("python.exe", "pythonw.exe")
    return pyw if os.path.isfile(pyw) else exe


def _consume_stop_flag() -> bool:
    if os.path.isfile(STOP_FLAG):
        try:
            os.remove(STOP_FLAG)
        except OSError:
            pass
        return True
    return False


def main():
    os.makedirs(APPDATA_DIR, exist_ok=True)
    with open(WATCHDOG_PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    extra_args = sys.argv[1:]
    try:
        while True:
            _consume_stop_flag()  # ignorer un flag périmé d'un run précédent
            proc = subprocess.Popen([_pythonw(), SCRIPT, *extra_args])
            proc.wait()
            if _consume_stop_flag():
                break  # sortie volontaire : ne pas relancer
            time.sleep(RESTART_DELAY_S)
    finally:
        try:
            os.remove(WATCHDOG_PID_FILE)
        except OSError:
            pass


if __name__ == "__main__":
    main()
